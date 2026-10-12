from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.event_store import AGGREGATE_SHIFT, SHIFT_DEFINED, Event, append_event
from app.extensions import db

ISRAEL = ZoneInfo("Asia/Jerusalem")


class CommandRejected(Exception):
    pass


def validate_shift_definition(
    shift_date,
    start_time,
    end_time,
    required_role,
    required_headcount,
    note="",
    crosses_midnight=False,
    today=None,
):
    try:
        parsed_date = date.fromisoformat(shift_date)
    except (TypeError, ValueError) as error:
        raise CommandRejected("The shift date must use YYYY-MM-DD.") from error

    current_day = today or datetime.now(ISRAEL).date()
    if parsed_date < current_day:
        raise CommandRejected("The shift date is in the past.")

    try:
        parsed_start = datetime.strptime(start_time, "%H:%M").time()
        parsed_end = datetime.strptime(end_time, "%H:%M").time()
    except (TypeError, ValueError) as error:
        raise CommandRejected("Shift times must use HH:MM.") from error

    if parsed_end <= parsed_start and not crosses_midnight:
        raise CommandRejected(
            "The end time must be later than the start, or the shift must cross midnight."
        )
    if int(required_headcount) < 1:
        raise CommandRejected("The required headcount must be at least 1.")

    role = (required_role or "").strip()
    if not role:
        raise CommandRejected("The shift needs a required role.")

    stored_note = note or ""
    if len(stored_note) > 300:
        raise CommandRejected("The note must be at most 300 characters.")

    return {
        "shift_date": parsed_date.isoformat(),
        "start_time": parsed_start.strftime("%H:%M"),
        "end_time": parsed_end.strftime("%H:%M"),
        "required_role": role,
        "required_headcount": int(required_headcount),
        "note": stored_note,
        "crosses_midnight": bool(crosses_midnight),
    }


def define_shift(
    *,
    actor_id,
    actor_role,
    shift_date,
    start_time,
    end_time,
    required_role,
    required_headcount,
    note="",
    crosses_midnight=False,
    today=None,
):
    if actor_role != "manager":
        raise CommandRejected("Only a manager can define a shift.")

    payload = validate_shift_definition(
        shift_date,
        start_time,
        end_time,
        required_role,
        required_headcount,
        note=note,
        crosses_midnight=crosses_midnight,
        today=today,
    )
    aggregate_id = "shift-" + uuid4().hex[:8]
    prior = (
        db.session.query(Event)
        .filter(
            Event.aggregate_type == AGGREGATE_SHIFT,
            Event.aggregate_id == aggregate_id,
        )
        .order_by(Event.version)
        .all()
    )
    if prior:
        raise CommandRejected("This shift already has events.")

    return append_event(
        aggregate_type=AGGREGATE_SHIFT,
        aggregate_id=aggregate_id,
        event_type=SHIFT_DEFINED,
        payload=payload,
        actor_id=actor_id,
    )
