from app.extensions import db
from app.projections import ensure_read_models
from app.read_models import EmployeeHoursRow
from app.tools.contract import error, ok
from app.tools.schedule import WEEKLY_LIMIT, parse_date


def calculate_weekly_hours(employee_id, week_start, additional_shift_hours):
    """Read approved hours and add the requested shift. This does not write events."""
    if not str(employee_id or "").strip():
        return error("invalid_employee")
    start = parse_date(week_start)
    if start is None:
        return error("invalid_week_start")
    if start.weekday() != 6:
        return error("invalid_week_start")
    additional = _hours(additional_shift_hours)
    if additional is None:
        return error("invalid_hours")
    ensure_read_models()
    row = (
        db.session.query(EmployeeHoursRow)
        .filter_by(employee_id=employee_id, week_start=start)
        .one_or_none()
    )
    approved = round(row.approved_hours, 2) if row is not None else 0.0
    projected = round(approved + additional, 2)
    overflow = round(max(0.0, projected - WEEKLY_LIMIT), 2)
    return ok(
        {
            "approved_hours": approved,
            "projected_hours": projected,
            "weekly_limit": WEEKLY_LIMIT,
            "exceeds_limit": projected > WEEKLY_LIMIT,
            "overflow_hours": overflow,
        }
    )


def _hours(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return round(number, 2)
