import os
import unittest
from datetime import date

from app import create_app
from app.commands.define_shift import define_shift
from app.extensions import db
from app.projections import project_shift_board


class UserFlowTests(unittest.TestCase):
    def setUp(self):
        self._saved_connection = os.environ.pop("SOMEE_SQLSERVER_CONNECTION_STRING", None)
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            event = define_shift(
                actor_id="manager-seed",
                actor_role="manager",
                shift_date="2026-10-19",
                start_time="09:00",
                end_time="17:00",
                required_role="cashier",
                required_headcount=2,
                note="front desk",
                today=date(2026, 10, 12),
            )
            define_shift(
                actor_id="manager-seed",
                actor_role="manager",
                shift_date="2026-10-20",
                start_time="09:00",
                end_time="17:00",
                required_role="shift lead",
                required_headcount=1,
                today=date(2026, 10, 12),
            )
            project_shift_board()
            self.shift_id = event.aggregate_id
        self._register("Noa Bar", "noa@example.com", "manager", "shift lead")
        self.cashier_id = self._register("Ayala Levi", "ayala@example.com", "employee", "cashier")["user_id"]
        self.other_id = self._register("Yael Cohen", "yael@example.com", "employee", "cashier")["user_id"]

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        if self._saved_connection is not None:
            os.environ["SOMEE_SQLSERVER_CONNECTION_STRING"] = self._saved_connection

    def test_board_search_and_shift_details_follow_the_signed_in_role(self):
        self.assertEqual(self.client.get("/queries/board").status_code, 401)
        self._login("ayala@example.com")
        board = self.client.get("/queries/board?mode=week&date=2026-10-19&open_only=1")
        self.assertEqual(board.status_code, 200)
        shifts = board.get_json()["shifts"]
        self.assertEqual([row["required_role"] for row in shifts], ["cashier"])
        self.assertEqual(shifts[0]["weekday"], "Monday")

        detail = self.client.get("/queries/shifts/" + self.shift_id)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["shift"]["note"], "front desk")
        self.assertEqual(detail.get_json()["registered"], [])

        self.client.post("/auth/logout")
        self._login("noa@example.com")
        both = self.client.get("/queries/board?start=2026-10-19&end=2026-10-20&role=cashier")
        self.assertEqual(len(both.get_json()["shifts"]), 1)

    def test_employee_submits_constraint_assignment_and_swap(self):
        self._login("ayala@example.com")
        constraint = self.client.post(
            "/commands/availability",
            json={"date": "2026-10-21", "constraint_type": "prefer", "note": "morning"},
        )
        self.assertEqual(constraint.status_code, 201)
        self.assertEqual(constraint.get_json()["status"], "Waiting for agent evaluation")

        assignment = self.client.post(
            "/commands/assignments",
            json={"shift_id": self.shift_id, "note": "I can cover"},
        )
        self.assertEqual(assignment.status_code, 201)
        request_id = assignment.get_json()["request_id"]

        blocked = self.client.post(
            "/commands/availability",
            json={"date": "2026-10-19", "constraint_type": "unavailable"},
        )
        self.assertEqual(blocked.status_code, 201)
        self.client.post("/auth/logout")
        self._login("yael@example.com")
        denied = self.client.post(
            "/commands/swaps",
            json={"shift_id": self.shift_id, "replacement_employee_id": self.cashier_id},
        )
        self.assertEqual(denied.status_code, 400)

        self.client.post("/commands/assignments", json={"shift_id": self.shift_id})
        swap = self.client.post(
            "/commands/swaps",
            json={
                "shift_id": self.shift_id,
                "replacement_employee_id": self.cashier_id,
                "note": "please swap",
            },
        )
        self.assertEqual(swap.status_code, 400)
        self.assertIn("unavailable", swap.get_json()["error"])

        self.client.post("/auth/logout")
        self._login("ayala@example.com")
        detail = self.client.get("/queries/shifts/" + self.shift_id)
        registered = detail.get_json()["registered"]
        own = next(item for item in registered if item["status"] == "Waiting for agent evaluation" and "note" in item)
        self.assertEqual(own["request_id"], request_id)
        self.assertNotIn("full_name", own)
        self.assertNotIn("Yael", detail.get_data(as_text=True))
        self.assertEqual(len(registered), 1)
        self.assertEqual(detail.get_json()["shift"]["waiting_candidates"], 2)

        self.client.post("/auth/logout")
        self._login("noa@example.com")
        manager_view = self.client.get("/queries/shifts/" + self.shift_id)
        notes = [item.get("note") for item in manager_view.get_json()["registered"]]
        self.assertIn("I can cover", notes)
        board = self.client.get("/queries/board?date=2026-10-19&mode=week&role=cashier")
        self.assertEqual(board.get_json()["shifts"][0]["waiting_candidates"], 2)

    def test_shift_role_is_chosen_and_a_mismatch_is_blocked(self):
        self._login("noa@example.com")
        page = self.client.get("/shifts/new")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn('<select id="required_role"', html)
        self.assertNotIn('<input id="required_role"', html)
        for role_name in ("cashier", "shift lead", "representative"):
            self.assertIn(role_name, html)
        rejected = self.client.post(
            "/commands/shifts",
            json={
                "shift_date": "2026-10-21",
                "start_time": "09:00",
                "end_time": "17:00",
                "required_role": "baker",
                "required_headcount": 1,
            },
        )
        self.assertEqual(rejected.status_code, 400)
        self.client.post("/auth/logout")
        self._login("ayala@example.com")
        listed = self.client.get("/queries/board?start=2026-10-20&end=2026-10-20")
        shift_id = listed.get_json()["shifts"][0]["shift_id"]
        card = self.client.get("/shifts/" + shift_id)
        body = card.get_data(as_text=True)
        self.assertIn("does not match", body)
        self.assertNotIn("Submit request", body)
        denied = self.client.post("/commands/assignments", json={"shift_id": shift_id})
        self.assertEqual(denied.status_code, 400)
        self.assertIn("does not match", denied.get_json()["error"])

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
        response = self.client.post(
            "/auth/login",
            json={"email": email, "password": "password1"},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response


class SqlServerBooleanFilterTests(unittest.TestCase):
    def test_boolean_filters_compile_as_equals_for_sql_server(self):
        from pathlib import Path

        from sqlalchemy.dialects import mssql

        from app.auth.models import User
        from app.read_models import ApprovedAssignmentRow

        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (Path(__file__).resolve().parents[1] / "app").rglob("*.py")
        )
        self.assertNotIn(".is_(True)", source)
        self.assertNotIn(".is_(False)", source)
        dialect = mssql.dialect()
        for expression in (ApprovedAssignmentRow.active == True, User.is_active == True):
            sql = str(expression.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
            self.assertNotIn("IS 1", sql)
            self.assertIn("= 1", sql)

    def test_employee_dashboard_lists_only_active_assignments(self):
        from app.queries.dashboard import employee_dashboard
        from app.read_models import ApprovedAssignmentRow

        saved = os.environ.pop("SOMEE_SQLSERVER_CONNECTION_STRING", None)
        app = create_app()
        try:
            with app.app_context():
                db.create_all()
                db.session.add(
                    ApprovedAssignmentRow(
                        assignment_id="keep",
                        employee_id="emp-1",
                        shift_id="shift-1",
                        shift_date=date(2026, 10, 20),
                        start_time="09:00",
                        end_time="17:00",
                        hours=8,
                        active=True,
                    )
                )
                db.session.add(
                    ApprovedAssignmentRow(
                        assignment_id="drop",
                        employee_id="emp-1",
                        shift_id="shift-2",
                        shift_date=date(2026, 10, 21),
                        start_time="09:00",
                        end_time="17:00",
                        hours=8,
                        active=False,
                    )
                )
                db.session.commit()
                summary = employee_dashboard("emp-1", today=date(2026, 10, 14))
                self.assertEqual(
                    [row["shift_id"] for row in summary["approved_shifts"]],
                    ["shift-1"],
                )
                db.session.remove()
                db.drop_all()
                db.engine.dispose()
        finally:
            if saved is not None:
                os.environ["SOMEE_SQLSERVER_CONNECTION_STRING"] = saved
