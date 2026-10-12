import os
from uuid import uuid4

import click
from flask import Flask, jsonify
from sqlalchemy.pool import StaticPool

from app.extensions import db, login_manager
from app.event_store import (
    AGGREGATE_SHIFT,
    SHIFT_DEFINED,
    append_event,
    sqlalchemy_uri_from_somee,
)


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
    somee_connection = os.environ.get("SOMEE_SQLSERVER_CONNECTION_STRING", "")
    app.config["SOMEE_SQLSERVER_CONNECTION_STRING"] = somee_connection
    database_uri = sqlalchemy_uri_from_somee(somee_connection) or "sqlite://"
    app.config["SQLALCHEMY_DATABASE_URI"] = database_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    if database_uri == "sqlite://":
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    app.config["MCP_TAVILY_API_KEY"] = os.environ.get("MCP_TAVILY_API_KEY", "")
    app.config["MCP_GMAIL_CREDENTIALS"] = os.environ.get("MCP_GMAIL_CREDENTIALS", "")
    app.config["VECTOR_STORE_PATH"] = os.environ.get("VECTOR_STORE_PATH", "instance/chroma")

    db.init_app(app)
    login_manager.init_app(app)

    @login_manager.unauthorized_handler
    def unauthorized():
        return jsonify({"error": "Sign-in required."}), 401

    from app.cli import register_cli
    from app.auth import auth_bp
    from app.commands import commands_bp
    from app.notifications import notifications_bp
    from app.queries import queries_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(commands_bp, url_prefix="/commands")
    app.register_blueprint(queries_bp, url_prefix="/queries")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")
    register_cli(app)

    @app.get("/")
    def index():
        return {"status": "ok", "service": "smart-shift-manager"}

    @app.cli.command("append-sample-event")
    def append_sample_event():
        """Append one ShiftDefined event to the Somee event store."""
        if not somee_connection.strip():
            raise click.ClickException(
                "Set SOMEE_SQLSERVER_CONNECTION_STRING to the connection string copied from Somee."
            )
        event = append_event(
            aggregate_type=AGGREGATE_SHIFT,
            aggregate_id="shift-" + uuid4().hex[:8],
            event_type=SHIFT_DEFINED,
            payload={
                "shift_date": "2026-10-18",
                "start_time": "08:00",
                "end_time": "16:00",
                "required_role": "cashier",
                "required_headcount": 1,
                "note": "sample event from the application",
            },
            actor_id="manager-1",
        )
        click.echo(
            "stored event_id={0} type={1} aggregate_id={2} version={3}".format(
                event.event_id,
                event.event_type,
                event.aggregate_id,
                event.version,
            )
        )

    return app
