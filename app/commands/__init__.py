from flask import Blueprint, jsonify, request
from flask_login import current_user

from app.auth.guards import role_required
from app.auth.models import ROLE_EMPLOYEE
from app.commands.define_shift import CommandRejected
from app.commands.submit_requests import request_assignment, request_swap, submit_availability
from app.projections import project_shift_board

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


def _submit(command):
    try:
        event = command()
    except CommandRejected as error:
        return jsonify({"error": str(error)}), 400
    project_shift_board()
    return jsonify({"request_id": event.aggregate_id, "status": WAITING}), 201
