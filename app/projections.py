import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, text

from app.event_store import (
    AGENT_EVALUATION_COMPLETED,
    ASSIGNMENT_APPROVED,
    ASSIGNMENT_REJECTED,
    ASSIGNMENT_REQUESTED,
    AVAILABILITY_CONSTRAINT_SUBMITTED,
    AVAILABILITY_CONSTRAINT_WITHDRAWN,
    SHIFT_CANCELLED,
    SHIFT_DEFINED,
    SWAP_APPROVED,
    SWAP_COLLEAGUE_APPROVED,
    SWAP_COLLEAGUE_REJECTED,
    SWAP_REJECTED,
    SWAP_REQUESTED,
    Event,
)
from app.extensions import db, run_once
from app.read_models import (
    ApprovedAssignmentRow,
    AvailabilityConstraintRow,
    EmployeeHoursRow,
    ProjectorCheckpoint,
    RequestRecommendationRow,
    ShiftBoardRow,
    ShiftRequestRow,
)
from app.tools.schedule import duration_hours, week_start_sunday

SHIFT_BOARD_PROJECTOR = "shift_board"
ISRAEL = ZoneInfo("Asia/Jerusalem")
WAITING_FOR_AGENT = "Waiting for agent evaluation"
WAITING_FOR_EMPLOYEE = "Waiting for employee approval"
WAITING_FOR_MANAGER = "Waiting for manager decision"
STATUS_APPROVED = "Approved"
STATUS_REJECTED = "Rejected"


def ensure_read_models():
    run_once("read_models_ready", _create_read_model_tables)
    close_expired_shifts()


def _create_read_model_tables():
    ShiftBoardRow.__table__.create(db.engine, checkfirst=True)
    ProjectorCheckpoint.__table__.create(db.engine, checkfirst=True)
    ShiftRequestRow.__table__.create(db.engine, checkfirst=True)
    _ensure_decision_note()
    _ensure_display_number()
    RequestRecommendationRow.__table__.create(db.engine, checkfirst=True)
    _ensure_sources_json()
    AvailabilityConstraintRow.__table__.create(db.engine, checkfirst=True)
    _ensure_constraint_schedule()
    _ensure_optional_constraint_dates()
    EmployeeHoursRow.__table__.create(db.engine, checkfirst=True)
    ApprovedAssignmentRow.__table__.create(db.engine, checkfirst=True)
    RequestRecommendationRow.__table__.create(db.engine, checkfirst=True)


def close_expired_shifts(today=None):
    """Open or partial shifts before today are closed. Registration is no longer possible."""
    current = today or datetime.now(ISRAEL).date()
    rows = (
        db.session.query(ShiftBoardRow)
        .filter(
            ShiftBoardRow.shift_date < current,
            ShiftBoardRow.status.in_(["open", "partial"]),
        )
        .all()
    )
    for row in rows:
        row.status = "closed"
    if rows:
        db.session.commit()
    return len(rows)


def project_shift_board():
    ensure_read_models()
    checkpoint = db.session.get(ProjectorCheckpoint, SHIFT_BOARD_PROJECTOR)
    last_event_id = checkpoint.last_event_id if checkpoint else 0
    events = (
        db.session.query(Event)
        .filter(Event.event_id > last_event_id)
        .order_by(Event.event_id)
        .all()
    )
    processed = 0
    for event in events:
        if event.event_type == SHIFT_DEFINED:
            _apply_shift_defined(event)
        elif event.event_type == ASSIGNMENT_REQUESTED:
            _apply_request(event, "assignment")
        elif event.event_type == SWAP_REQUESTED:
            _apply_request(event, "swap")
        elif event.event_type == SWAP_COLLEAGUE_APPROVED:
            _apply_colleague(event, WAITING_FOR_AGENT)
        elif event.event_type == SWAP_COLLEAGUE_REJECTED:
            _apply_colleague(event, STATUS_REJECTED)
        elif event.event_type == AVAILABILITY_CONSTRAINT_SUBMITTED:
            _apply_availability(event)
        elif event.event_type == AVAILABILITY_CONSTRAINT_WITHDRAWN:
            _apply_availability_withdrawn(event)
        elif event.event_type == ASSIGNMENT_APPROVED:
            _apply_assignment_approved(event)
        elif event.event_type == ASSIGNMENT_REJECTED:
            _apply_decision(event, STATUS_REJECTED)
        elif event.event_type == SWAP_APPROVED:
            _apply_swap_approved(event)
        elif event.event_type == SWAP_REJECTED:
            _apply_decision(event, STATUS_REJECTED)
        elif event.event_type == SHIFT_CANCELLED:
            _apply_shift_cancelled(event)
        elif event.event_type == AGENT_EVALUATION_COMPLETED:
            _apply_evaluation(event)
        if checkpoint is None:
            checkpoint = ProjectorCheckpoint(
                projector_name=SHIFT_BOARD_PROJECTOR,
                last_event_id=event.event_id,
            )
            db.session.add(checkpoint)
        else:
            checkpoint.last_event_id = event.event_id
        db.session.commit()
        processed += 1
    close_expired_shifts()
    return processed


