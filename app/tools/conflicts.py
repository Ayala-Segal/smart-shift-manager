from datetime import timedelta

from sqlalchemy import and_, or_

from app.extensions import db
from app.projections import ensure_read_models
from app.read_models import ApprovedAssignmentRow, AvailabilityConstraintRow
from app.tools.contract import error, ok
from app.tools.schedule import REST_HOURS, interval_minutes, parse_date, parse_minutes

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
NOTE_LIMITS = ("nights", "shabbat_eve", "holiday")
CONSTRAINT_REASON_PREFIXES = (
    "The employee is unavailable",
    "The employee prefers not",
    "The employee note limits",
)


def check_availability_conflicts(
    employee_id,
    shift_date,
    start_time,
    end_time,
    holiday=False,
    include_constraints=True,
):
    """Cross-check constraints, overlap, and rest. This does not write events.

    include_constraints is false when the employee requested the shift themselves.
    Overlap and rest still apply.
    """
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
    notes = []
    conflicting_shift_id = None
    proposed = interval_minutes(day, start_time, end_time, day)
    constraints = _constraints(employee_id, day) if include_constraints else []
    for constraint in constraints:
        matched = _schedule_matches(constraint, day, start_time, end_time, holiday)
        if (constraint.note or "").strip():
            notes.append(
                {
                    "note": constraint.note.strip(),
                    "constraint_type": constraint.constraint_type,
                    "matched": matched,
                }
            )
        if not matched:
            continue
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
        "notes": notes,
    }
    if conflicting_shift_id:
        result["conflicting_shift_id"] = conflicting_shift_id
    return ok(result)


def soften_own_constraint_blocks(conflict):
    """An employee can proceed over their own constraints. Overlap and rest stay a reject."""
    result = conflict.setdefault("result", {})
    reasons = list(result.get("reasons") or [])
    other = [item for item in reasons if not str(item).startswith(CONSTRAINT_REASON_PREFIXES)]
    own = [item for item in reasons if str(item).startswith(CONSTRAINT_REASON_PREFIXES)]
    if own and not other:
        result["hard_block"] = False
        result["soft_block"] = True
    return conflict


def shift_matches_label(label, shift_date, start_time, end_time, holiday):
    """True when a classified note describes this shift."""
    day = parse_date(shift_date)
    if day is None or label not in NOTE_LIMITS:
        return False
    if label == "nights":
        return _overlaps_clock_window(day, start_time, end_time, "22:00", "06:00")
    if label == "shabbat_eve":
        start = parse_minutes(start_time)
        return day.weekday() == 4 and start is not None and start >= 16 * 60
    return bool(holiday)


def _schedule_matches(constraint, day, start_time, end_time, holiday):
    weekdays = [item for item in (constraint.weekdays or "").split(",") if item]
    if weekdays and WEEKDAYS[day.weekday()] not in weekdays:
        return False
    if (constraint.time_start or "") and (constraint.time_end or ""):
        if not _overlaps_clock_window(day, start_time, end_time, constraint.time_start, constraint.time_end):
            return False
    if constraint.nights and not _overlaps_clock_window(day, start_time, end_time, "22:00", "06:00"):
        return False
    if constraint.shabbat_eves:
        start = parse_minutes(start_time)
        if day.weekday() != 4 or start is None or start < 16 * 60:
            return False
    if constraint.holidays and not holiday:
        return False
    return True


def _overlaps_clock_window(day, start_time, end_time, window_start, window_end):
    proposed = interval_minutes(day, start_time, end_time, day)
    start = parse_minutes(window_start)
    end = parse_minutes(window_end)
    if proposed is None or start is None or end is None:
        return False
    if end > start:
        return _overlaps(proposed, (start, end))
    return (
        _overlaps(proposed, (start, 24 * 60))
        or _overlaps(proposed, (0, end))
        or _overlaps(proposed, (start, end + 24 * 60))
    )


def _constraints(employee_id, day):
    return (
        db.session.query(AvailabilityConstraintRow)
        .filter(
            AvailabilityConstraintRow.employee_id == employee_id,
            or_(
                and_(
                    AvailabilityConstraintRow.start_date.is_(None),
                    AvailabilityConstraintRow.end_date.is_(None),
                ),
                and_(
                    AvailabilityConstraintRow.start_date <= day,
                    AvailabilityConstraintRow.end_date >= day,
                ),
            ),
        )
        .all()
    )


def _nearby_assignments(employee_id, day):
    return (
        db.session.query(ApprovedAssignmentRow)
        .filter(
            ApprovedAssignmentRow.employee_id == employee_id,
            ApprovedAssignmentRow.active == True,
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
