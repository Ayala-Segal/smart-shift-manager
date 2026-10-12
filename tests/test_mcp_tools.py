import json
import os
import subprocess
import sys
import unittest
from datetime import date

from app import create_app
from app.commands.define_shift import define_shift
from app.event_store import (
    ASSIGNMENT_APPROVED,
    NOTIFICATION_FAILED,
    NOTIFICATION_SENT,
    SHIFT_CANCELLED,
    Event,
    append_event,
)
from app.extensions import db
from app.mcp.protocol import handle_message
from app.mcp.stdio import read_message, write_message
from app.notifications.dispatch import dispatch_notifications
from app.projections import project_shift_board
from app.tools.conflicts import check_availability_conflicts
from app.tools.hours import calculate_weekly_hours
from app.tools.tavily import search_date_context


class McpToolTests(unittest.TestCase):
    def setUp(self):
        self._saved_connection = os.environ.pop("SOMEE_SQLSERVER_CONNECTION_STRING", None)
        self._saved_tavily = os.environ.pop("MCP_TAVILY_API_KEY", None)
        self._saved_gmail_command = os.environ.pop("MCP_GMAIL_COMMAND", None)
        self._saved_gmail_credentials = os.environ.pop("MCP_GMAIL_CREDENTIALS", None)
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self._restore("SOMEE_SQLSERVER_CONNECTION_STRING", self._saved_connection)
        self._restore("MCP_TAVILY_API_KEY", self._saved_tavily)
        self._restore("MCP_GMAIL_COMMAND", self._saved_gmail_command)
        self._restore("MCP_GMAIL_CREDENTIALS", self._saved_gmail_credentials)

    def test_weekly_hours_warn_above_42_and_do_not_write_events(self):
        with self.app.app_context():
            self._approve("employee-1", "shift-1", "09:00", "17:00")
            before = db.session.query(Event).count()
            hours = calculate_weekly_hours("employee-1", "2026-10-18", 36)
            self.assertEqual(db.session.query(Event).count(), before)
        self.assertTrue(hours["ok"])
        self.assertEqual(hours["result"]["approved_hours"], 8)
        self.assertEqual(hours["result"]["projected_hours"], 44)
        self.assertEqual(hours["result"]["weekly_limit"], 42)
        self.assertTrue(hours["result"]["exceeds_limit"])
        self.assertEqual(hours["result"]["overflow_hours"], 2)
        with self.app.app_context():
            exact = calculate_weekly_hours("employee-1", "2026-10-18", 34)
            invalid = calculate_weekly_hours("employee-1", "2026-10-19", 1)
        self.assertFalse(exact["result"]["exceeds_limit"])
        self.assertEqual(invalid["error_code"], "invalid_week_start")

    def test_conflicts_cover_unavailable_overlap_rest_and_prefer_not(self):
        with self.app.app_context():
            self._constraint("employee-1", "unavailable")
            blocked = check_availability_conflicts("employee-1", "2026-10-19", "09:00", "17:00")
            self._constraint("employee-2", "prefer not")
            soft = check_availability_conflicts("employee-2", "2026-10-19", "09:00", "17:00")
            self._constraint("employee-3", "prefer")
            clear = check_availability_conflicts("employee-3", "2026-10-19", "09:00", "17:00")
            self._approve("employee-4", "shift-4", "09:00", "17:00")
            overlap = check_availability_conflicts("employee-4", "2026-10-19", "16:00", "20:00")
            rest = check_availability_conflicts("employee-4", "2026-10-20", "00:30", "08:00")
            enough = check_availability_conflicts("employee-4", "2026-10-20", "01:00", "09:00")
        self.assertTrue(blocked["result"]["hard_block"])
        self.assertIn("unavailable", blocked["result"]["reasons"][0])
        self.assertTrue(soft["result"]["soft_block"])
        self.assertFalse(soft["result"]["hard_block"])
        self.assertFalse(clear["result"]["hard_block"])
        self.assertFalse(clear["result"]["soft_block"])
        self.assertEqual(overlap["result"]["conflicting_shift_id"], "shift-4")
        self.assertTrue(rest["result"]["hard_block"])
        self.assertNotIn("conflicting_shift_id", rest["result"])
        self.assertFalse(enough["result"]["hard_block"])

    def test_cancelling_a_shift_removes_its_hours(self):
        with self.app.app_context():
            self._approve("employee-1", "shift-1", "09:00", "17:00")
            append_event(
                aggregate_type="Shift",
                aggregate_id="shift-1",
                event_type=SHIFT_CANCELLED,
                payload={},
                actor_id="manager-1",
            )
            project_shift_board()
            hours = calculate_weekly_hours("employee-1", "2026-10-18", 0)
            conflict = check_availability_conflicts("employee-1", "2026-10-19", "09:00", "17:00")
        self.assertEqual(hours["result"]["approved_hours"], 0)
        self.assertFalse(conflict["result"]["hard_block"])

    def test_tavily_reports_a_holiday_and_gmail_records_one_notice(self):
        found = search_date_context(
            "2026-10-19",
            search=lambda query: {
                "answer": "",
                "results": [
                    {
                        "title": "Holiday eve",
                        "url": "https://example.com/holiday",
                        "content": "A public holiday",
                    }
                ],
            },
        )
        self.assertTrue(found["result"]["found"])
        self.assertEqual(found["result"]["kind"], "holiday")
        self.assertEqual(found["result"]["source"], "https://example.com/holiday")
        self.assertEqual(search_date_context("2026-10-19")["error_code"], "not_configured")

        registered = self.client.post(
            "/auth/register",
            json={
                "full_name": "Ayala Levi",
                "email": "ayala@example.com",
                "password": "password1",
                "role": "employee",
                "professional_role": "cashier",
            },
        )
        employee_id = registered.get_json()["user_id"]
        with self.app.app_context():
            decision = append_event(
                aggregate_type="AssignmentRequest",
                aggregate_id="request-1",
                event_type=ASSIGNMENT_APPROVED,
                payload={
                    "employee_id": employee_id,
                    "shift_id": "shift-1",
                    "shift_date": "2026-10-19",
                    "start_time": "09:00",
                    "end_time": "17:00",
                },
                actor_id="manager-1",
            )
            failed = dispatch_notifications()
            self.assertEqual(failed, 0)
            failure = db.session.query(Event).filter_by(event_type=NOTIFICATION_FAILED).one()
            self.assertEqual(json.loads(failure.payload_json)["error_code"], "not_configured")
            self.assertEqual(decision.event_type, ASSIGNMENT_APPROVED)
            retried = dispatch_notifications(transport=lambda **kwargs: {"ok": True})
            self.assertEqual(retried, 0)
            append_event(
                aggregate_type="AssignmentRequest",
                aggregate_id="request-2",
                event_type=ASSIGNMENT_APPROVED,
                payload={
                    "employee_id": employee_id,
                    "shift_id": "shift-2",
                    "shift_date": "2026-10-20",
                    "start_time": "09:00",
                    "end_time": "13:00",
                },
                actor_id="manager-1",
            )
            sent = dispatch_notifications(transport=lambda **kwargs: {"ok": True})
            self.assertEqual(sent, 1)
            notice = db.session.query(Event).filter_by(event_type=NOTIFICATION_SENT).one()
            self.assertNotIn("body", json.loads(notice.payload_json))
            again = dispatch_notifications(transport=lambda **kwargs: {"ok": True})
            self.assertEqual(again, 0)

    def test_mcp_server_exposes_both_tools(self):
        with self.app.app_context():
            listed = handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, self.app)
            called = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "calculate_weekly_hours",
                        "arguments": {
                            "employee_id": "employee-1",
                            "week_start": "2026-10-18",
                            "additional_shift_hours": 4,
                        },
                    },
                },
                self.app,
            )
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertEqual(
            names,
            ["calculate_weekly_hours", "check_availability_conflicts"],
        )
        body = json.loads(called["result"]["content"][0]["text"])
        self.assertTrue(body["ok"])
        self.assertEqual(body["result"]["projected_hours"], 4)
        self._assert_stdio_lists_tools()

    def _approve(self, employee_id, shift_id, start_time, end_time):
        define_shift(
            actor_id="manager-1",
            actor_role="manager",
            shift_date="2026-10-19",
            start_time=start_time,
            end_time=end_time,
            required_role="cashier",
            required_headcount=1,
            today=date(2026, 10, 12),
        )
        append_event(
            aggregate_type="AssignmentRequest",
            aggregate_id="approved-" + shift_id,
            event_type=ASSIGNMENT_APPROVED,
            payload={
                "employee_id": employee_id,
                "shift_id": shift_id,
                "shift_date": "2026-10-19",
                "start_time": start_time,
                "end_time": end_time,
            },
            actor_id="manager-1",
        )
        project_shift_board()

    def _constraint(self, employee_id, constraint_type):
        append_event(
            aggregate_type="EmployeeAvailability",
            aggregate_id="constraint-" + employee_id,
            event_type="AvailabilityConstraintSubmitted",
            payload={
                "employee_id": employee_id,
                "start_date": "2026-10-19",
                "end_date": "2026-10-19",
                "constraint_type": constraint_type,
                "note": "",
            },
            actor_id=employee_id,
        )
        project_shift_board()

    def _assert_stdio_lists_tools(self):
        env = os.environ.copy()
        env.pop("SOMEE_SQLSERVER_CONNECTION_STRING", None)
        process = subprocess.Popen(
            [sys.executable, "mcp_server.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            write_message(
                process.stdin,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0.0"},
                    },
                },
            )
            initialized = read_message(process.stdout)
            write_message(process.stdin, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            listed = read_message(process.stdout)
        finally:
            process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "smart-shift-tools")
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertIn("check_availability_conflicts", names)

    def _restore(self, name, value):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