def _apply_shift_defined(event):
    payload = json.loads(event.payload_json)
    shift_date = date.fromisoformat(payload["shift_date"])
    row = db.session.get(ShiftBoardRow, event.aggregate_id)
    if row is None:
        row = ShiftBoardRow(shift_id=event.aggregate_id)
        db.session.add(row)
    row.shift_date = shift_date
    row.weekday = shift_date.strftime("%A")
    row.start_time = payload["start_time"]
    row.end_time = payload["end_time"]
    row.required_role = payload["required_role"]
    row.assigned_count = 0
    row.required_headcount = int(payload["required_headcount"])
    row.status = "open"
    row.waiting_candidates = 0
    row.note = payload.get("note") or ""


def _apply_request(event, request_type):
    payload = json.loads(event.payload_json)
    row = ShiftRequestRow(
        request_id=event.aggregate_id,
        shift_id=payload["shift_id"],
        request_type=request_type,
        employee_id=payload["employee_id"],
        full_name=payload.get("full_name") or "",
        professional_role=payload.get("professional_role") or "",
        status=payload.get("status") or "Waiting for agent evaluation",
        note=payload.get("note") or "",
        decision_note="",
        replacement_employee_id=payload.get("replacement_employee_id"),
        replacement_name=payload.get("replacement_name"),
        display_number=payload.get("display_number"),
    )
    db.session.add(row)
    if request_type == "assignment":
        shift = db.session.get(ShiftBoardRow, payload["shift_id"])
        if shift is not None:
            shift.waiting_candidates = (shift.waiting_candidates or 0) + 1


def _apply_availability(event):
    payload = json.loads(event.payload_json)
    row = db.session.get(AvailabilityConstraintRow, event.aggregate_id)
    if row is None:
        row = AvailabilityConstraintRow(constraint_id=event.aggregate_id)
        db.session.add(row)
    row.employee_id = payload["employee_id"]
    row.start_date = _optional_date(payload.get("start_date"))
    row.end_date = _optional_date(payload.get("end_date"))
    row.constraint_type = payload["constraint_type"]
    row.note = payload.get("note") or ""
    row.weekdays = payload.get("weekdays") or ""
    row.time_start = payload.get("time_start") or ""
    row.time_end = payload.get("time_end") or ""
    row.nights = bool(payload.get("nights"))
    row.shabbat_eves = bool(payload.get("shabbat_eves"))
    row.holidays = bool(payload.get("holidays"))


def _apply_availability_withdrawn(event):
    row = db.session.get(AvailabilityConstraintRow, event.aggregate_id)
    if row is not None:
        db.session.delete(row)


def _optional_date(value):
    if not value:
        return None
    return date.fromisoformat(value)


def _apply_assignment_approved(event):
    payload = json.loads(event.payload_json)
    shift = db.session.get(ShiftBoardRow, payload.get("shift_id"))
    if shift is not None and shift.status == "cancelled":
        return
    _close_request(event.aggregate_id, STATUS_APPROVED, payload.get("manager_note") or "")
    if db.session.get(ApprovedAssignmentRow, event.aggregate_id) is not None:
        return
    shift_date = date.fromisoformat(payload["shift_date"])
    hours = duration_hours(payload["start_time"], payload["end_time"])
    row = ApprovedAssignmentRow(
        assignment_id=event.aggregate_id,
        employee_id=payload["employee_id"],
        shift_id=payload["shift_id"],
        shift_date=shift_date,
        start_time=payload["start_time"],
        end_time=payload["end_time"],
        hours=hours,
        active=True,
    )
    db.session.add(row)
    _add_hours(payload["employee_id"], week_start_sunday(shift_date), hours)
    if shift is not None:
        shift.assigned_count = (shift.assigned_count or 0) + 1
        _refresh_status(shift)


