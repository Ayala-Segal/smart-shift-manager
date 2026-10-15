import os
import unittest

from app import create_app
from app.agent.cycle import apply_constraint_notes
from app.agent.decision import decide
from app.event_store import append_event
from app.extensions import db
from app.projections import project_shift_board
from app.queries.board import recommendation_findings
from app.tools.conflicts import check_availability_conflicts


class ConstraintScheduleTests(unittest.TestCase):
    def setUp(self):
        self._saved_connection = os.environ.pop("SOMEE_SQLSERVER_CONNECTION_STRING", None)
        self.app = create_app()
        self.app.config["TESTING"] = True
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        if self._saved_connection is not None:
            os.environ["SOMEE_SQLSERVER_CONNECTION_STRING"] = self._saved_connection

    def test_an_old_date_constraint_still_blocks_the_whole_day(self):
        with self.app.app_context():
            self._store("employee-old", "unavailable", note="")
            blocked = check_availability_conflicts("employee-old", "2026-10-19", "09:00", "17:00")
        self.assertTrue(blocked["result"]["hard_block"])
        self.assertIn("unavailable", blocked["result"]["reasons"][0])

    def test_a_night_constraint_blocks_only_a_night_shift(self):
        with self.app.app_context():
            self._store("employee-night", "unavailable", nights=True)
            night = check_availability_conflicts("employee-night", "2026-10-19", "22:00", "06:00")
            day = check_availability_conflicts("employee-night", "2026-10-19", "09:00", "17:00")
        self.assertTrue(night["result"]["hard_block"])
        self.assertFalse(day["result"]["hard_block"])

    def test_a_night_note_on_a_prefer_constraint_warns_for_a_night_shift(self):
        with self.app.app_context():
            self._store("employee-note", "prefer", note="אני לא רוצה לעבוד בלילה")
            conflict = check_availability_conflicts("employee-note", "2026-10-19", "22:00", "06:00")
            apply_constraint_notes(
                conflict,
                shift_date="2026-10-19",
                start_time="22:00",
                end_time="06:00",
                holiday=False,
                classify=lambda note: "nights",
            )
            choice = decide(**self._decision_input(conflict))
        self.assertEqual(choice["decision"], "warning")
        self.assertIn("night", choice["rationale"])

    def test_a_personal_note_does_not_change_the_decision_and_reaches_the_manager(self):
        with self.app.app_context():
            self._store("employee-wedding", "prefer", note="חתונה של אחותי")
            conflict = check_availability_conflicts("employee-wedding", "2026-10-19", "09:00", "17:00")
            apply_constraint_notes(
                conflict,
                shift_date="2026-10-19",
                start_time="09:00",
                end_time="17:00",
                holiday=False,
                classify=lambda note: "personal",
            )
            choice = decide(**self._decision_input(conflict))
            findings = recommendation_findings(
                [{"name": "constraint_notes", "ok": True, "notes": conflict["result"]["constraint_notes"]}]
            )
        self.assertEqual(choice["decision"], "approve")
        self.assertIn("חתונה של אחותי", findings[0])

    def test_a_failed_classifier_keeps_the_note_and_the_form_rules(self):
        def fail(note):
            raise TimeoutError("ollama is down")

        with self.app.app_context():
            self._store("employee-fail", "prefer", note="אני לא רוצה לעבוד בלילה")
            self._store("employee-form", "unavailable", nights=True)
            day = check_availability_conflicts("employee-fail", "2026-10-19", "09:00", "17:00")
            apply_constraint_notes(
                day,
                shift_date="2026-10-19",
                start_time="09:00",
                end_time="17:00",
                holiday=False,
                classify=fail,
            )
            day_choice = decide(**self._decision_input(day))
            night = check_availability_conflicts("employee-form", "2026-10-19", "22:00", "06:00")
            apply_constraint_notes(
                night,
                shift_date="2026-10-19",
                start_time="22:00",
                end_time="06:00",
                holiday=False,
                classify=fail,
            )
            night_choice = decide(**self._decision_input(night))
        self.assertEqual(day["result"]["constraint_notes"][0]["label"], "personal")
        self.assertEqual(day["result"]["constraint_notes"][0]["note"], "אני לא רוצה לעבוד בלילה")
        self.assertEqual(day_choice["decision"], "approve")
        self.assertEqual(night_choice["decision"], "reject")

    def _store(self, employee_id, constraint_type, note="", nights=False):
        append_event(
            aggregate_type="EmployeeAvailability",
            aggregate_id="constraint-" + employee_id,
            event_type="AvailabilityConstraintSubmitted",
            payload={
                "employee_id": employee_id,
                "start_date": "2026-10-19",
                "end_date": "2026-10-19",
                "constraint_type": constraint_type,
                "note": note,
                "nights": nights,
            },
            actor_id=employee_id,
        )
        project_shift_board()

    def _decision_input(self, conflict):
        return {
            "role_matches": True,
            "conflict": conflict,
            "hours": {
                "ok": True,
                "result": {
                    "approved_hours": 0,
                    "projected_hours": 8,
                    "weekly_limit": 42,
                    "exceeds_limit": False,
                },
            },
            "tavily": {"ok": True, "result": {"found": False, "title": ""}},
            "retrieval_ok": True,
            "shift_hours": 8,
        }


