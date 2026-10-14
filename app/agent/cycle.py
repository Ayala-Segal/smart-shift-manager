"""One evaluation cycle for assignment and swap requests."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.agent.decision import decide
from app.auth.models import User
from app.event_store import (
    AGENT_EVALUATION_COMPLETED,
    ASSIGNMENT_REQUESTED,
    SWAP_REQUESTED,
    Event,
    append_event,
)
from app.extensions import db
from app.projections import ensure_read_models, project_shift_board
from app.read_models import ApprovedAssignmentRow, EvaluationLock, ShiftBoardRow
from app.retrieval.service import describe_shift
from app.retrieval.store import RetrievalUnavailable
from app.tools.conflicts import check_availability_conflicts
from app.tools.hours import calculate_weekly_hours
from app.tools.schedule import duration_hours, week_start_sunday
from app.tools.tavily import search_date_context

STALE_LOCK = timedelta(seconds=120)
HISTORY_DAYS = 30
ACTOR_ID = "agent"


def run_evaluation_cycle(
    app,
    *,
    availability=None,
    weekly_hours=None,
    search=None,
    retrieve=None,
    run_id=None,
):
    availability = availability or check_availability_conflicts
    weekly_hours = weekly_hours or calculate_weekly_hours
    search = search or search_date_context
    retrieve = retrieve or _retrieve
    run_id = run_id or uuid4().hex
    evaluated = 0
    with app.app_context():
        ensure_read_models()
        EvaluationLock.__table__.create(db.engine, checkfirst=True)
        for request in _pending_requests():
            if not _claim(request["request_id"], run_id):
                continue
            try:
                outcome = _evaluate_one(
                    app,
                    request,
                    availability=availability,
                    weekly_hours=weekly_hours,
                    search=search,
                    retrieve=retrieve,
                )
            except Exception:
                db.session.rollback()
                _release(request["request_id"], run_id)
                continue
            if outcome is None:
                _release(request["request_id"], run_id)
                continue
            _release(request["request_id"], run_id)
            print(
                "evaluated request_id={0} decision={1}".format(
                    request["request_id"],
                    outcome,
                ),
                flush=True,
            )
            evaluated += 1
        project_shift_board()
    return evaluated


def _evaluate_one(app, request, *, availability, weekly_hours, search, retrieve):
    if _already_evaluated(request["request_id"]):
        return None
    shift = db.session.get(ShiftBoardRow, request["payload"]["shift_id"])
    if shift is None:
        return None
    subject_id, role_name = _subject(request)
    if subject_id is None:
        return None
    shift_hours = duration_hours(shift.start_time, shift.end_time)
    conflict = availability(
        subject_id,
        shift.shift_date.isoformat(),
        shift.start_time,
        shift.end_time,
    )
    hours = weekly_hours(
        subject_id,
        week_start_sunday(shift.shift_date).isoformat(),
        shift_hours,
    )
    if not conflict.get("ok") or not hours.get("ok"):
        return None
    tavily = search(shift.shift_date.isoformat())
    retrieval_ok, passage_count = _passages(app, retrieve, subject_id, shift)
    choice = decide(
        role_matches=role_name == shift.required_role,
        conflict=conflict,
        hours=hours,
        tavily=tavily,
        retrieval_ok=retrieval_ok,
        shift_hours=shift_hours,
    )
    if _already_evaluated(request["request_id"]):
        return None
    history = _recent_assignments(subject_id, shift.shift_date)
    try:
        append_event(
            aggregate_type=request["aggregate_type"],
            aggregate_id=request["request_id"],
            event_type=AGENT_EVALUATION_COMPLETED,
            payload={
                "request_id": request["request_id"],
                "request_type": request["request_type"],
                "shift_id": shift.shift_id,
                "employee_id": subject_id,
                "decision": choice["decision"],
                "rationale": choice["rationale"],
                "hours_warning": choice["hours_warning"],
                "sources": _sources(conflict, hours, tavily, retrieval_ok, passage_count, history),
            },
            actor_id=ACTOR_ID,
        )
    except IntegrityError:
        db.session.rollback()
        if _already_evaluated(request["request_id"]):
            return None
        return None
    return choice["decision"]


def _pending_requests():
    requested = (
        db.session.query(Event)
        .filter(Event.event_type.in_([ASSIGNMENT_REQUESTED, SWAP_REQUESTED]))
        .order_by(Event.event_id)
        .all()
    )
    done = {
        row[0]
        for row in db.session.query(Event.aggregate_id)
        .filter(Event.event_type == AGENT_EVALUATION_COMPLETED)
        .all()
    }
    pending = []
    for event in requested:
        if event.aggregate_id in done:
            continue
        pending.append(
            {
                "request_id": event.aggregate_id,
                "aggregate_type": event.aggregate_type,
                "request_type": "swap" if event.event_type == SWAP_REQUESTED else "assignment",
                "payload": json.loads(event.payload_json),
            }
        )
    return pending


def _subject(request):
    payload = request["payload"]
    if request["request_type"] == "swap":
        user = db.session.get(User, payload.get("replacement_employee_id"))
        if user is None:
            return None, None
        return user.user_id, user.professional_role
    return payload.get("employee_id"), payload.get("professional_role") or ""


def _passages(app, retrieve, employee_id, shift):
    try:
        query = describe_shift(
            required_role=shift.required_role,
            note=shift.note,
            start_time=shift.start_time,
            end_time=shift.end_time,
            shift_date=shift.shift_date.isoformat(),
        )
        passages = retrieve(app.config["VECTOR_STORE_PATH"], employee_id, query)
    except (RetrievalUnavailable, Exception):
        return False, 0
    return True, len(passages or [])


def _retrieve(store_path, employee_id, query):
    from app.retrieval.service import passages_for_employee

    return passages_for_employee(store_path, employee_id, query)


def _recent_assignments(employee_id, shift_date):
    start = shift_date - timedelta(days=HISTORY_DAYS)
    return (
        db.session.query(ApprovedAssignmentRow)
        .filter(
            ApprovedAssignmentRow.employee_id == employee_id,
            ApprovedAssignmentRow.shift_date >= start,
            ApprovedAssignmentRow.shift_date <= shift_date,
            ApprovedAssignmentRow.active.is_(True),
        )
        .count()
    )


def _sources(conflict, hours, tavily, retrieval_ok, passage_count, history):
    tavily_body = {"ok": bool(tavily.get("ok"))}
    if tavily.get("ok"):
        result = tavily["result"]
        tavily_body.update(
            {
                "found": result.get("found"),
                "title": result.get("title") or "",
                "kind": result.get("kind") or "",
            }
        )
    else:
        tavily_body["error_code"] = tavily.get("error_code")
    figures = hours["result"]
    return [
        {
            "name": "check_availability_conflicts",
            "ok": True,
            "hard_block": conflict["result"]["hard_block"],
            "soft_block": conflict["result"]["soft_block"],
            "reasons": conflict["result"]["reasons"],
        },
        {
            "name": "calculate_weekly_hours",
            "ok": True,
            "approved_hours": figures["approved_hours"],
            "projected_hours": figures["projected_hours"],
            "weekly_limit": figures["weekly_limit"],
            "exceeds_limit": figures["exceeds_limit"],
        },
        {"name": "tavily", **tavily_body},
        {"name": "retrieval", "ok": retrieval_ok, "passage_count": passage_count},
        {"name": "approved_history", "days": HISTORY_DAYS, "count": history},
    ]


def _already_evaluated(request_id):
    return (
        db.session.query(Event)
        .filter(
            Event.aggregate_id == request_id,
            Event.event_type == AGENT_EVALUATION_COMPLETED,
        )
        .first()
        is not None
    )


def _claim(request_id, run_id):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = db.session.get(EvaluationLock, request_id)
    if row is not None and row.run_id != run_id and now - row.locked_at < STALE_LOCK:
        return False
    if row is None:
        db.session.add(EvaluationLock(request_id=request_id, run_id=run_id, locked_at=now))
    else:
        row.run_id = run_id
        row.locked_at = now
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    current = db.session.get(EvaluationLock, request_id)
    return current is not None and current.run_id == run_id


def _release(request_id, run_id):
    row = db.session.get(EvaluationLock, request_id)
    if row is not None and row.run_id == run_id:
        db.session.delete(row)
        db.session.commit()

