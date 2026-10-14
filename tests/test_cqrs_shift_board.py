import unittest
from datetime import date

from flask import Flask
from sqlalchemy import BigInteger
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.commands.define_shift import CommandRejected, define_shift
from app.event_store import ASSIGNMENT_APPROVED, AGGREGATE_ASSIGNMENT_REQUEST, Event, append_event
from app.extensions import db, login_manager
from app.projections import project_shift_board
from app.read_models import ApprovedAssignmentRow, ShiftBoardRow
from app.queries import queries_bp
from app.queries.board import get_shift_board


@compiles(BigInteger, "sqlite")
def _sqlite_bigint_as_integer(element, compiler, **kw):
    return "INTEGER"


def build_app():
    app = Flask("cqrs-test")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    db.init_app(app)
    login_manager.init_app(app)
    app.register_blueprint(queries_bp, url_prefix="/queries")
    with app.app_context():
        db.create_all()
    return app


class ShiftBoardCqrsTests(unittest.TestCase):
    def setUp(self):
        self.app = build_app()

    def test_define_writes_an_event_and_projection_fills_the_board(self):
        with self.app.app_context():
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
            self.assertEqual(event.event_type, "ShiftDefined")
            self.assertEqual(get_shift_board(), [])

            processed = project_shift_board()
            self.assertEqual(processed, 1)
            rows = get_shift_board()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].status, "open")
            self.assertEqual(rows[0].assigned_count, 0)
            self.assertEqual(rows[0].required_headcount, 2)
            self.assertEqual(rows[0].weekday, "Monday")
            self.assertEqual(project_shift_board(), 0)
            self.assertEqual(len(get_shift_board()), 1)

            response = self.app.test_client().get("/queries/board")
            self.assertEqual(response.status_code, 401)

    def test_replaying_an_approved_assignment_does_not_insert_it_again(self):
        with self.app.app_context():
            shift = define_shift(
                actor_id="manager-1",
                actor_role="manager",
                shift_date="2026-10-19",
                start_time="08:00",
                end_time="16:00",
                required_role="cashier",
                required_headcount=1,
                today=date(2026, 10, 12),
            )
            project_shift_board()
            approved = append_event(
                aggregate_type=AGGREGATE_ASSIGNMENT_REQUEST,
                aggregate_id="request-032fb226",
                event_type=ASSIGNMENT_APPROVED,
                payload={
                    "shift_id": shift.aggregate_id,
                    "shift_date": "2026-10-19",
                    "start_time": "08:00",
                    "end_time": "16:00",
                    "employee_id": "employee-1",
                    "manager_note": "",
                },
                actor_id="manager-1",
            )
            db.session.add(
                ApprovedAssignmentRow(
                    assignment_id=approved.aggregate_id,
                    employee_id="employee-1",
                    shift_id=shift.aggregate_id,
                    shift_date=date(2026, 10, 19),
                    start_time="08:00",
                    end_time="16:00",
                    hours=8,
                    active=True,
                )
            )
            db.session.commit()
            project_shift_board()
            board = db.session.get(ShiftBoardRow, shift.aggregate_id)
            self.assertEqual(board.assigned_count, 0)
            self.assertEqual(
                db.session.query(ApprovedAssignmentRow).count(),
                1,
            )

    def test_rejected_command_stores_nothing(self):
        with self.app.app_context():
            with self.assertRaises(CommandRejected):
                define_shift(
                    actor_id="employee-1",
                    actor_role="employee",
                    shift_date="2026-10-19",
                    start_time="09:00",
                    end_time="17:00",
                    required_role="cashier",
                    required_headcount=1,
                    today=date(2026, 10, 12),
                )
            with self.assertRaises(CommandRejected):
                define_shift(
                    actor_id="manager-1",
                    actor_role="manager",
                    shift_date="2026-10-01",
                    start_time="17:00",
                    end_time="09:00",
                    required_role="cashier",
                    required_headcount=0,
                    today=date(2026, 10, 12),
                )
            self.assertEqual(db.session.query(Event).count(), 0)


if __name__ == "__main__":
    unittest.main()
