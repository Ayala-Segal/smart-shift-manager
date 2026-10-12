import json
from datetime import date

from app.event_store import (
    ASSIGNMENT_APPROVED,
    ASSIGNMENT_REQUESTED,
    AVAILABILITY_CONSTRAINT_SUBMITTED,
    SHIFT_CANCELLED,
    SHIFT_DEFINED,
    SWAP_REQUESTED,
    Event,
)
from app.extensions import db
from app.read_models import (
    ApprovedAssignmentRow,
    AvailabilityConstraintRow,
    EmployeeHoursRow,
    ProjectorCheckpoint,
    ShiftBoardRow,
    ShiftRequestRow,
)
from app.tools.schedule import duration_hours, week_start_sunday

SHIFT_BOARD_PROJECTOR = "shift_board"


def ensure_read_models():
    ShiftBoardRow.__table__.create(db.engine, checkfirst=True)
    ProjectorCheckpoint.__table__.create(db.engine, checkfirst=True)
    ShiftRequestRow.__table__.create(db.engine, checkfirst=True)
    AvailabilityConstraintRow.__table__.create(db.engine, checkfirst=True)
    EmployeeHoursRow.__table__.create(db.engine, checkfirst=True)
    ApprovedAssignmentRow.__table__.create(db.engine, checkfirst=True)


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
        elif event.event_type == AVAILABILITY_CONSTRAINT_SUBMITTED:
            _apply_availability(event)
        elif event.event_type == ASSIGNMENT_APPROVED:
            _apply_assignment_approved(event)
        elif event.event_type == SHIFT_CANCELLED:
            _apply_shift_cancelled(event)
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
        replacement_employee_id=payload.get("replacement_employee_id"),
        replacement_name=payload.get("replacement_name"),
    )
    db.session.add(row)
    if request_type == "assignment":
        shift = db.session.get(ShiftBoardRow, payload["shift_id"])
        if shift is not None:
            shift.waiting_candidates = (shift.waiting_candidates or 0) + 1


def _apply_availability(event):
    payload = json.loads(event.payload_json)
    db.session.add(
        AvailabilityConstraintRow(
            constraint_id=event.aggregate_id,
            employee_id=payload["employee_id"],
            start_date=date.fromisoformat(payload["start_date"]),
            end_date=date.fromisoformat(payload["end_date"]),
            constraint_type=payload["constraint_type"],
            note=payload.get("note") or "",
        )
    )


def _apply_assignment_approved(event):
    payload = json.loads(event.payload_json)
    shift = db.session.get(ShiftBoardRow, payload.get("shift_id"))
    if shift is not None and shift.status == "cancelled":
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


def _refresh_status(row):
    if row.assigned_count <= 0:
        row.assigned_count = 0
        row.status = "open"
    elif row.assigned_count < row.required_headcount:
        row.status = "partial"
    else:
        row.status = "staffed"
