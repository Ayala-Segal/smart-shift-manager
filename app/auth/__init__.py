from flask import Blueprint

auth_bp = Blueprint("auth", __name__)


@auth_bp.get("/status")
def status():
    return {"blueprint": "auth", "ready": True}
