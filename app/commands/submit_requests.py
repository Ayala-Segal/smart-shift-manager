import json
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.auth.models import ROLE_EMPLOYEE, User
from app.commands.define_shift import CommandRejected
from app.event_store import (
    AGGREGATE_ASSIGNMENT_REQUEST,
    AGGREGATE_EMPLOYEE_AVAILABILITY,
    AGGREGATE_SHIFT,
    AGGREGATE_SWAP_REQUEST,
    ASSIGNMENT_REQUESTED,
    AVAILABILITY_CONSTRAINT_SUBMITTED,
    SHIFT_DEFINED,
    SWAP_REQUESTED,
    Event,
    append_event,
)
from app.extensions import db
from app.tools.conflicts import check_availability_conflicts

ISRAEL = ZoneInfo("Asia/Jerusalem")
WAITING = "Waiting for agent evaluation"
CONSTRAINT_TYPES = frozenset({"unavailable", "prefer", "prefer not"})


def submit_availability(*, actor_id, actor_role, constraint_type, note="", shift_date=None, start_date=None, end_date=None):
    if actor_role != ROLE_EMPLOYEE:
        raise CommandRejected("Only an employee can submit an availability constraint.")
    if constraint_type not in CONSTRAINT_TYPES:
        raise CommandRejected("Constraint type must be unavailable, prefer, or prefer not.")
    stored_note = note or ""
    if len(stored_note) > 300:
        raise CommandRejected("The note must be at most 300 characters.")
    start, end = _constraint_range(shift_date, start_date, end_date)
    constraint_id = "constraint-" + uuid4().hex[:8]
    return append_event(
        aggregate_type=AGGREGATE_EMPLOYEE_AVAILABILITY,
        aggregate_id=constraint_id,
        event_type=AVAILABILITY_CONSTRAINT_SUBMITTED,
        payload={
            "employee_id": actor_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "constraint_type": constraint_type,
            "note": stored_note,
        },
        actor_id=actor_id,
    )


def request_assignment(*, actor_id, actor_role, shift_id, note=""):
    if actor_role != ROLE_EMPLOYEE:
        raise CommandRejected("Only an employee can request an assignment.")
    employee = _active_employee(actor_id)
    shift = _defined_shift(shift_id)
    if shift["status"] not in {"open", "partial"}:
        raise CommandRejected("Assignment requests are only accepted for open or partial shifts.")
    if employee.professional_role != shift["required_role"]:
        raise CommandRejected("Your professional role does not match this shift.")
    if _has_assignment_request(actor_id, shift_id):
        raise CommandRejected("You already requested this shift.")
    stored_note = note or ""
    if len(stored_note) > 300:
        raise CommandRejected("The note must be at most 300 characters.")
    request_id = "request-" + uuid4().hex[:8]
    return append_event(
        aggregate_type=AGGREGATE_ASSIGNMENT_REQUEST,
        aggregate_id=request_id,
        event_type=ASSIGNMENT_REQUESTED,
        payload={
            "shift_id": shift_id,
            "employee_id": actor_id,
            "full_name": employee.full_name,
            "professional_role": employee.professional_role,
            "note": stored_note,
            "status": WAITING,
        },
        actor_id=actor_id,
    )


def request_swap(*, actor_id, actor_role, shift_id, replacement_employee_id, note="", today=None):
    if actor_role != ROLE_EMPLOYEE:
        raise CommandRejected("Only an employee can request a swap.")
    employee = _active_employee(actor_id)
    replacement = _active_employee(replacement_employee_id)
    if replacement.user_id == actor_id:
        raise CommandRejected("Choose a different employee for the swap.")
    shift = _defined_shift(shift_id)
    current_day = today or datetime.now(ISRAEL).date()
    if date.fromisoformat(shift["shift_date"]) < current_day:
        raise CommandRejected("A swap is only possible for a future shift.")
    if employee.professional_role != shift["required_role"] or replacement.professional_role != shift["required_role"]:
        raise CommandRejected("Both employees must match the shift role.")
    if not _has_assignment_request(actor_id, shift_id):
        raise CommandRejected("You can swap a shift only after you have requested it.")
    block = check_availability_conflicts(
        replacement.user_id,
        shift["shift_date"],
        shift["start_time"],
        shift["end_time"],
    )
    if not block.get("ok"):
        raise CommandRejected("The availability check is unavailable.")
    if block["result"]["hard_block"]:
        raise CommandRejected(block["result"]["reasons"][0])
    stored_note = note or ""
    if len(stored_note) > 300:
        raise CommandRejected("The note must be at most 300 characters.")
    request_id = "swap-" + uuid4().hex[:8]
    return append_event(
        aggregate_type=AGGREGATE_SWAP_REQUEST,
        aggregate_id=request_id,
        event_type=SWAP_REQUESTED,
        payload={
            "shift_id": shift_id,
            "employee_id": actor_id,
            "full_name": employee.full_name,
            "professional_role": employee.professional_role,
            "replacement_employee_id": replacement.user_id,
            "replacement_name": replacement.full_name,
            "note": stored_note,
            "status": WAITING,
        },
        actor_id=actor_id,
    )


def _active_employee(user_id):
    user = db.session.get(User, user_id)
    if user is None or not user.is_active or user.role != ROLE_EMPLOYEE:
        raise CommandRejected("The employee account is not available.")
    return user


def _defined_shift(shift_id):
    events = (
        db.session.query(Event)
        .filter(Event.aggregate_type == AGGREGATE_SHIFT, Event.aggregate_id == shift_id)
        .order_by(Event.version)
        .all()
    )
    defined = next((event for event in events if event.event_type == SHIFT_DEFINED), None)
    if defined is None:
        raise CommandRejected("The shift does not exist.")
    payload = json.loads(defined.payload_json)
    payload["status"] = "open"
    payload["shift_id"] = shift_id
    return payload


def _has_assignment_request(employee_id, shift_id):
    events = (
        db.session.query(Event)
        .filter(
            Event.aggregate_type == AGGREGATE_ASSIGNMENT_REQUEST,
            Event.event_type == ASSIGNMENT_REQUESTED,
        )
        .all()
    )
    for event in events:
        payload = json.loads(event.payload_json)
        if payload.get("employee_id") == employee_id and payload.get("shift_id") == shift_id:
            return True
    return False


def _constraint_range(shift_date, start_date, end_date):
    if shift_date:
        day = _parse_date(shift_date, "date")
        return day, day
    if not start_date or not end_date:
        raise CommandRejected("Provide a date, or both a start date and an end date.")
    start = _parse_date(start_date, "start date")
    end = _parse_date(end_date, "end date")
    if end < start:
        raise CommandRejected("The end date must be on or after the start date.")
    return start, end


def _parse_date(value, label):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise CommandRejected(f"The {label} must use YYYY-MM-DD.") from error
