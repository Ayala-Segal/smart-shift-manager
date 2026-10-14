import json

from app.auth.models import User
from app.event_store import (
    ASSIGNMENT_APPROVED,
    ASSIGNMENT_REJECTED,
    NOTIFICATION_FAILED,
    NOTIFICATION_SENT,
    SHIFT_CANCELLED,
    SWAP_APPROVED,
    SWAP_REJECTED,
    Event,
    append_event,
)
from app.extensions import db
from app.projections import project_shift_board
from app.read_models import ApprovedAssignmentRow, ShiftBoardRow
from app.tools.gmail import send_message

DECISION_TYPES = (
    ASSIGNMENT_APPROVED,
    ASSIGNMENT_REJECTED,
    SWAP_APPROVED,
    SWAP_REJECTED,
    SHIFT_CANCELLED,
)
SUBJECTS = {
    ASSIGNMENT_APPROVED: "Shift assignment approved",
    ASSIGNMENT_REJECTED: "Shift assignment rejected",
    SWAP_APPROVED: "Shift swap approved",
    SWAP_REJECTED: "Shift swap rejected",
    SHIFT_CANCELLED: "Shift cancelled",
}


def dispatch_notifications(transport=None):
    """Send one email per decision that affects an employee, then record the result."""
    project_shift_board()
    sent = 0
    seen = _already_notified()
    decisions = (
        db.session.query(Event)
        .filter(Event.event_type.in_(DECISION_TYPES))
        .order_by(Event.event_id)
        .all()
    )
    for decision in decisions:
        payload = json.loads(decision.payload_json)
        for employee_id in _recipients(decision, payload):
            key = (decision.event_id, employee_id)
            if key in seen:
                continue
            employee = db.session.get(User, employee_id)
            subject = SUBJECTS[decision.event_type]
            if employee is None or not employee.email:
                _record(decision, employee_id, subject, "missing_address")
                seen.add(key)
                continue
            if getattr(employee, "notify_email", True) is False:
                _record(decision, employee_id, subject, "notifications_disabled")
                seen.add(key)
                continue
            result = send_message(
                to=employee.email,
                subject=subject,
                body=_body(decision, payload),
                transport=transport,
            )
            error_code = None if result.get("ok") else result.get("error_code") or "gmail_unavailable"
            _record(decision, employee_id, subject, error_code)
            seen.add(key)
            if result.get("ok"):
                sent += 1
    return sent


def _recipients(decision, payload):
    if decision.event_type == SHIFT_CANCELLED:
        rows = (
            db.session.query(ApprovedAssignmentRow)
            .filter(ApprovedAssignmentRow.shift_id == decision.aggregate_id)
            .all()
        )
        return [row.employee_id for row in rows]
    recipients = []
    if payload.get("employee_id"):
        recipients.append(payload["employee_id"])
    if decision.event_type == SWAP_APPROVED and payload.get("replacement_employee_id"):
        recipients.append(payload["replacement_employee_id"])
    return recipients


def _body(decision, payload):
    when = payload.get("shift_date") or ""
    hours = ""
    if payload.get("start_time") and payload.get("end_time"):
        hours = "{0}-{1}".format(payload["start_time"], payload["end_time"])
    event_type = decision.event_type
    if event_type == ASSIGNMENT_APPROVED:
        return "Your shift assignment on {0} {1} was approved.".format(when, hours).strip()
    if event_type == ASSIGNMENT_REJECTED:
        return "Your shift assignment request on {0} was rejected.".format(when).strip()
    if event_type == SWAP_APPROVED:
        return "Your shift swap on {0} was approved.".format(when).strip()
    if event_type == SWAP_REJECTED:
        return "Your shift swap request on {0} was rejected.".format(when).strip()
    shift = db.session.get(ShiftBoardRow, decision.aggregate_id)
    if shift is not None:
        return "The shift on {0}, {1}-{2}, was cancelled.".format(
            shift.shift_date.isoformat(),
            shift.start_time,
            shift.end_time,
        )
    return "A shift was cancelled."


def _already_notified():
    seen = set()
    events = (
        db.session.query(Event)
        .filter(Event.event_type.in_((NOTIFICATION_SENT, NOTIFICATION_FAILED)))
        .all()
    )
    for event in events:
        payload = json.loads(event.payload_json)
        if payload.get("decision_event_id") and payload.get("employee_id"):
            seen.add((payload["decision_event_id"], payload["employee_id"]))
    return seen


def _record(decision, employee_id, subject, error_code):
    event_type = NOTIFICATION_SENT if error_code is None else NOTIFICATION_FAILED
    body = {
        "decision_event_id": decision.event_id,
        "employee_id": employee_id,
        "subject": subject,
    }
    if error_code:
        body["error_code"] = error_code
    append_event(
        aggregate_type="Notification",
        aggregate_id="notification-{0}-{1}".format(decision.event_id, employee_id),
        event_type=event_type,
        payload=body,
        actor_id="notifications",
    )
