import os
import unittest
from datetime import date

from app import create_app
from app.agent.cycle import run_evaluation_cycle
from app.commands.define_shift import define_shift
from app.event_store import (
    AGENT_EVALUATION_COMPLETED,
    ASSIGNMENT_APPROVED,
    ASSIGNMENT_REQUESTED,
    Event,
    append_event,
)
from app.extensions import db
from app.projections import project_shift_board
from app.read_models import EmployeeHoursRow, RequestRecommendationRow
from app.retrieval.store import RetrievalUnavailable
from app.tools.contract import error, ok
from app.tools.schedule import week_start_sunday


class AgentEvaluationTests(unittest.TestCase):
    def setUp(self):
        self._saved_connection = os.environ.pop("SOMEE_SQLSERVER_CONNECTION_STRING", None)
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            event = define_shift(
                actor_id="manager-1",
                actor_role="manager",
                shift_date="2026-10-19",
                start_time="09:00",
                end_time="17:00",
                required_role="cashier",
                required_headcount=2,
                today=date(2026, 10, 12),
            )
            project_shift_board()
            self.shift_id = event.aggregate_id
        self.employee_id = self._register("Ayala Levi", "ayala@example.com", "cashier")
        self._register("Noa Bar", "noa@example.com", "shift lead", role="manager")

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        if self._saved_connection is not None:
            os.environ["SOMEE_SQLSERVER_CONNECTION_STRING"] = self._saved_connection

    def test_clear_request_is_approved_once(self):
        self._request(self.employee_id, "cashier")
        first = self._cycle()
        second = self._cycle()
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        event_id = self._recommendation()
        payload = self._payload(event_id)
        self.assertEqual(payload["decision"], "approve")
        self.assertIn("Approved hours 0", payload["rationale"])
        self.assertIn("projected hours 8", payload["rationale"])
        with self.app.app_context():
            event = db.session.get(Event, event_id)
            self.assertEqual(event.actor_id, "agent")
        self.assertFalse(self._has(ASSIGNMENT_APPROVED))
        self._login("noa@example.com")
        detail = self.client.get("/queries/shifts/" + self.shift_id).get_json()
        registered = detail["registered"][0]
        self.assertEqual(registered["recommendation"], "approve")
        self.assertIn("rationale", registered)

    def test_overlap_is_rejected_and_hours_over_42_warn(self):
        self._approve_existing("09:00", "17:00")
        self._request(self.employee_id, "cashier", start="16:00", end="20:00", shift_date="2026-10-19")
        self._cycle()
        rejected = self._payload(self._recommendation())
        self.assertEqual(rejected["decision"], "reject")
        self.assertIn("overlaps", rejected["rationale"].lower())

        with self.app.app_context():
            week = week_start_sunday(date(2026, 10, 21))
            row = db.session.get(EmployeeHoursRow, (self.employee_id, week))
            if row is None:
                row = EmployeeHoursRow(
                    employee_id=self.employee_id,
                    week_start=week,
                    approved_hours=0,
                )
                db.session.add(row)
            row.approved_hours = 40
            db.session.commit()
        self._request(self.employee_id, "cashier", shift_date="2026-10-21", start="09:00", end="17:00")
        self._cycle()
        warned = self._payload(self._latest_recommendation())
        self.assertEqual(warned["decision"], "warning")
        self.assertTrue(warned["hours_warning"])
        self.assertIn("42", warned["rationale"])

    def test_role_mismatch_is_rejected(self):
        self._request(self.employee_id, "nurse")
        self._cycle()
        payload = self._payload(self._recommendation())
        self.assertEqual(payload["decision"], "reject")
        self.assertIn("role does not match", payload["rationale"])

    def test_retrieval_failure_warns_without_blocking_the_tools(self):
        self._request(self.employee_id, "cashier")

        def fail_retrieve(*args, **kwargs):
            raise RetrievalUnavailable("down")

        self._cycle(retrieve=fail_retrieve)
        payload = self._payload(self._recommendation())
        self.assertEqual(payload["decision"], "warning")
        self.assertIn("Evaluation without semantic context", payload["rationale"])
        self.assertIn("Approved hours", payload["rationale"])

    def test_holiday_warns_and_a_missing_search_is_recorded(self):
        self._request(self.employee_id, "cashier")
        self._cycle(
            search=lambda shift_date: ok(
                {"found": True, "title": "Purim", "source": "https://example.test", "kind": "holiday"}
            )
        )
        holiday = self._payload(self._recommendation())
        self.assertEqual(holiday["decision"], "warning")
        self.assertIn("Purim", holiday["rationale"])

        self._request(self.employee_id, "cashier", shift_date="2026-10-22")
        self._cycle(search=lambda shift_date: error("tavily_unavailable"))
        missing = self._payload(self._latest_recommendation())
        self.assertEqual(missing["decision"], "approve")
        self.assertIn("Holiday data was unavailable.", missing["rationale"])

    def test_a_failed_hours_tool_leaves_the_request_for_the_next_cycle(self):
        self._request(self.employee_id, "cashier")
        evaluated = self._cycle(weekly_hours=lambda *args, **kwargs: error("invalid_hours"))
        self.assertEqual(evaluated, 0)
        self.assertIsNone(self._recommendation())
        recovered = self._cycle()
        self.assertEqual(recovered, 1)

    def _cycle(self, **kwargs):
        options = {
            "search": lambda shift_date: ok({"found": False, "title": "", "source": "", "kind": ""}),
            "retrieve": lambda *args, **kwargs: [],
        }
        options.update(kwargs)
        return run_evaluation_cycle(self.app, **options)

    def _request(self, employee_id, role, shift_date="2026-10-19", start="09:00", end="17:00"):
        with self.app.app_context():
            if shift_date != "2026-10-19" or start != "09:00":
                shift = define_shift(
                    actor_id="manager-1",
                    actor_role="manager",
                    shift_date=shift_date,
                    start_time=start,
                    end_time=end,
                    required_role="cashier",
                    required_headcount=1,
                    today=date(2026, 10, 12),
                )
                shift_id = shift.aggregate_id
                project_shift_board()
            else:
                shift_id = self.shift_id
            append_event(
                aggregate_type="AssignmentRequest",
                aggregate_id="request-" + shift_id,
                event_type=ASSIGNMENT_REQUESTED,
                payload={
                    "shift_id": shift_id,
                    "employee_id": employee_id,
                    "full_name": "Ayala Levi",
                    "professional_role": role,
                    "note": "",
                    "status": "Waiting for agent evaluation",
                },
                actor_id=employee_id,
            )

    def _approve_existing(self, start, end):
        with self.app.app_context():
            append_event(
                aggregate_type="AssignmentRequest",
                aggregate_id="approved-existing",
                event_type=ASSIGNMENT_APPROVED,
                payload={
                    "employee_id": self.employee_id,
                    "shift_id": self.shift_id,
                    "shift_date": "2026-10-19",
                    "start_time": start,
                    "end_time": end,
                },
                actor_id="manager-1",
            )
            project_shift_board()

    def _recommendation(self, latest=False):
        with self.app.app_context():
            query = db.session.query(Event).filter(Event.event_type == AGENT_EVALUATION_COMPLETED)
            if latest:
                query = query.order_by(Event.event_id.desc())
            else:
                query = query.order_by(Event.event_id)
            event = query.first()
            return None if event is None else event.event_id

    def _latest_recommendation(self):
        return self._recommendation(latest=True)

    def _payload(self, event_id):
        import json

        with self.app.app_context():
            stored = db.session.get(Event, event_id)
            payload = json.loads(stored.payload_json)
            saved = db.session.get(RequestRecommendationRow, stored.aggregate_id)
            self.assertIsNotNone(saved)
            self.assertEqual(saved.decision, payload["decision"])
            return payload

    def _has(self, event_type):
        with self.app.app_context():
            return (
                db.session.query(Event).filter(Event.event_type == event_type).first() is not None
            )

    def _register(self, full_name, email, professional_role, role="employee"):
        response = self.client.post(
            "/auth/register",
            json={
                "full_name": full_name,
                "email": email,
                "password": "password1",
                "role": role,
                "professional_role": professional_role,
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["user_id"]

    def _login(self, email):
        response = self.client.post(
            "/auth/login",
            json={"email": email, "password": "password1"},
        )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
