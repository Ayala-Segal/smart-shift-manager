import csv
import io
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.auth.models import ROLE_EMPLOYEE, User
from app.extensions import db
from app.read_models import ApprovedAssignmentRow, RequestRecommendationRow, ShiftBoardRow, ShiftRequestRow

OPEN_STATUSES = ("open", "partial")
ISRAEL = ZoneInfo("Asia/Jerusalem")


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
    include_closed = (args.get("include_closed") or "").strip() in {"1", "true", "yes"}
    if open_only:
        statuses = list(OPEN_STATUSES)
        if viewer.role == ROLE_EMPLOYEE:
            role = viewer.professional_role
    if include_closed:
        statuses = ["open", "partial", "closed"]
        if viewer.role == ROLE_EMPLOYEE:
            role = viewer.professional_role
    return {"start": start, "end": end, "role": role, "statuses": statuses}


def shift_report_csv(rows):
    """CSV of the shift table: date, staffing, status, and waiting candidates."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "shift_date",
            "weekday",
            "start_time",
            "end_time",
            "required_role",
            "assigned_count",
            "required_headcount",
            "status",
            "waiting_candidates",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                _csv_cell(row.shift_date.isoformat()),
                _csv_cell(row.weekday),
                _csv_cell(row.start_time),
                _csv_cell(row.end_time),
                _csv_cell(row.required_role),
                row.assigned_count,
                row.required_headcount,
                _csv_cell(row.status),
                row.waiting_candidates,
            ]
        )
    return buffer.getvalue()


def _csv_cell(value):
    text = str(value if value is not None else "")
    if text[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def requests_for_shift(shift_id):
    return (
        db.session.query(ShiftRequestRow)
        .filter(ShiftRequestRow.shift_id == shift_id)
        .order_by(ShiftRequestRow.request_id)
        .all()
    )


def recommendations_for(shift_id):
    rows = (
        db.session.query(RequestRecommendationRow)
        .filter(RequestRecommendationRow.shift_id == shift_id)
        .all()
    )
    return {row.request_id: row for row in rows}


def shift_board_payload(rows):
    payload = {
        "shifts": [_shift_summary(row) for row in rows],
    }
    if not rows:
        payload["message"] = "No shifts in the selected range"
    return payload


def shift_detail_payload(row, requests, viewer, recommendations=None):
    recommendations = recommendations or {}
    visible = []
    for item in requests:
        entry = {
            "request_id": item.request_id,
            "display_number": item.display_number,
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
        recommendation = recommendations.get(item.request_id)
        if recommendation and _can_see_recommendation(viewer, item):
            entry["recommendation"] = recommendation.decision
            entry["rationale"] = recommendation.rationale
            entry["hours_warning"] = recommendation.hours_warning
            entry["findings"] = recommendation_findings(recommendation.sources_json)
        visible.append(entry)
    today = datetime.now(ISRAEL).date()
    return {
        "shift": _shift_summary(row),
        "registered": visible,
        "assignees": _assignees(row.shift_id, viewer),
        "registration_open": row.status in OPEN_STATUSES and row.shift_date >= today,
    }


def recommendation_findings(sources):
    """Turn stored tool results into sentences a manager can read."""
    if not sources:
        return []
    if isinstance(sources, str):
        try:
            sources = json.loads(sources)
        except ValueError:
            return []
    by_name = {item.get("name"): item for item in sources if isinstance(item, dict)}
    findings = []
    availability = by_name.get("check_availability_conflicts")
    if availability:
        if availability.get("hard_block"):
            reasons = "; ".join(availability.get("reasons") or []) or "a hard constraint"
            findings.append("Availability blocks this request: {0}.".format(reasons))
        elif availability.get("soft_block"):
            findings.append("The employee prefers not to work this date. That is a warning, not a block.")
        else:
            findings.append("No availability conflict was found.")
    hours = by_name.get("calculate_weekly_hours")
    if hours:
        findings.append(
            "Approved hours are {0}. This shift would bring the week to {1}, and the limit is {2}.".format(
                hours.get("approved_hours"),
                hours.get("projected_hours"),
                hours.get("weekly_limit"),
            )
        )
        if hours.get("exceeds_limit"):
            findings.append("Weekly hours would pass 42. The request stays open for a manager decision.")
    tavily = by_name.get("tavily")
    if tavily:
        if tavily.get("found"):
            findings.append(
                "The date has a {0}: {1}.".format(
                    tavily.get("kind") or "event",
                    tavily.get("title") or "details were not titled",
                )
            )
        elif tavily.get("ok"):
            findings.append("No holiday or exceptional event was found for this date.")
        else:
            findings.append("Holiday and event lookup was unavailable, so the recommendation does not use it.")
    retrieval = by_name.get("retrieval")
    if retrieval:
        if retrieval.get("ok"):
            findings.append(
                "Semantic profile passages considered: {0}.".format(retrieval.get("passage_count"))
            )
        else:
            findings.append("Evaluation without semantic context. Profile matching was unavailable.")
    history = by_name.get("approved_history")
    if history:
        findings.append(
            "Approved shifts in the last {0} days: {1}.".format(history.get("days"), history.get("count"))
        )
    tools = by_name.get("tools")
    if tools and not tools.get("ok"):
        findings.append("Scheduling checks did not finish. A manager still needs to decide.")
    return findings


def _assignees(shift_id, viewer):
    rows = (
        db.session.query(ApprovedAssignmentRow)
        .filter(
            ApprovedAssignmentRow.shift_id == shift_id,
            ApprovedAssignmentRow.active == True,
        )
        .order_by(ApprovedAssignmentRow.employee_id)
        .all()
    )
    visible = []
    for row in rows:
        user = db.session.get(User, row.employee_id)
        name = user.full_name if user is not None else "Unknown"
        role = user.professional_role if user is not None else ""
        if viewer.role == ROLE_EMPLOYEE:
            visible.append({"first_name": name.split()[0], "professional_role": role})
        else:
            visible.append(
                {
                    "employee_id": row.employee_id,
                    "full_name": name,
                    "professional_role": role,
                }
            )
    return visible


def _can_see_recommendation(viewer, request_row):
    if viewer.role != ROLE_EMPLOYEE:
        return True
    return request_row.employee_id == viewer.user_id


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
