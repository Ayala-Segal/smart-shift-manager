import json
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.auth.models import ROLE_EMPLOYEE, ROLE_MANAGER, User, professional_roles_match
from app.commands.define_shift import CommandRejected
from app.event_store import (
    AGGREGATE_ASSIGNMENT_REQUEST,
    AGGREGATE_EMPLOYEE_AVAILABILITY,
    AGGREGATE_SHIFT,
    AGGREGATE_SWAP_REQUEST,
    ASSIGNMENT_REQUESTED,
    AVAILABILITY_CONSTRAINT_SUBMITTED,
    AVAILABILITY_CONSTRAINT_WITHDRAWN,
    SHIFT_DEFINED,
    SWAP_COLLEAGUE_APPROVED,
    SWAP_COLLEAGUE_REJECTED,
    SWAP_REQUESTED,
    Event,
    append_event,
)
from app.extensions import db
from app.tools.conflicts import (
    CONSTRAINT_REASON_PREFIXES,
    NOTE_LIMITS,
    check_availability_conflicts,
    shift_matches_label,
)
from app.tools.schedule import parse_minutes

ISRAEL = ZoneInfo("Asia/Jerusalem")
WAITING = "Waiting for agent evaluation"
WAITING_FOR_EMPLOYEE = "Waiting for employee approval"
CONSTRAINT_TYPES = frozenset({"unavailable", "prefer", "prefer not"})
CONSTRAINT_CONFLICT_PROMPT = "This conflicts with your constraints. Do you still want to proceed?"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def submit_availability(
    *,
    actor_id,
    actor_role,
    constraint_type,
    note="",
    shift_date=None,
    start_date=None,
    end_date=None,
    weekdays=None,
    time_start="",
    time_end="",
    nights=False,
    shabbat_eves=False,
    holidays=False,
    constraint_id=None,
):
    if actor_role != ROLE_EMPLOYEE:
        raise CommandRejected("Only an employee can submit an availability constraint.")
    if constraint_type not in CONSTRAINT_TYPES:
        raise CommandRejected("Constraint type must be unavailable, prefer, or prefer not.")
    stored_note = note or ""
    if len(stored_note) > 300:
        raise CommandRejected("The note must be at most 300 characters.")
    start, end = _constraint_range(shift_date, start_date, end_date)
    clock_start, clock_end = _clock_pair(time_start, time_end)
    replacing = (constraint_id or "").strip()
    if replacing:
        _owned_constraint(replacing, actor_id)
        constraint_id = replacing
    else:
        constraint_id = "constraint-" + uuid4().hex[:8]
    return append_event(
        aggregate_type=AGGREGATE_EMPLOYEE_AVAILABILITY,
        aggregate_id=constraint_id,
        event_type=AVAILABILITY_CONSTRAINT_SUBMITTED,
        payload={
            "employee_id": actor_id,
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
            "constraint_type": constraint_type,
            "note": stored_note,
            "weekdays": _weekdays(weekdays),
            "time_start": clock_start,
            "time_end": clock_end,
            "nights": bool(nights),
            "shabbat_eves": bool(shabbat_eves),
            "holidays": bool(holidays),
        },
        actor_id=actor_id,
    )


def withdraw_availability(*, actor_id, actor_role, constraint_id):
    if actor_role != ROLE_EMPLOYEE:
        raise CommandRejected("Only an employee can cancel an availability constraint.")
    target = (constraint_id or "").strip()
    if not target:
        raise CommandRejected("The constraint does not exist.")
    _owned_constraint(target, actor_id)
    return append_event(
        aggregate_type=AGGREGATE_EMPLOYEE_AVAILABILITY,
        aggregate_id=target,
        event_type=AVAILABILITY_CONSTRAINT_WITHDRAWN,
        payload={"employee_id": actor_id},
        actor_id=actor_id,
    )


def request_assignment(*, actor_id, actor_role, shift_id, note="", today=None, confirm_constraints=False):
    if actor_role != ROLE_EMPLOYEE:
        raise CommandRejected("Only an employee can request an assignment.")
    return _queue_assignment(
        employee_id=actor_id,
        actor_id=actor_id,
        shift_id=shift_id,
        note=note,
        today=today,
        confirm_constraints=confirm_constraints,
    )


