import os
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import create_app
from app.commands.decide_request import decide_request
from app.commands.define_shift import define_shift
from app.event_store import (
    AGENT_EVALUATION_COMPLETED,
    AGGREGATE_ASSIGNMENT_REQUEST,
    AGGREGATE_SWAP_REQUEST,
    ASSIGNMENT_APPROVED,
    ASSIGNMENT_REJECTED,
    NOTIFICATION_FAILED,
    Event,
    append_event,
)
from app.extensions import db
from app.projections import project_shift_board
from app.read_models import ApprovedAssignmentRow, EmployeeHoursRow, ShiftBoardRow, ShiftRequestRow
from app.tools.schedule import week_start_sunday

ISRAEL = ZoneInfo("Asia/Jerusalem")


class Stage7ScreenTests(unittest.TestCase):
    def setUp(self):
        self._saved_connection = os.environ.pop("SOMEE_SQLSERVER_CONNECTION_STRING", None)
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.today = date(2026, 10, 12)
        with self.app.app_context():
            db.create_all()
            event = define_shift(
                actor_id="manager-seed",
                actor_role="manager",
                shift_date="2026-10-14",
                start_time="09:00",
                end_time="17:00",
                required_role="cashier",
                required_headcount=1,
                today=self.today,
            )
            self.later = define_shift(
                actor_id="manager-seed",
                actor_role="manager",
                shift_date="2026-10-14",
                start_time="16:00",
                end_time="20:00",
                required_role="cashier",
                required_headcount=1,
                today=self.today,
            ).aggregate_id
            project_shift_board()
            self.shift_id = event.aggregate_id
        self.manager = self._register("Noa Bar", "noa@example.com", "manager", "shift lead")
        self.employee = self._register("Ayala Levi", "ayala@example.com", "employee", "cashier")
        self.replacement = self._register("Yael Cohen", "yael@example.com", "employee", "cashier")

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        if self._saved_connection is not None:
            os.environ["SOMEE_SQLSERVER_CONNECTION_STRING"] = self._saved_connection

    def test_manager_can_export_the_shift_report(self):
        self._login("noa@example.com")
        page = self.client.get("/board")
        self.assertIn("Export report", page.get_data(as_text=True))
        exported = self.client.get("/board/export")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.mimetype, "text/csv")
        self.assertIn("shift-report.csv", exported.headers["Content-Disposition"])
        text = exported.get_data(as_text=True)
        self.assertIn("assigned_count", text)
        self.assertIn("waiting_candidates", text)
        self.assertIn("2026-10-14", text)
        self.assertIn("open", text)
        self.client.post("/sign-out")
        self._login("ayala@example.com")
        own_role = self.client.get("/board/export?include_closed=1")
        self.assertNotIn("shift lead", own_role.get_data(as_text=True))
        self.assertIn("cashier", own_role.get_data(as_text=True))

    def test_approval_updates_the_board_only_after_projection(self):
        request_id = self._assignment(self.employee["user_id"])
        self._login("noa@example.com")
        early = self.client.post(
            "/commands/assignments/" + request_id + "/decision",
            json={"action": "approve"},
        )
        self.assertEqual(early.status_code, 400)
        self.assertIn("recommendation", early.get_json()["error"])
        self.assertFalse(self._has(ASSIGNMENT_APPROVED))

        self._recommend(request_id, self.shift_id, self.employee["user_id"])
        with self.app.app_context():
            decide_request(
                actor_id=self.manager["user_id"],
                actor_role="manager",
                request_id=request_id,
                action="approve",
            )
            row = db.session.get(ShiftBoardRow, self.shift_id)
            self.assertEqual(row.assigned_count, 0)
            project_shift_board()
            db.session.expire_all()
            row = db.session.get(ShiftBoardRow, self.shift_id)
            self.assertEqual(row.assigned_count, 1)
            self.assertEqual(row.status, "staffed")
            self.assertEqual(row.waiting_candidates, 0)

        overlap_id = self._assignment(self.employee["user_id"], self.later)
        self._recommend(overlap_id, self.later, self.employee["user_id"])
        self._login("noa@example.com")
        blocked = self.client.post(
            "/commands/assignments/" + overlap_id + "/decision",
            json={"action": "approve"},
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("overlaps", blocked.get_json()["error"])
        with self.app.app_context():
            later = db.session.get(ShiftBoardRow, self.later)
            self.assertEqual(later.assigned_count, 0)

    def test_rejection_keeps_the_note_when_mail_is_not_configured(self):
        request_id = self._assignment(self.employee["user_id"])
        self._recommend(request_id, self.shift_id, self.employee["user_id"], decision="reject")
        self._login("noa@example.com")
        missing = self.client.post(
            "/commands/assignments/" + request_id + "/decision",
            json={"action": "reject"},
        )
        self.assertEqual(missing.status_code, 400)
        decided = self.client.post(
            "/commands/assignments/" + request_id + "/decision",
            json={"action": "reject", "note": "Covered by another cashier"},
        )
        self.assertEqual(decided.status_code, 201)
        self.assertEqual(decided.get_json()["status"], "Rejected")
        with self.app.app_context():
            row = db.session.get(ShiftRequestRow, request_id)
            self.assertEqual(row.status, "Rejected")
            self.assertEqual(row.decision_note, "Covered by another cashier")
            self.assertEqual(db.session.get(ShiftBoardRow, self.shift_id).assigned_count, 0)
            self.assertTrue(self._has(ASSIGNMENT_REJECTED))
            self.assertTrue(self._has(NOTIFICATION_FAILED))
        self._login("ayala@example.com")
        mine = self.client.get("/queries/my-dashboard").get_json()
        self.assertEqual(mine["decided_requests"][0]["status"], "Rejected")
        self.assertEqual(mine["decided_requests"][0]["decision_note"], "Covered by another cashier")

    def test_dashboard_counts_open_partial_staffed_and_refuses_an_employee(self):
        request_id = self._assignment(self.employee["user_id"])
        self._login("noa@example.com")
        before = self.client.get("/queries/manager-dashboard?start=2026-10-12&end=2026-10-18")
        body = before.get_json()
        self.assertEqual(body["user_id"], self.manager["user_id"])
        self.assertEqual(body["open_shifts"], 2)
        self.assertEqual(body["partial_shifts"], 0)
        self.assertEqual(body["staffed_shifts"], 0)
        self.assertEqual(body["waiting_for_agent"], 1)
        self.assertEqual(body["unstaffed"][0]["waiting_candidates"], 1)

        self._recommend(request_id, self.shift_id, self.employee["user_id"])
        after = self.client.get("/queries/manager-dashboard?start=2026-10-12&end=2026-10-18").get_json()
        self.assertEqual(after["waiting_for_agent"], 0)
        self.assertEqual(after["waiting_for_manager"], 1)

        page = self.client.get("/decisions")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Eligible for this shift.", page.get_data(as_text=True))
        self.assertIn("Approve", page.get_data(as_text=True))

        self._login("ayala@example.com")
        refused = self.client.get("/decisions")
        self.assertEqual(refused.status_code, 403)
        self.assertIn("You cannot open this page.", refused.get_data(as_text=True))

    def test_swap_approval_replaces_the_assignment(self):
        request_id = self._assignment(self.employee["user_id"])
        self._recommend(request_id, self.shift_id, self.employee["user_id"])
        self._login("noa@example.com")
        approved = self.client.post(
            "/commands/assignments/" + request_id + "/decision",
            json={"action": "approve"},
        )
        self.assertEqual(approved.status_code, 201)
        self._login("ayala@example.com")
        swap = self.client.post(
            "/commands/swaps",
            json={
                "shift_id": self.shift_id,
                "replacement_employee_id": self.replacement["user_id"],
                "note": "please cover",
            },
        )
        self.assertEqual(swap.status_code, 201, swap.get_json())
        swap_id = swap.get_json()["request_id"]
        self._login("yael@example.com")
        accepted = self.client.post(
            "/commands/swaps/" + swap_id + "/colleague",
            json={"action": "approve"},
        )
        self.assertEqual(accepted.status_code, 201, accepted.get_json())
        self._recommend(
            swap_id,
            self.shift_id,
            self.employee["user_id"],
            request_type="swap",
        )
        self._login("noa@example.com")
        decided = self.client.post(
            "/commands/swaps/" + swap_id + "/decision",
            json={"action": "approve", "note": "Agreed"},
        )
        self.assertEqual(decided.status_code, 201, decided.get_json())
        with self.app.app_context():
            board = db.session.get(ShiftBoardRow, self.shift_id)
            self.assertEqual(board.assigned_count, 1)
            self.assertEqual(board.status, "staffed")
            original = db.session.get(ApprovedAssignmentRow, request_id)
            replacement = db.session.get(ApprovedAssignmentRow, swap_id)
            self.assertFalse(original.active)
            self.assertTrue(replacement.active)
            self.assertEqual(replacement.employee_id, self.replacement["user_id"])
            week = week_start_sunday(date(2026, 10, 14))
            original_hours = db.session.get(EmployeeHoursRow, (self.employee["user_id"], week))
            replacement_hours = db.session.get(EmployeeHoursRow, (self.replacement["user_id"], week))
            self.assertIsNone(original_hours)
            self.assertEqual(replacement_hours.approved_hours, 8)

    def test_screens_cover_sign_in_board_define_and_constraints(self):
        sign_in = self.client.get("/sign-in")
        self.assertEqual(sign_in.status_code, 200)
        self.assertIn("Email", sign_in.get_data(as_text=True))

        created = self.client.post(
            "/register",
            data={
                "full_name": "Dana Levi",
                "email": "dana@example.com",
                "password": "password1",
                "role": "manager",
                "professional_role": "shift lead",
            },
            follow_redirects=True,
        )
        self.assertIn("Account created. Sign in to continue.", created.get_data(as_text=True))
        home = self.client.post(
            "/sign-in",
            data={"email": "dana@example.com", "password": "password1"},
            follow_redirects=True,
        )
        page = home.get_data(as_text=True)
        self.assertIn("Open shifts", page)
        self.assertIn("Partial shifts", page)
        self.assertIn("Staffed shifts", page)

        shift_day = (datetime.now(ISRAEL).date() + timedelta(days=3)).isoformat()
        defined = self.client.post(
            "/shifts/new",
            data={
                "shift_date": shift_day,
                "start_time": "09:00",
                "end_time": "17:00",
                "required_role": "cashier",
                "required_headcount": "1",
                "note": "front counter",
            },
            follow_redirects=True,
        )
        self.assertIn("front counter", defined.get_data(as_text=True))
        self.assertIn("open", defined.get_data(as_text=True))

        empty = self.client.get("/board?start=2020-01-01&end=2020-01-02")
        empty_page = empty.get_data(as_text=True)
        self.assertIn("No shifts in the selected range", empty_page)
        self.assertIn("Widen the range", empty_page)
        self.assertIn("Define a shift", empty_page)

        self.client.post("/sign-out")
        self.client.post(
            "/register",
            data={
                "full_name": "Roni Paz",
                "email": "roni@example.com",
                "password": "password1",
                "role": "employee",
                "professional_role": "cashier",
            },
        )
        self.client.post(
            "/sign-in",
            data={"email": "roni@example.com", "password": "password1"},
        )
        saved = self.client.post(
            "/constraints",
            data={"date": shift_day, "constraint_type": "prefer", "note": "morning"},
            follow_redirects=True,
        )
        self.assertIn("prefer", saved.get_data(as_text=True))
        self.assertIn("morning", saved.get_data(as_text=True))
        search = self.client.get("/search?mode=week&date=" + shift_day)
        self.assertIn(shift_day, search.get_data(as_text=True))
        self.assertNotIn("shift lead", search.get_data(as_text=True))

    def test_past_shift_is_closed_and_the_manager_can_open_the_roster(self):
        with self.app.app_context():
            past = define_shift(
                actor_id="manager-seed",
                actor_role="manager",
                shift_date="2020-01-02",
                start_time="09:00",
                end_time="17:00",
                required_role="cashier",
                required_headcount=1,
                today=date(2019, 12, 1),
            )
            project_shift_board()
            past_id = past.aggregate_id
        self._login("ayala@example.com")
        refused = self.client.post(
            "/commands/assignments",
            json={"shift_id": past_id, "note": "too late"},
        )
        self.assertEqual(refused.status_code, 400)
        self.assertIn("current or future", refused.get_json()["error"])
        card = self.client.get("/shifts/" + past_id)
        self.assertIn("registration is closed", card.get_data(as_text=True))
        self.assertNotIn("Submit request", card.get_data(as_text=True))
        search = self.client.get("/search?start=2020-01-01&end=2020-01-03")
        page = search.get_data(as_text=True)
        self.assertIn("Past shifts", page)
        self.assertIn("2020-01-02", page)

        self._login("noa@example.com")
        roster = self.client.get("/employees")
        text = roster.get_data(as_text=True)
        self.assertEqual(roster.status_code, 200)
        self.assertIn("Ayala Levi", text)
        self.assertIn("ayala@example.com", text)
        self.assertIn("cashier", text)
        self._login("ayala@example.com")
        self.assertEqual(self.client.get("/employees").status_code, 403)

    def test_screen_request_reaches_the_manager_after_the_agent_runs(self):
        self._login("ayala@example.com")
        submitted = self.client.post(
            "/shifts/" + self.shift_id + "/request",
            data={"note": "I can cover"},
            follow_redirects=True,
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertIn("Request #0", submitted.get_data(as_text=True))
        self._login("noa@example.com")
        page = self.client.get("/decisions")
        text = page.get_data(as_text=True)
        self.assertIn("Agent recommendation", text)
        self.assertIn("Approve", text)
        self.assertNotIn("No requests are waiting for a decision.", text)

    def _assignment(self, employee_id, shift_id=None):
        self._login_employee(employee_id)
        response = self.client.post(
            "/commands/assignments",
            json={"shift_id": shift_id or self.shift_id, "note": "I can cover"},
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        self.client.post("/auth/logout")
        return response.get_json()["request_id"]

    def _login_employee(self, employee_id):
        email = {
            self.employee["user_id"]: "ayala@example.com",
            self.replacement["user_id"]: "yael@example.com",
        }[employee_id]
        self._login(email)

    def _recommend(self, request_id, shift_id, employee_id, request_type="assignment", decision="approve"):
        aggregate = AGGREGATE_ASSIGNMENT_REQUEST if request_type == "assignment" else AGGREGATE_SWAP_REQUEST
        with self.app.app_context():
            append_event(
                aggregate_type=aggregate,
                aggregate_id=request_id,
                event_type=AGENT_EVALUATION_COMPLETED,
                payload={
                    "shift_id": shift_id,
                    "employee_id": employee_id,
                    "request_type": request_type,
                    "decision": decision,
                    "rationale": "Eligible for this shift.",
                    "hours_warning": False,
                },
                actor_id="agent",
            )
            project_shift_board()

    def _register(self, full_name, email, role, professional_role):
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
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def _login(self, email):
        response = self.client.post("/auth/login", json={"email": email, "password": "password1"})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response

    def _has(self, event_type):
        with self.app.app_context():
            return db.session.query(Event).filter(Event.event_type == event_type).first() is not None
