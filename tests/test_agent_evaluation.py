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

    def test_an_employee_confirms_before_overriding_a_constraint(self):
        from app.commands.define_shift import CommandRejected
        from app.commands.submit_requests import CONSTRAINT_CONFLICT_PROMPT, request_assignment

        self._unavailable(self.employee_id, "2026-10-19")
        self._login("ayala@example.com")
        prompted = self.client.post("/shifts/" + self.shift_id + "/request", data={"note": "I can cover"})
        page = prompted.get_data(as_text=True)
        self.assertEqual(prompted.status_code, 400)
        self.assertIn(CONSTRAINT_CONFLICT_PROMPT, page)
        self.assertIn("Proceed", page)
        self.assertIn("I can cover", page)
        with self.app.app_context():
            with self.assertRaises(CommandRejected) as caught:
                request_assignment(
                    actor_id=self.employee_id,
                    actor_role="employee",
                    shift_id=self.shift_id,
                )
            self.assertEqual(str(caught.exception), CONSTRAINT_CONFLICT_PROMPT)
            self.assertFalse(self._has(ASSIGNMENT_REQUESTED))
            stored = request_assignment(
                actor_id=self.employee_id,
                actor_role="employee",
                shift_id=self.shift_id,
                confirm_constraints=True,
            )
            import json

            self.assertTrue(json.loads(stored.payload_json)["constraint_override"])

    def test_a_self_request_warns_for_an_unavailable_constraint(self):
        from app.commands.decide_request import decide_request
        from app.commands.define_shift import CommandRejected
        from app.commands.submit_requests import register_employee

        self._unavailable(self.employee_id, "2026-10-19")
        self._request(self.employee_id, "cashier")
        self._cycle()
        payload = self._payload(self._recommendation())
        self.assertEqual(payload["decision"], "warning")
        self.assertIn("conflicts with the employee's constraints", payload["rationale"])
        with self.app.app_context():
            request_id = (
                db.session.query(Event.aggregate_id)
                .filter(Event.event_type == ASSIGNMENT_REQUESTED)
                .scalar()
            )
            decide_request(
                actor_id="manager-1",
                actor_role="manager",
                request_id=request_id,
                action="approve",
            )
            later = define_shift(
                actor_id="manager-1",
                actor_role="manager",
                shift_date="2026-10-20",
                start_time="09:00",
                end_time="17:00",
                required_role="cashier",
                required_headcount=1,
                today=date(2026, 10, 12),
            )
            later_id = later.aggregate_id
            project_shift_board()
            self._unavailable(self.employee_id, "2026-10-20")
            with self.assertRaises(CommandRejected) as caught:
                register_employee(
                    actor_id="manager-1",
                    actor_role="manager",
                    shift_id=later_id,
                    employee_id=self.employee_id,
                )
        self.assertIn("unavailable", str(caught.exception).lower())

    def test_a_manager_request_warns_when_a_night_note_matches_the_shift(self):
        from app.queries.board import recommendation_findings

        with self.app.app_context():
            append_event(
                aggregate_type="EmployeeAvailability",
                aggregate_id="constraint-night-note",
                event_type="AvailabilityConstraintSubmitted",
                payload={
                    "employee_id": self.employee_id,
                    "constraint_type": "prefer not",
                    "note": "I'm afraid to work at night because there are no buses",
                },
                actor_id=self.employee_id,
            )
            project_shift_board()
        self._request(
            self.employee_id,
            "cashier",
            shift_date="2026-11-01",
            start="22:00",
            end="23:00",
            actor_id="manager-1",
        )
        self._cycle(classify=lambda note: "nights")
        payload = self._payload(self._recommendation())
        self.assertEqual(payload["decision"], "warning")
        self.assertIn("night", payload["rationale"].lower())
        availability = next(item for item in payload["sources"] if item["name"] == "check_availability_conflicts")
        self.assertTrue(availability["soft_block"])
        notes = next(item for item in payload["sources"] if item["name"] == "constraint_notes")
        self.assertIn("buses", notes["notes"][0]["note"])
        findings = recommendation_findings(payload["sources"])
        self.assertTrue(any("night" in item.lower() for item in findings))
        self.assertTrue(any("buses" in item for item in findings))

    def test_a_manager_placed_request_rejects_unavailable(self):
        from app.commands.decide_request import CommandRejected, decide_request

        self._unavailable(self.employee_id, "2026-10-21")
        self._request(self.employee_id, "cashier", shift_date="2026-10-21", actor_id="manager-1")
        self._cycle()
        payload = self._payload(self._recommendation())
        self.assertEqual(payload["decision"], "reject")
        self.assertIn("unavailable", payload["rationale"].lower())
        with self.app.app_context():
            request_id = (
                db.session.query(Event)
                .filter(Event.event_type == ASSIGNMENT_REQUESTED)
                .one()
                .aggregate_id
            )
            with self.assertRaises(CommandRejected) as caught:
                decide_request(
                    actor_id="manager-1",
                    actor_role="manager",
                    request_id=request_id,
                    action="approve",
                )
        self.assertIn("unavailable", str(caught.exception).lower())

    def test_a_failed_hours_tool_still_reaches_the_manager(self):
        self._request(self.employee_id, "cashier")
        evaluated = self._cycle(weekly_hours=lambda *args, **kwargs: error("invalid_hours"))
        self.assertEqual(evaluated, 1)
        payload = self._payload(self._recommendation())
        self.assertEqual(payload["decision"], "warning")
        self.assertIn("manager still needs to decide", payload["rationale"])
        self.assertEqual(self._cycle(), 0)

    def _cycle(self, **kwargs):
        options = {
            "search": lambda shift_date: ok({"found": False, "title": "", "source": "", "kind": ""}),
            "retrieve": lambda *args, **kwargs: [],
        }
        options.update(kwargs)
        return run_evaluation_cycle(self.app, **options)

    def _unavailable(self, employee_id, shift_date):
        with self.app.app_context():
            append_event(
                aggregate_type="EmployeeAvailability",
                aggregate_id="constraint-" + employee_id + "-" + shift_date,
                event_type="AvailabilityConstraintSubmitted",
                payload={
                    "employee_id": employee_id,
                    "start_date": shift_date,
                    "end_date": shift_date,
                    "constraint_type": "unavailable",
                    "note": "",
                },
                actor_id=employee_id,
            )
            project_shift_board()

    def _request(self, employee_id, role, shift_date="2026-10-19", start="09:00", end="17:00", actor_id=None):
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
                actor_id=actor_id or employee_id,
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
