import json
from datetime import date

from app.event_store import SHIFT_DEFINED, Event
from app.extensions import db
from app.read_models import ProjectorCheckpoint, ShiftBoardRow

SHIFT_BOARD_PROJECTOR = "shift_board"


def ensure_read_models():
    ShiftBoardRow.__table__.create(db.engine, checkfirst=True)
    ProjectorCheckpoint.__table__.create(db.engine, checkfirst=True)


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