def _queue_assignment(
    *,
    employee_id,
    actor_id,
    shift_id,
    note,
    today,
    enforce_time_off=False,
    confirm_constraints=False,
):
    employee = _active_employee(employee_id)
    shift = _defined_shift(shift_id)
    if enforce_time_off:
        _reject_time_off(employee_id, shift)
    current_day = today or datetime.now(ISRAEL).date()
    if date.fromisoformat(shift["shift_date"]) < current_day:
        raise CommandRejected("Assignment requests are only accepted for a current or future shift.")
    if shift["status"] not in {"open", "partial"}:
        raise CommandRejected("Assignment requests are only accepted for open or partial shifts.")
    if not professional_roles_match(employee.professional_role, shift["required_role"]):
        raise CommandRejected("Your professional role does not match this shift.")
    if _has_assignment_request(employee_id, shift_id):
        raise CommandRejected("You already requested this shift.")
    stored_note = note or ""
    if len(stored_note) > 300:
        raise CommandRejected("The note must be at most 300 characters.")
    override = False
    if not enforce_time_off and actor_id == employee_id:
        override = _has_constraint_conflict(employee_id, shift)
        if override and not confirm_constraints:
            raise CommandRejected(CONSTRAINT_CONFLICT_PROMPT)
    request_id = "request-" + uuid4().hex[:8]
    payload = {
        "shift_id": shift_id,
        "employee_id": employee_id,
        "full_name": employee.full_name,
        "professional_role": employee.professional_role,
        "note": stored_note,
        "status": WAITING,
        "display_number": _next_display_number(),
    }
    if override:
        payload["constraint_override"] = True
    return append_event(
        aggregate_type=AGGREGATE_ASSIGNMENT_REQUEST,
        aggregate_id=request_id,
        event_type=ASSIGNMENT_REQUESTED,
        payload=payload,
        actor_id=actor_id,
    )


def register_employee(*, actor_id, actor_role, shift_id, employee_id, note="", today=None):
    """A manager places an employee on the request queue. The agent evaluates next."""
    if actor_role != ROLE_MANAGER:
        raise CommandRejected("Only a manager can register an employee for a shift.")
    return _queue_assignment(
        employee_id=employee_id,
        actor_id=actor_id,
        shift_id=shift_id,
        note=note,
        today=today,
        enforce_time_off=True,
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
    if not professional_roles_match(employee.professional_role, shift["required_role"]) or not professional_roles_match(replacement.professional_role, shift["required_role"]):
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
            "status": WAITING_FOR_EMPLOYEE,
            "display_number": _next_display_number(),
        },
        actor_id=actor_id,
    )


def respond_to_swap(*, actor_id, actor_role, request_id, action):
    """The employee named on a swap accepts or declines it. The manager decides later."""
    if actor_role != ROLE_EMPLOYEE:
        raise CommandRejected("Only an employee can respond to a swap.")
    choice = (action or "").strip().lower()
    if choice not in {"approve", "reject"}:
        raise CommandRejected("Choose approve or reject.")
    events = (
        db.session.query(Event)
        .filter(Event.aggregate_id == request_id)
        .order_by(Event.version)
        .all()
    )
    requested = next((event for event in events if event.event_type == SWAP_REQUESTED), None)
    if requested is None:
        raise CommandRejected("The swap request does not exist.")
    if any(event.event_type in {SWAP_COLLEAGUE_APPROVED, SWAP_COLLEAGUE_REJECTED} for event in events):
        raise CommandRejected("This swap already has an employee response.")
    payload = json.loads(requested.payload_json)
    if payload.get("replacement_employee_id") != actor_id:
        raise CommandRejected("Only the selected employee can respond to this swap.")
    event_type = SWAP_COLLEAGUE_APPROVED if choice == "approve" else SWAP_COLLEAGUE_REJECTED
    return append_event(
        aggregate_type=AGGREGATE_SWAP_REQUEST,
        aggregate_id=request_id,
        event_type=event_type,
        payload={
            "shift_id": payload["shift_id"],
            "employee_id": payload["employee_id"],
            "replacement_employee_id": actor_id,
            "display_number": payload.get("display_number"),
        },
        actor_id=actor_id,
    )


