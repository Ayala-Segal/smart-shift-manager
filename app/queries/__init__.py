from datetime import date

from flask import Blueprint, current_app, jsonify, redirect, request
from flask_login import current_user

from app.auth.guards import role_required
from app.auth.models import ROLE_EMPLOYEE, ROLE_MANAGER
from app.extensions import db
from app.queries.dashboard import employee_dashboard, manager_dashboard_summary, pending_decisions
from app.queries.board import (
    board_filters_from_request,
    get_shift_board,
    recommendations_for,
    requests_for_shift,
    shift_board_payload,
    shift_detail_payload,
)
from app.read_models import ShiftBoardRow
from app.retrieval.service import ProfileRejected, describe_shift, match_candidates, passages_for_employee
from app.retrieval.store import RetrievalUnavailable

queries_bp = Blueprint("queries", __name__)


@queries_bp.get("/status")
def status():
    return {"blueprint": "queries", "ready": True}


@queries_bp.get("/manager-dashboard")
@role_required(ROLE_MANAGER)
def manager_dashboard():
    if _wants_html():
        return redirect("/manager")
    try:
        summary = manager_dashboard_summary(
            start=_query_date("start"),
            end=_query_date("end"),
        )
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    summary["dashboard"] = "manager"
    summary["user_id"] = current_user.user_id
    return jsonify(summary)


@queries_bp.get("/my-dashboard")
@role_required(ROLE_EMPLOYEE)
def my_dashboard():
    if _wants_html():
        return redirect("/me")
    summary = employee_dashboard(current_user.user_id)
    summary["dashboard"] = "employee"
    summary["user_id"] = current_user.user_id
    return jsonify(summary)


@queries_bp.get("/decisions")
@role_required(ROLE_MANAGER)
def decisions():
    if _wants_html():
        return redirect("/decisions")
    return jsonify({"decisions": pending_decisions()})


@queries_bp.get("/board")
@role_required(ROLE_MANAGER, ROLE_EMPLOYEE)
def board():
    try:
        filters = board_filters_from_request(request.args, current_user)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return shift_board_payload(get_shift_board(**filters))


@queries_bp.get("/shifts/<shift_id>")
@role_required(ROLE_MANAGER, ROLE_EMPLOYEE)
def shift_detail(shift_id):
    row = db.session.get(ShiftBoardRow, shift_id)
    if row is None:
        return jsonify({"error": "The shift does not exist."}), 404
    return shift_detail_payload(
        row,
        requests_for_shift(shift_id),
        current_user,
        recommendations_for(shift_id),
    )


@queries_bp.post("/semantic-match")
@role_required(ROLE_MANAGER)
def semantic_match():
    body = request.get_json(silent=True) or {}
    try:
        query_text = _semantic_query(body)
        matches = match_candidates(current_app.config["VECTOR_STORE_PATH"], query_text)
    except ProfileRejected as error:
        return jsonify({"error": str(error)}), 400
    except RetrievalUnavailable:
        return jsonify({"error": "Semantic search is unavailable."}), 503
    return jsonify({"query": query_text, "matches": matches})


@queries_bp.get("/semantic-context")
@role_required(ROLE_MANAGER)
def semantic_context():
    query_text = (request.args.get("q") or "").strip()
    if not query_text:
        return jsonify({"error": "A search query is required."}), 400
    try:
        passages = passages_for_employee(
            current_app.config["VECTOR_STORE_PATH"],
            (request.args.get("employee_id") or "").strip(),
            query_text,
        )
    except ProfileRejected as error:
        return jsonify({"error": str(error)}), 400
    except RetrievalUnavailable:
        return jsonify({"error": "Semantic search is unavailable."}), 503
    return jsonify({"query": query_text, "passages": passages})


def _wants_html():
    return "text/html" in request.headers.get("Accept", "")


def _query_date(name):
    value = (request.args.get(name) or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("Dates must use YYYY-MM-DD.") from error


def _semantic_query(body):
    shift_id = (body.get("shift_id") or "").strip()
    if shift_id:
        row = db.session.get(ShiftBoardRow, shift_id)
        if row is None:
            raise ProfileRejected("The shift does not exist.")
        return describe_shift(
            required_role=row.required_role,
            note=row.note,
            start_time=row.start_time,
            end_time=row.end_time,
            shift_date=row.shift_date.isoformat(),
        )
    return describe_shift(
        required_role=body.get("required_role") or "",
        note=body.get("note") or "",
        start_time=body.get("start_time") or "",
        end_time=body.get("end_time") or "",
        shift_date=body.get("date") or "",
    )
