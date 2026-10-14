from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.auth.models import ROLE_EMPLOYEE, User
from app.extensions import db
from app.projections import WAITING_FOR_AGENT, WAITING_FOR_MANAGER, ensure_read_models
from app.queries.board import get_shift_board
from app.read_models import (
    ApprovedAssignmentRow,
    AvailabilityConstraintRow,
    EmployeeHoursRow,
    RequestRecommendationRow,
    ShiftBoardRow,
    ShiftRequestRow,
)
from app.tools.schedule import week_start_sunday

ISRAEL = ZoneInfo("Asia/Jerusalem")
OPEN_REQUESTS = (WAITING_FOR_AGENT, WAITING_FOR_MANAGER)


def manager_dashboard_summary(today=None, start=None, end=None):
    """Count board status from the shift read model. This does not read events."""
    ensure_read_models()
    current = today or datetime.now(ISRAEL).date()
    range_start = start or current
    range_end = end or (range_start + timedelta(days=6))
    rows = get_shift_board(range_start, range_end)
    shift_ids = [row.shift_id for row in rows]
    requests = _requests_for(shift_ids)
    return {
        "start": range_start.isoformat(),
        "end": range_end.isoformat(),
        "open_shifts": _count_status(rows, "open"),
        "partial_shifts": _count_status(rows, "partial"),
        "staffed_shifts": _count_status(rows, "staffed"),
        "waiting_for_manager": _count_status_text(requests, WAITING_FOR_MANAGER),
        "waiting_for_agent": _count_status_text(requests, WAITING_FOR_AGENT),
        "unstaffed": [_unstaffed_row(row) for row in rows if row.status in {"open", "partial"}],
    }


def pending_decisions(start=None, end=None):
    """Requests that have a recommendation and still need a manager decision."""
    ensure_read_models()
    query = db.session.query(ShiftRequestRow).filter(ShiftRequestRow.status == WAITING_FOR_MANAGER)
    rows = query.order_by(ShiftRequestRow.request_id).all()
    if start is not None or end is not None:
        allowed = {row.shift_id for row in get_shift_board(start, end)}
        rows = [row for row in rows if row.shift_id in allowed]
    return [_decision_row(row) for row in rows]


def employee_dashboard(employee_id, today=None):
    ensure_read_models()
    current = today or datetime.now(ISRAEL).date()
    week_start = week_start_sunday(current)
    week_end = week_start + timedelta(days=6)
    hours = db.session.get(EmployeeHoursRow, (employee_id, week_start))
    approved = (
        db.session.query(ApprovedAssignmentRow)
        .filter(
            ApprovedAssignmentRow.employee_id == employee_id,
            ApprovedAssignmentRow.active.is_(True),
            ApprovedAssignmentRow.shift_date >= week_start,
            ApprovedAssignmentRow.shift_date <= week_end,
        )
        .order_by(ApprovedAssignmentRow.shift_date, ApprovedAssignmentRow.start_time)
        .all()
    )
    requests = (
        db.session.query(ShiftRequestRow)
        .filter(ShiftRequestRow.employee_id == employee_id)
        .order_by(ShiftRequestRow.request_id)
        .all()
    )
    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "approved_hours": round(hours.approved_hours, 2) if hours is not None else 0,
        "approved_shifts": [_approved_shift(row) for row in approved],
        "open_requests": [_request_summary(row) for row in requests if row.status in OPEN_REQUESTS],
        "decided_requests": [
            _request_summary(row) for row in requests if row.status not in OPEN_REQUESTS
        ],
    }


def my_approved_shifts(employee_id):
    ensure_read_models()
    rows = (
        db.session.query(ApprovedAssignmentRow)
        .filter(
            ApprovedAssignmentRow.employee_id == employee_id,
            ApprovedAssignmentRow.active.is_(True),
        )
        .order_by(ApprovedAssignmentRow.shift_date, ApprovedAssignmentRow.start_time)
        .all()
    )
    return [_approved_shift(row) for row in rows]


def other_employees(user_id):
    users = (
        db.session.query(User)
        .filter(
            User.role == ROLE_EMPLOYEE,
            User.is_active.is_(True),
            User.user_id != user_id,
        )
        .order_by(User.full_name)
        .all()
    )
    return [
        {
            "user_id": user.user_id,
            "full_name": user.full_name,
            "professional_role": user.professional_role,
        }
        for user in users
    ]


def constraints_for(employee_id):
    ensure_read_models()
    rows = (
        db.session.query(AvailabilityConstraintRow)
        .filter(AvailabilityConstraintRow.employee_id == employee_id)
        .order_by(AvailabilityConstraintRow.start_date, AvailabilityConstraintRow.constraint_id)
        .all()
    )
    return [
        {
            "constraint_id": row.constraint_id,
            "start_date": row.start_date.isoformat(),
            "end_date": row.end_date.isoformat(),
            "constraint_type": row.constraint_type,
            "note": row.note,
        }
        for row in rows
    ]


def _requests_for(shift_ids):
    if not shift_ids:
        return []
    return (
        db.session.query(ShiftRequestRow)
        .filter(ShiftRequestRow.shift_id.in_(shift_ids))
        .all()
    )


def _count_status(rows, status):
    return sum(1 for row in rows if row.status == status)


def _count_status_text(rows, status):
    return sum(1 for row in rows if row.status == status)


def _unstaffed_row(row):
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
    }


def _decision_row(row):
    recommendation = db.session.get(RequestRecommendationRow, row.request_id)
    shift = db.session.get(ShiftBoardRow, row.shift_id)
    return {
        "request_id": row.request_id,
        "request_type": row.request_type,
        "employee_id": row.employee_id,
        "full_name": row.full_name,
        "shift_id": row.shift_id,
        "shift_date": shift.shift_date.isoformat() if shift is not None else "",
        "weekday": shift.weekday if shift is not None else "",
        "start_time": shift.start_time if shift is not None else "",
        "end_time": shift.end_time if shift is not None else "",
        "required_role": shift.required_role if shift is not None else row.professional_role,
        "recommendation": recommendation.decision if recommendation is not None else "",
        "rationale": recommendation.rationale if recommendation is not None else "",
        "hours_warning": bool(recommendation.hours_warning) if recommendation is not None else False,
        "replacement_name": row.replacement_name or "",
    }


def _approved_shift(row):
    shift = db.session.get(ShiftBoardRow, row.shift_id)
    return {
        "shift_id": row.shift_id,
        "shift_date": row.shift_date.isoformat(),
        "weekday": shift.weekday if shift is not None else row.shift_date.strftime("%A"),
        "start_time": row.start_time,
        "end_time": row.end_time,
        "required_role": shift.required_role if shift is not None else "",
        "hours": row.hours,
    }


def _request_summary(row):
    shift = db.session.get(ShiftBoardRow, row.shift_id)
    return {
        "request_id": row.request_id,
        "request_type": row.request_type,
        "status": row.status,
        "shift_id": row.shift_id,
        "shift_date": shift.shift_date.isoformat() if shift is not None else "",
        "start_time": shift.start_time if shift is not None else "",
        "end_time": shift.end_time if shift is not None else "",
        "decision_note": row.decision_note,
    }
