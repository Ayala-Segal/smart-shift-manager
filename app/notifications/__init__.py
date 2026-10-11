from flask import Blueprint

notifications_bp = Blueprint("notifications", __name__)


@notifications_bp.get("/status")
def status():
    return {"blueprint": "notifications", "ready": True}
