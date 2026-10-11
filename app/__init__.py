import os

from flask import Flask


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
    app.config["SOMEE_SQLSERVER_CONNECTION_STRING"] = os.environ.get(
        "SOMEE_SQLSERVER_CONNECTION_STRING", ""
    )
    app.config["MCP_TAVILY_API_KEY"] = os.environ.get("MCP_TAVILY_API_KEY", "")
    app.config["MCP_GMAIL_CREDENTIALS"] = os.environ.get("MCP_GMAIL_CREDENTIALS", "")

    from app.auth import auth_bp
    from app.commands import commands_bp
    from app.notifications import notifications_bp
    from app.queries import queries_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(commands_bp, url_prefix="/commands")
    app.register_blueprint(queries_bp, url_prefix="/queries")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")

    @app.get("/")
    def index():
        return {"status": "ok", "service": "smart-shift-manager"}

    return app