def _apply_shift_cancelled(event):
    shift = db.session.get(ShiftBoardRow, event.aggregate_id)
    if shift is not None:
        shift.status = "cancelled"
    assignments = (
        db.session.query(ApprovedAssignmentRow)
        .filter(ApprovedAssignmentRow.shift_id == event.aggregate_id)
        .all()
    )
    for assignment in assignments:
        if not assignment.active:
            continue
        assignment.active = False
        _add_hours(
            assignment.employee_id,
            week_start_sunday(assignment.shift_date),
            -assignment.hours,
        )


def _add_hours(employee_id, week_start, hours):
    row = db.session.get(EmployeeHoursRow, (employee_id, week_start))
    if row is None:
        row = EmployeeHoursRow(
            employee_id=employee_id,
            week_start=week_start,
            approved_hours=0,
        )
        db.session.add(row)
    row.approved_hours = round((row.approved_hours or 0) + hours, 2)
    if row.approved_hours <= 0:
        db.session.delete(row)


def _apply_evaluation(event):
    payload = json.loads(event.payload_json)
    row = db.session.get(RequestRecommendationRow, event.aggregate_id)
    if row is None:
        row = RequestRecommendationRow(request_id=event.aggregate_id)
        db.session.add(row)
    row.shift_id = payload["shift_id"]
    row.employee_id = payload["employee_id"]
    row.request_type = payload["request_type"]
    row.decision = payload["decision"]
    row.rationale = payload["rationale"]
    row.hours_warning = bool(payload.get("hours_warning"))
    row.sources_json = json.dumps(payload.get("sources") or [])
    request_row = db.session.get(ShiftRequestRow, event.aggregate_id)
    if request_row is not None and request_row.status == WAITING_FOR_AGENT:
        request_row.status = WAITING_FOR_MANAGER


def _apply_decision(event, status):
    payload = json.loads(event.payload_json)
    _close_request(event.aggregate_id, status, payload.get("manager_note") or "")


def _apply_swap_approved(event):
    payload = json.loads(event.payload_json)
    shift = db.session.get(ShiftBoardRow, payload.get("shift_id"))
    if shift is not None and shift.status == "cancelled":
        return
    original = (
        db.session.query(ApprovedAssignmentRow)
        .filter(
            ApprovedAssignmentRow.employee_id == payload["employee_id"],
            ApprovedAssignmentRow.shift_id == payload["shift_id"],
            ApprovedAssignmentRow.active == True,
        )
        .first()
    )
    shift_date = date.fromisoformat(payload["shift_date"])
    hours = duration_hours(payload["start_time"], payload["end_time"])
    if original is not None:
        original.active = False
        _add_hours(original.employee_id, week_start_sunday(original.shift_date), -original.hours)
    db.session.add(
        ApprovedAssignmentRow(
            assignment_id=event.aggregate_id,
            employee_id=payload["replacement_employee_id"],
            shift_id=payload["shift_id"],
            shift_date=shift_date,
            start_time=payload["start_time"],
            end_time=payload["end_time"],
            hours=hours,
            active=True,
        )
    )
    _add_hours(payload["replacement_employee_id"], week_start_sunday(shift_date), hours)
    if shift is not None and original is None:
        shift.assigned_count = (shift.assigned_count or 0) + 1
        _refresh_status(shift)
    _close_request(event.aggregate_id, STATUS_APPROVED, payload.get("manager_note") or "")


def _apply_colleague(event, status):
    row = db.session.get(ShiftRequestRow, event.aggregate_id)
    if row is None:
        return
    row.status = status


def _close_request(request_id, status, decision_note):
    row = db.session.get(ShiftRequestRow, request_id)
    if row is None:
        return
    was_waiting = row.status in {WAITING_FOR_AGENT, WAITING_FOR_MANAGER}
    row.status = status
    row.decision_note = (decision_note or "")[:300]
    if was_waiting and row.request_type == "assignment":
        shift = db.session.get(ShiftBoardRow, row.shift_id)
        if shift is not None and shift.waiting_candidates:
            shift.waiting_candidates -= 1


