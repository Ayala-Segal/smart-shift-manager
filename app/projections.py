import json
from datetime import date

from app.event_store import (
    ASSIGNMENT_REQUESTED,
    AVAILABILITY_CONSTRAINT_SUBMITTED,
    SHIFT_DEFINED,
    SWAP_REQUESTED,
    Event,
)
from app.extensions import db
from app.read_models import (
    AvailabilityConstraintRow,
    ProjectorCheckpoint,
    ShiftBoardRow,
    ShiftRequestRow,
)

SHIFT_BOARD_PROJECTOR = "shift_board"


def ensure_read_models():
    ShiftBoardRow.__table__.create(db.engine, checkfirst=True)
    ProjectorCheckpoint.__table__.create(db.engine, checkfirst=True)
    ShiftRequestRow.__table__.create(db.engine, checkfirst=True)
    AvailabilityConstraintRow.__table__.create(db.engine, checkfirst=True)


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
