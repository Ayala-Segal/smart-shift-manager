from flask import Blueprint, jsonify, request
from flask_login import current_user

from app.auth.guards import role_required
from app.auth.models import ROLE_EMPLOYEE, ROLE_MANAGER
from app.extensions import db
from app.queries.board import (
    board_filters_from_request,
    get_shift_board,
    requests_for_shift,
    shift_board_payload,
    shift_detail_payload,
)
from app.read_models import ShiftBoardRow

queries_bp = Blueprint("queries", __name__)


@queries_bp.get("/status")
def status():
    return {"blueprint": "queries", "ready": True}


@queries_bp.get("/manager-dashboard")
@role_required(ROLE_MANAGER)
def manager_dashboard():
    return jsonify(
        {
            "dashboard": "manager",
            "user_id": current_user.user_id,
        }
    )


@queries_bp.get("/my-dashboard")
@role_required(ROLE_EMPLOYEE)
def my_dashboard():
    return jsonify(
        {
            "dashboard": "employee",
            "user_id": current_user.user_id,
        }
    )


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
    return shift_detail_payload(row, requests_for_shift(shift_id), current_user)