def _ensure_display_number():
    _ensure_column(
        "read_shift_requests",
        "display_number",
        sqlite_sql="ALTER TABLE read_shift_requests ADD COLUMN display_number INTEGER",
        sqlserver_sql="ALTER TABLE read_shift_requests ADD display_number INT NULL",
    )


def _ensure_sources_json():
    _ensure_column(
        "read_request_recommendations",
        "sources_json",
        sqlite_sql="ALTER TABLE read_request_recommendations ADD COLUMN sources_json TEXT",
        sqlserver_sql="ALTER TABLE read_request_recommendations ADD sources_json NVARCHAR(MAX) NULL",
    )


def _ensure_column(table_name, column_name, sqlite_sql, sqlserver_sql):
    columns = {column["name"] for column in inspect(db.engine).get_columns(table_name)}
    if column_name in columns:
        return
    statement = sqlite_sql if db.engine.dialect.name == "sqlite" else sqlserver_sql
    db.session.execute(text(statement))
    db.session.commit()


def _ensure_optional_constraint_dates():
    if db.engine.dialect.name == "sqlite":
        return
    columns = {column["name"]: column for column in inspect(db.engine).get_columns("read_availability_constraints")}
    for name in ("start_date", "end_date"):
        if columns[name].get("nullable"):
            continue
        db.session.execute(
            text("ALTER TABLE read_availability_constraints ALTER COLUMN {0} DATE NULL".format(name))
        )
        db.session.commit()


def _ensure_constraint_schedule():
    columns = (
        (
            "weekdays",
            "ALTER TABLE read_availability_constraints ADD COLUMN weekdays VARCHAR(40) NOT NULL DEFAULT ''",
            "ALTER TABLE read_availability_constraints ADD weekdays VARCHAR(40) NOT NULL "
            "CONSTRAINT DF_read_availability_constraints_weekdays DEFAULT ''",
        ),
        (
            "time_start",
            "ALTER TABLE read_availability_constraints ADD COLUMN time_start VARCHAR(5) NOT NULL DEFAULT ''",
            "ALTER TABLE read_availability_constraints ADD time_start VARCHAR(5) NOT NULL "
            "CONSTRAINT DF_read_availability_constraints_time_start DEFAULT ''",
        ),
        (
            "time_end",
            "ALTER TABLE read_availability_constraints ADD COLUMN time_end VARCHAR(5) NOT NULL DEFAULT ''",
            "ALTER TABLE read_availability_constraints ADD time_end VARCHAR(5) NOT NULL "
            "CONSTRAINT DF_read_availability_constraints_time_end DEFAULT ''",
        ),
        (
            "nights",
            "ALTER TABLE read_availability_constraints ADD COLUMN nights BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE read_availability_constraints ADD nights BIT NOT NULL "
            "CONSTRAINT DF_read_availability_constraints_nights DEFAULT 0",
        ),
        (
            "shabbat_eves",
            "ALTER TABLE read_availability_constraints ADD COLUMN shabbat_eves BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE read_availability_constraints ADD shabbat_eves BIT NOT NULL "
            "CONSTRAINT DF_read_availability_constraints_shabbat_eves DEFAULT 0",
        ),
        (
            "holidays",
            "ALTER TABLE read_availability_constraints ADD COLUMN holidays BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE read_availability_constraints ADD holidays BIT NOT NULL "
            "CONSTRAINT DF_read_availability_constraints_holidays DEFAULT 0",
        ),
    )
    for name, sqlite_sql, sqlserver_sql in columns:
        _ensure_column("read_availability_constraints", name, sqlite_sql, sqlserver_sql)


def _ensure_decision_note():
    columns = {column["name"] for column in inspect(db.engine).get_columns("read_shift_requests")}
    if "decision_note" in columns:
        return
    if db.engine.dialect.name == "sqlite":
        statement = (
            "ALTER TABLE read_shift_requests "
            "ADD COLUMN decision_note VARCHAR(300) NOT NULL DEFAULT ''"
        )
    else:
        statement = (
            "ALTER TABLE read_shift_requests "
            "ADD decision_note VARCHAR(300) NOT NULL "
            "CONSTRAINT DF_read_shift_requests_decision_note DEFAULT ''"
        )
    db.session.execute(text(statement))
    db.session.commit()


def _refresh_status(row):
    if row.assigned_count <= 0:
        row.assigned_count = 0
        row.status = "open"
    elif row.assigned_count < row.required_headcount:
        row.status = "partial"
    else:
        row.status = "staffed"
