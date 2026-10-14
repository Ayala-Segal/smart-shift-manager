from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

from app.auth.guards import role_required
from app.auth.models import ROLE_EMPLOYEE, ROLE_MANAGER
from app.commands.decide_request import decide_request
from app.commands.define_shift import CommandRejected, define_shift
from app.commands.submit_requests import (
    request_assignment,
    request_swap,
    respond_to_swap,
    submit_availability,
)
from app.event_store import ASSIGNMENT_APPROVED, SWAP_APPROVED
from app.notifications.dispatch import dispatch_notifications
from app.projections import project_shift_board
from app.retrieval.service import ProfileRejected, save_employee_profile
from app.retrieval.store import RetrievalUnavailable

commands_bp = Blueprint("commands", __name__)
WAITING = "Waiting for agent evaluation"


@commands_bp.get("/status")
def status():
    return {"blueprint": "commands", "ready": True}


@commands_bp.post("/availability")
@role_required(ROLE_EMPLOYEE)
def availability():
    body = request.get_json(silent=True) or {}
    return _submit(
        lambda: submit_availability(
            actor_id=current_user.user_id,
            actor_role=current_user.role,
            constraint_type=body.get("constraint_type"),
            note=body.get("note") or "",
            shift_date=body.get("date"),
            start_date=body.get("start_date"),
            end_date=body.get("end_date"),
        )
    )


@commands_bp.post("/assignments")
@role_required(ROLE_EMPLOYEE)
def assignments():
    body = request.get_json(silent=True) or {}
    return _submit(
        lambda: request_assignment(
            actor_id=current_user.user_id,
            actor_role=current_user.role,
            shift_id=body.get("shift_id"),
            note=body.get("note") or "",
        )
    )


@commands_bp.post("/swaps")
@role_required(ROLE_EMPLOYEE)
def swaps():
    body = request.get_json(silent=True) or {}
    return _submit(
        lambda: request_swap(
            actor_id=current_user.user_id,
            actor_role=current_user.role,
            shift_id=body.get("shift_id"),
            replacement_employee_id=body.get("replacement_employee_id"),
            note=body.get("note") or "",
        )
    )


@commands_bp.post("/shifts")
@role_required(ROLE_MANAGER)
def shifts():
    body = request.get_json(silent=True) or {}
    try:
        event = define_shift(
            actor_id=current_user.user_id,
            actor_role=current_user.role,
            shift_date=body.get("shift_date"),
            start_time=body.get("start_time"),
            end_time=body.get("end_time"),
            required_role=body.get("required_role"),
            required_headcount=body.get("required_headcount") or 0,
            note=body.get("note") or "",
            crosses_midnight=bool(body.get("crosses_midnight")),
        )
    except CommandRejected as error:
        return jsonify({"error": str(error)}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "The required headcount must be at least 1."}), 400
    project_shift_board()
    return jsonify({"shift_id": event.aggregate_id, "status": "open"}), 201


@commands_bp.post("/assignments/<request_id>/decision")
@role_required(ROLE_MANAGER)
def assignment_decision(request_id):
    return _decide(request_id, "assignment")


@commands_bp.post("/swaps/<request_id>/colleague")
@role_required(ROLE_EMPLOYEE)
def swap_colleague(request_id):
    body = request.get_json(silent=True) or {}
    try:
        event = respond_to_swap(
            actor_id=current_user.user_id,
            actor_role=current_user.role,
            request_id=request_id,
            action=body.get("action"),
        )
    except CommandRejected as error:
        return jsonify({"error": str(error)}), 400
    project_shift_board()
    status = (
        "Waiting for agent evaluation"
        if event.event_type == "SwapColleagueApproved"
        else "Rejected"
    )
    return jsonify({"request_id": event.aggregate_id, "status": status}), 201


@commands_bp.post("/swaps/<request_id>/decision")
@role_required(ROLE_MANAGER)
def swap_decision(request_id):
    return _decide(request_id, "swap")


@commands_bp.post("/profiles")
@role_required(ROLE_MANAGER)
def profiles():
    body = request.get_json(silent=True) or {}
    try:
        saved = save_employee_profile(
            store_path=current_app.config["VECTOR_STORE_PATH"],
            employee_id=body.get("employee_id"),
            years_experience=body.get("years_experience"),
            skills=body.get("skills"),
            shift_types=body.get("shift_types") or "",
            preferences=body.get("preferences") or "",
            limitations=body.get("limitations") or "",
            manager_notes=body.get("manager_notes") or "",
        )
    except ProfileRejected as error:
        return jsonify({"error": str(error)}), 400
    except RetrievalUnavailable:
        return jsonify({"error": "Semantic search is unavailable."}), 503
    return jsonify(saved), 201


def _decide(request_id, expected_type):
    body = request.get_json(silent=True) or {}
    try:
        event = decide_request(
            actor_id=current_user.user_id,
            actor_role=current_user.role,
            request_id=request_id,
            action=body.get("action"),
            note=body.get("note") or "",
            expected_type=expected_type,
        )
    except CommandRejected as error:
        return jsonify({"error": str(error)}), 400
    project_shift_board()
    dispatch_notifications()
    status = "Approved" if event.event_type in {ASSIGNMENT_APPROVED, SWAP_APPROVED} else "Rejected"
    return jsonify({"request_id": event.aggregate_id, "status": status}), 201


def _submit(command):
    try:
        event = command()
    except CommandRejected as error:
        return jsonify({"error": str(error)}), 400
    project_shift_board()
    import json

    payload = json.loads(event.payload_json)
    body = {"request_id": event.aggregate_id, "status": payload.get("status") or WAITING}
    if payload.get("display_number") is not None:
        body["display_number"] = payload["display_number"]
    return jsonify(body), 201
