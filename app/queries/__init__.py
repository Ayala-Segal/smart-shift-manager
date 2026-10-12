from flask import Blueprint

from app.queries.board import get_shift_board, shift_board_payload

queries_bp = Blueprint("queries", __name__)


@queries_bp.get("/status")
def status():
    return {"blueprint": "queries", "ready": True}


@queries_bp.get("/board")
def board():
    return shift_board_payload(get_shift_board())
