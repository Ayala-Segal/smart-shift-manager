from datetime import date, timedelta

from app.auth.models import ROLE_EMPLOYEE
from app.extensions import db
from app.read_models import ShiftBoardRow, ShiftRequestRow

OPEN_STATUSES = ("open", "partial")


def get_shift_board(start=None, end=None, role=None, statuses=None):
    query = db.session.query(ShiftBoardRow)
    if start is not None:
        query = query.filter(ShiftBoardRow.shift_date >= start)
    if end is not None:
        query = query.filter(ShiftBoardRow.shift_date <= end)
    if role:
        query = query.filter(ShiftBoardRow.required_role == role)
    if statuses:
        query = query.filter(ShiftBoardRow.status.in_(statuses))
    return query.order_by(
        ShiftBoardRow.shift_date, ShiftBoardRow.start_time, ShiftBoardRow.shift_id
    ).all()


def board_filters_from_request(args, viewer):
    mode = (args.get("mode") or "").strip().lower()
    anchor = args.get("date")
    start = _optional_date(args.get("start"), "start")
    end = _optional_date(args.get("end"), "end")
    if mode:
        if mode not in {"week", "month"}:
            raise ValueError("Mode must be week or month.")
        if not anchor:
            raise ValueError("A date is required when mode is set.")
        start, end = _mode_range(mode, _required_date(anchor, "date"))
    role = (args.get("role") or "").strip() or None
    status = (args.get("status") or "").strip() or None
    statuses = [status] if status else None
    open_only = (args.get("open_only") or "").strip() in {"1", "true", "yes"}
    if open_only:
        statuses = list(OPEN_STATUSES)
        if viewer.role == ROLE_EMPLOYEE:
            role = viewer.professional_role
    return {"start": start, "end": end, "role": role, "statuses": statuses}


def requests_for_shift(shift_id):
    return (
        db.session.query(ShiftRequestRow)
        .filter(ShiftRequestRow.shift_id == shift_id)
        .order_by(ShiftRequestRow.request_id)
        .all()
    )


def shift_board_payload(rows):
    payload = {
        "shifts": [_shift_summary(row) for row in rows],
    }
    if not rows:
        payload["message"] = "No shifts in the selected range"
    return payload


def shift_detail_payload(row, requests, viewer):
    visible = []
    for item in requests:
        entry = {
            "request_id": item.request_id,
            "request_type": item.request_type,
            "professional_role": item.professional_role,
            "status": item.status,
        }
        if viewer.role == ROLE_EMPLOYEE:
            entry["first_name"] = item.full_name.split()[0]
            if item.employee_id == viewer.user_id and item.note:
                entry["note"] = item.note
        else:
            entry["full_name"] = item.full_name
            entry["employee_id"] = item.employee_id
            entry["note"] = item.note
            if item.replacement_employee_id:
                entry["replacement_employee_id"] = item.replacement_employee_id
                entry["replacement_name"] = item.replacement_name
        visible.append(entry)
    return {"shift": _shift_summary(row), "registered": visible}


def _shift_summary(row):
    return {
        "shift_id": row.shift_id,
        "shift_date": row.shift_date.isoformat(),
        "weekday": row.weekday,
        "start_time": row.start_time,
        "end_time": row.end_time,
        "required_role": row.required_role,
        "assigned_count": row.assigned_count,
        "required_headcount": row.required_headcount,
        "status": row.status,
        "waiting_candidates": row.waiting_candidates,
        "note": row.note,
    }


def _mode_range(mode, anchor):
    if mode == "week":
        days_since_sunday = (anchor.weekday() + 1) % 7
        start = anchor - timedelta(days=days_since_sunday)
        return start, start + timedelta(days=6)
    start = anchor.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    return start, next_month - timedelta(days=1)


def _optional_date(value, label):
    if not value:
        return None
    return _required_date(value, label)


def _required_date(value, label):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"The {label} date must use YYYY-MM-DD.") from error