class WeekdayCommandTests(unittest.TestCase):
    def test_evening_hours_without_a_date_apply_on_any_day(self):
        saved = os.environ.pop("SOMEE_SQLSERVER_CONNECTION_STRING", None)
        app = create_app()
        app.config["TESTING"] = True
        try:
            with app.app_context():
                db.create_all()
                from app.commands.submit_requests import submit_availability
                from app.projections import project_shift_board

                submit_availability(
                    actor_id="employee-evenings",
                    actor_role="employee",
                    constraint_type="prefer not",
                    time_start="16:00",
                    time_end="22:00",
                )
                project_shift_board()
                evening = check_availability_conflicts("employee-evenings", "2026-11-03", "17:00", "21:00")
                morning = check_availability_conflicts("employee-evenings", "2026-12-01", "09:00", "13:00")
            self.assertTrue(evening["result"]["soft_block"])
            self.assertFalse(evening["result"]["hard_block"])
            self.assertFalse(morning["result"]["soft_block"])
        finally:
            with app.app_context():
                db.session.remove()
                db.drop_all()
                db.engine.dispose()
            if saved is not None:
                os.environ["SOMEE_SQLSERVER_CONNECTION_STRING"] = saved

    def test_weekdays_are_stored_in_order(self):
        saved = os.environ.pop("SOMEE_SQLSERVER_CONNECTION_STRING", None)
        app = create_app()
        app.config["TESTING"] = True
        try:
            with app.app_context():
                db.create_all()
                from app.commands.submit_requests import submit_availability

                event = submit_availability(
                    actor_id="employee-days",
                    actor_role="employee",
                    constraint_type="prefer not",
                    shift_date="2026-10-19",
                    weekdays=["fri", "mon"],
                    shabbat_eves=True,
                )
                import json

                payload = json.loads(event.payload_json)
                self.assertEqual(payload["weekdays"], "mon,fri")
                self.assertTrue(payload["shabbat_eves"])
        finally:
            with app.app_context():
                db.session.remove()
                db.drop_all()
                db.engine.dispose()
            if saved is not None:
                os.environ["SOMEE_SQLSERVER_CONNECTION_STRING"] = saved


