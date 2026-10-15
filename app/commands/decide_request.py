import json

from app.auth.models import ROLE_MANAGER
from app.commands.define_shift import CommandRejected
from app.event_store import (
    AGENT_EVALUATION_COMPLETED,
    AGGREGATE_ASSIGNMENT_REQUEST,
    AGGREGATE_SHIFT,
    AGGREGATE_SWAP_REQUEST,
    ASSIGNMENT_APPROVED,
    ASSIGNMENT_REJECTED,
    ASSIGNMENT_REQUESTED,
    SHIFT_CANCELLED,
    SHIFT_DEFINED,
    SWAP_APPROVED,
    SWAP_COLLEAGUE_APPROVED,
    SWAP_COLLEAGUE_REJECTED,
    SWAP_REJECTED,
    SWAP_REQUESTED,
    Event,
    append_event,
)
from app.extensions import db
from app.tools.conflicts import check_availability_conflicts

DECISION_EVENTS = frozenset(
    {ASSIGNMENT_APPROVED, ASSIGNMENT_REJECTED, SWAP_APPROVED, SWAP_REJECTED}
)


def decide_request(*, actor_id, actor_role, request_id, action, note="", expected_type=None):
    """Append one manager decision. Read models stay with the projector."""
    if actor_role != ROLE_MANAGER:
        raise CommandRejected("Only a manager can decide a request.")
    choice = (action or "").strip().lower()
    if choice not in {"approve", "reject"}:
        raise CommandRejected("Choose approve or reject.")
    stored_note = note or ""
    if len(stored_note) > 300:
        raise CommandRejected("The note must be at most 300 characters.")
    if choice == "reject" and not stored_note.strip():
        raise CommandRejected("A rejection needs a note.")

    history = _history(request_id)
    requested = history[0]
    if requested.event_type == ASSIGNMENT_REQUESTED:
        request_type = "assignment"
    elif requested.event_type == SWAP_REQUESTED:
        request_type = "swap"
    else:
        raise CommandRejected("The request does not exist.")
    if expected_type and expected_type != request_type:
        label = "assignment" if expected_type == "assignment" else "swap"
        raise CommandRejected("This request is not a {0} request.".format(label))
    if any(event.event_type in DECISION_EVENTS for event in history):
        raise CommandRejected("This request already has a decision.")
    if request_type == "swap":
        if any(event.event_type == SWAP_COLLEAGUE_REJECTED for event in history):
            raise CommandRejected("The other employee declined this swap.")
        if not any(event.event_type == SWAP_COLLEAGUE_APPROVED for event in history):
            raise CommandRejected(
                "The other employee has to approve the swap before a manager can decide."
            )
    if not any(event.event_type == AGENT_EVALUATION_COMPLETED for event in history):
        raise CommandRejected("A request cannot be decided before a recommendation exists.")

    payload = json.loads(requested.payload_json)
    shift = _shift_snapshot(payload["shift_id"])
    if choice == "approve":
        subject_id = (
            payload["replacement_employee_id"] if request_type == "swap" else payload["employee_id"]
        )
        self_scheduled = request_type == "assignment" and requested.actor_id == payload.get("employee_id")
        _reject_overlap(subject_id, shift, time_off=not self_scheduled)

    event_type = {
        ("assignment", "approve"): ASSIGNMENT_APPROVED,
        ("assignment", "reject"): ASSIGNMENT_REJECTED,
        ("swap", "approve"): SWAP_APPROVED,
        ("swap", "reject"): SWAP_REJECTED,
    }[(request_type, choice)]
    body = {
        "shift_id": payload["shift_id"],
        "shift_date": shift["shift_date"],
        "start_time": shift["start_time"],
        "end_time": shift["end_time"],
        "employee_id": payload["employee_id"],
        "manager_note": stored_note.strip(),
    }
    if request_type == "swap":
        body["replacement_employee_id"] = payload["replacement_employee_id"]
    aggregate_type = (
        AGGREGATE_ASSIGNMENT_REQUEST if request_type == "assignment" else AGGREGATE_SWAP_REQUEST
    )
    return append_event(
        aggregate_type=aggregate_type,
        aggregate_id=request_id,
        event_type=event_type,
        payload=body,
        actor_id=actor_id,
    )


def _history(request_id):
    events = (
        db.session.query(Event)
        .filter(Event.aggregate_id == request_id)
        .order_by(Event.version)
        .all()
    )
    if not events:
        raise CommandRejected("The request does not exist.")
    return events


def _shift_snapshot(shift_id):
    events = (
        db.session.query(Event)
        .filter(Event.aggregate_type == AGGREGATE_SHIFT, Event.aggregate_id == shift_id)
        .order_by(Event.version)
        .all()
    )
    defined = None
    cancelled = False
    for event in events:
        if event.event_type == SHIFT_DEFINED:
            defined = json.loads(event.payload_json)
        elif event.event_type == SHIFT_CANCELLED:
            cancelled = True
    if defined is None:
        raise CommandRejected("The shift does not exist.")
    if cancelled:
        raise CommandRejected("The shift is cancelled.")
    return defined


def _reject_overlap(employee_id, shift, *, time_off=False):
    block = check_availability_conflicts(
        employee_id,
        shift["shift_date"],
        shift["start_time"],
        shift["end_time"],
    )
    if not block.get("ok"):
        raise CommandRejected("The availability check is unavailable.")
    for reason in block["result"]["reasons"]:
        if "overlaps" in reason or (time_off and reason.startswith("The employee is unavailable")):
            raise CommandRejected(reason)
