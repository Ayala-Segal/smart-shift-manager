from flask import Blueprint, jsonify
from flask_login import current_user

from app.auth.guards import role_required
from app.auth.models import ROLE_EMPLOYEE, ROLE_MANAGER
from app.queries.board import get_shift_board, shift_board_payload

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
def board():
    return shift_board_payload(get_shift_board())