class ConstraintChangeTests(unittest.TestCase):
    def setUp(self):
        self._saved_connection = os.environ.pop("SOMEE_SQLSERVER_CONNECTION_STRING", None)
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
        if self._saved_connection is not None:
            os.environ["SOMEE_SQLSERVER_CONNECTION_STRING"] = self._saved_connection

    def test_cancelling_a_constraint_stops_it_from_blocking(self):
        from app.commands.define_shift import CommandRejected
        from app.commands.submit_requests import submit_availability, withdraw_availability
        from app.queries.dashboard import constraints_for

        with self.app.app_context():
            event = submit_availability(
                actor_id="employee-cancel",
                actor_role="employee",
                constraint_type="unavailable",
                shift_date="2026-10-19",
            )
            project_shift_board()
            withdraw_availability(
                actor_id="employee-cancel",
                actor_role="employee",
                constraint_id=event.aggregate_id,
            )
            project_shift_board()
            blocked = check_availability_conflicts("employee-cancel", "2026-10-19", "09:00", "17:00")
            listed = constraints_for("employee-cancel")
            with self.assertRaises(CommandRejected):
                withdraw_availability(
                    actor_id="employee-cancel",
                    actor_role="employee",
                    constraint_id=event.aggregate_id,
                )
            with self.assertRaises(CommandRejected):
                withdraw_availability(
                    actor_id="employee-other",
                    actor_role="employee",
                    constraint_id=event.aggregate_id,
                )
        self.assertFalse(blocked["result"]["hard_block"])
        self.assertEqual(listed, [])

    def test_replacing_a_constraint_keeps_one_row_and_the_new_limit(self):
        from app.commands.submit_requests import submit_availability
        from app.queries.dashboard import constraints_for

        with self.app.app_context():
            event = submit_availability(
                actor_id="employee-replace",
                actor_role="employee",
                constraint_type="unavailable",
                shift_date="2026-10-19",
            )
            constraint_id = event.aggregate_id
            project_shift_board()
            replaced = submit_availability(
                actor_id="employee-replace",
                actor_role="employee",
                constraint_type="prefer not",
                time_start="16:00",
                time_end="22:00",
                constraint_id=constraint_id,
            )
            replaced_id = replaced.aggregate_id
            replaced_version = replaced.version
            project_shift_board()
            evening = check_availability_conflicts("employee-replace", "2026-11-02", "17:00", "21:00")
            morning = check_availability_conflicts("employee-replace", "2026-10-19", "09:00", "17:00")
            listed = constraints_for("employee-replace")
        self.assertEqual(replaced_id, constraint_id)
        self.assertEqual(replaced_version, 2)
        self.assertTrue(evening["result"]["soft_block"])
        self.assertFalse(evening["result"]["hard_block"])
        self.assertFalse(morning["result"]["hard_block"])
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["constraint_type"], "prefer not")
        self.assertEqual(listed[0]["when"], "Any date")

    def test_the_constraints_page_can_cancel_and_replace(self):
        self.client.post(
            "/register",
            data={
                "full_name": "Roni Paz",
                "email": "roni.change@example.com",
                "password": "password1",
                "role": "employee",
                "professional_role": "cashier",
            },
        )
        self.client.post(
            "/sign-in",
            data={"email": "roni.change@example.com", "password": "password1"},
        )
        saved = self.client.post(
            "/constraints",
            data={"constraint_type": "unavailable", "note": "no evenings", "time_start": "16:00", "time_end": "22:00"},
            follow_redirects=True,
        )
        page = saved.get_data(as_text=True)
        self.assertIn("Cancel", page)
        self.assertIn("Replace", page)
        self.assertIn("no evenings", page)
        start = page.find("/constraints/")
        constraint_id = page[start + len("/constraints/"):].split("/")[0]
        edited = self.client.get("/constraints/" + constraint_id + "/edit")
        self.assertIn("Replace constraint", edited.get_data(as_text=True))
        self.assertIn("no evenings", edited.get_data(as_text=True))
        replaced = self.client.post(
            "/constraints",
            data={
                "constraint_id": constraint_id,
                "constraint_type": "prefer not",
                "note": "late evenings only",
                "time_start": "18:00",
                "time_end": "22:00",
            },
            follow_redirects=True,
        )
        replaced_page = replaced.get_data(as_text=True)
        self.assertIn("late evenings only", replaced_page)
        self.assertIn("18:00–22:00", replaced_page)
        self.assertNotIn("no evenings", replaced_page)
        self.assertIn("Constraint " + constraint_id + " is replaced.", replaced_page)
        cancelled = self.client.post(
            "/constraints/" + constraint_id + "/cancel",
            follow_redirects=True,
        )
        self.assertIn("No constraints yet.", cancelled.get_data(as_text=True))
        self.assertNotIn("late evenings only", cancelled.get_data(as_text=True))