def _next_display_number():
    return (
        db.session.query(Event)
        .filter(Event.event_type.in_([ASSIGNMENT_REQUESTED, SWAP_REQUESTED]))
        .count()
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


def _has_constraint_conflict(employee_id, shift):
    from app.agent.cycle import apply_constraint_notes
    from app.agent.ollama import classify_constraint_note

    block = check_availability_conflicts(
        employee_id,
        shift["shift_date"],
        shift["start_time"],
        shift["end_time"],
    )
    if not block.get("ok"):
        raise CommandRejected("The availability check is unavailable.")
    apply_constraint_notes(
        block,
        shift_date=shift["shift_date"],
        start_time=shift["start_time"],
        end_time=shift["end_time"],
        holiday=False,
        classify=classify_constraint_note,
    )
    reasons = block["result"]["reasons"]
    return any(str(reason).startswith(CONSTRAINT_REASON_PREFIXES) for reason in reasons)


def _reject_time_off(employee_id, shift):
    from app.agent.cycle import apply_constraint_notes
    from app.agent.ollama import classify_constraint_note

    block = check_availability_conflicts(
        employee_id,
        shift["shift_date"],
        shift["start_time"],
        shift["end_time"],
    )
    if not block.get("ok"):
        raise CommandRejected("The availability check is unavailable.")
    apply_constraint_notes(
        block,
        shift_date=shift["shift_date"],
        start_time=shift["start_time"],
        end_time=shift["end_time"],
        holiday=False,
        classify=classify_constraint_note,
    )
    for reason in block["result"]["reasons"]:
        if reason.startswith("The employee is unavailable"):
            raise CommandRejected(reason)
    noted = {
        "nights": "The employee note limits night shifts.",
        "shabbat_eve": "The employee note limits Shabbat eves.",
        "holiday": "The employee note limits holidays.",
    }
    for item in block["result"].get("constraint_notes") or []:
        label = item.get("label")
        if item.get("constraint_type") != "unavailable" or label not in NOTE_LIMITS:
            continue
        if shift_matches_label(
            label,
            shift["shift_date"],
            shift["start_time"],
            shift["end_time"],
            False,
        ):
            raise CommandRejected(noted[label])


def _owned_constraint(constraint_id, actor_id):
    events = (
        db.session.query(Event)
        .filter(
            Event.aggregate_type == AGGREGATE_EMPLOYEE_AVAILABILITY,
            Event.aggregate_id == constraint_id,
        )
        .order_by(Event.version)
        .all()
    )
    if not events:
        raise CommandRejected("The constraint does not exist.")
    current = None
    withdrawn = False
    for event in events:
        if event.event_type == AVAILABILITY_CONSTRAINT_SUBMITTED:
            current = json.loads(event.payload_json)
            withdrawn = False
        elif event.event_type == AVAILABILITY_CONSTRAINT_WITHDRAWN:
            withdrawn = True
    if current is None or current.get("employee_id") != actor_id:
        raise CommandRejected("The constraint does not exist.")
    if withdrawn:
        raise CommandRejected("The constraint is already cancelled.")
    return current


def _weekdays(value):
    if not value:
        return ""
    if isinstance(value, str):
        parts = [item.strip().lower() for item in value.split(",") if item.strip()]
    else:
        parts = [str(item).strip().lower() for item in value if str(item).strip()]
    unknown = [item for item in parts if item not in WEEKDAYS]
    if unknown:
        raise CommandRejected("Weekdays must be mon, tue, wed, thu, fri, sat, or sun.")
    return ",".join(day for day in WEEKDAYS if day in parts)


def _clock_pair(start, end):
    clock_start = (start or "").strip()
    clock_end = (end or "").strip()
    if not clock_start and not clock_end:
        return "", ""
    if (
        parse_minutes(clock_start) is None
        or parse_minutes(clock_end) is None
        or parse_minutes(clock_start) == parse_minutes(clock_end)
    ):
        raise CommandRejected("Enter both a start time and an end time.")
    return clock_start[:5], clock_end[:5]


def _constraint_range(shift_date, start_date, end_date):
    if shift_date:
        day = _parse_date(shift_date, "date")
        return day, day
    if not start_date and not end_date:
        return None, None
    if not start_date or not end_date:
        raise CommandRejected("Provide both a start date and an end date.")
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
