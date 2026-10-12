from datetime import timedelta

from app.extensions import db
from app.projections import ensure_read_models
from app.read_models import ApprovedAssignmentRow, AvailabilityConstraintRow
from app.tools.contract import error, ok
from app.tools.schedule import REST_HOURS, interval_minutes, parse_date, parse_minutes


def check_availability_conflicts(employee_id, shift_date, start_time, end_time):
    """Cross-check constraints, overlap, and rest. This does not write events."""
    if not str(employee_id or "").strip():
        return error("invalid_employee")
    day = parse_date(shift_date)
    if day is None:
        return error("invalid_date")
    if parse_minutes(start_time) is None or parse_minutes(end_time) is None:
        return error("invalid_time")
    if parse_minutes(start_time) == parse_minutes(end_time):
        return error("invalid_time")
    ensure_read_models()
    reasons = []
    hard_block = False
    soft_block = False
    conflicting_shift_id = None
    proposed = interval_minutes(day, start_time, end_time, day)
    for constraint in _constraints(employee_id, day):
        if constraint.constraint_type == "unavailable":
            hard_block = True
            reasons.append("The employee is unavailable on this date.")
        elif constraint.constraint_type == "prefer not":
            soft_block = True
            reasons.append("The employee prefers not to work on this date.")
    for assignment in _nearby_assignments(employee_id, day):
        other = interval_minutes(
            assignment.shift_date,
            assignment.start_time,
            assignment.end_time,
            day,
        )
        if other is None or _overlaps(proposed, other):
            hard_block = True
            if conflicting_shift_id is None:
                conflicting_shift_id = assignment.shift_id
            reasons.append("The shift overlaps {0}.".format(assignment.shift_id))
            continue
        gap = _rest_gap(proposed, other)
        if gap < REST_HOURS:
            hard_block = True
            reasons.append("Rest between shifts is under 8 hours.")
    result = {
        "hard_block": hard_block,
        "soft_block": soft_block,
        "reasons": reasons,
    }
    if conflicting_shift_id:
        result["conflicting_shift_id"] = conflicting_shift_id
    return ok(result)


def _constraints(employee_id, day):
    return (
        db.session.query(AvailabilityConstraintRow)
        .filter(
            AvailabilityConstraintRow.employee_id == employee_id,
            AvailabilityConstraintRow.start_date <= day,
            AvailabilityConstraintRow.end_date >= day,
        )
        .all()
    )


def _nearby_assignments(employee_id, day):
    return (
        db.session.query(ApprovedAssignmentRow)
        .filter(
            ApprovedAssignmentRow.employee_id == employee_id,
            ApprovedAssignmentRow.active.is_(True),
            ApprovedAssignmentRow.shift_date >= day - timedelta(days=1),
            ApprovedAssignmentRow.shift_date <= day + timedelta(days=1),
        )
        .all()
    )


def _overlaps(left, right):
    return left[0] < right[1] and right[0] < left[1]


def _rest_gap(proposed, other):
    if proposed[0] >= other[1]:
        return (proposed[0] - other[1]) / 60
    return (other[0] - proposed[1]) / 60
