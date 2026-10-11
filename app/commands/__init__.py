from flask import Blueprint

commands_bp = Blueprint("commands", __name__)


@commands_bp.get("/status")
def status():
    return {"blueprint": "commands", "ready": True}
