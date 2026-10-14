from flask import Blueprint

screens_bp = Blueprint("screens", __name__)

from app.screens import routes  # noqa: E402,F401
