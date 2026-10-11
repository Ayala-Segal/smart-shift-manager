from flask import Blueprint

queries_bp = Blueprint("queries", __name__)


@queries_bp.get("/status")
def status():
    return {"blueprint": "queries", "ready": True}
