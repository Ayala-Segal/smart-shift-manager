import json
from datetime import datetime, timezone
from urllib.parse import quote_plus

from sqlalchemy import BigInteger
from sqlalchemy.ext.compiler import compiles

from app.extensions import db

@compiles(BigInteger, "sqlite")
def _sqlite_bigint_as_integer(element, compiler, **kw):
    """SQLite only autoincrements an INTEGER primary key."""
    return "INTEGER"


AGGREGATE_SHIFT = "Shift"
AGGREGATE_ASSIGNMENT_REQUEST = "AssignmentRequest"
AGGREGATE_SWAP_REQUEST = "SwapRequest"
AGGREGATE_EMPLOYEE_AVAILABILITY = "EmployeeAvailability"

SHIFT_DEFINED = "ShiftDefined"
SHIFT_UPDATED = "ShiftUpdated"
SHIFT_CANCELLED = "ShiftCancelled"
AVAILABILITY_CONSTRAINT_SUBMITTED = "AvailabilityConstraintSubmitted"
ASSIGNMENT_REQUESTED = "AssignmentRequested"
SWAP_REQUESTED = "SwapRequested"
AGENT_EVALUATION_COMPLETED = "AgentEvaluationCompleted"
ASSIGNMENT_APPROVED = "AssignmentApproved"
ASSIGNMENT_REJECTED = "AssignmentRejected"
SWAP_APPROVED = "SwapApproved"
SWAP_REJECTED = "SwapRejected"
NOTIFICATION_SENT = "NotificationSent"
NOTIFICATION_FAILED = "NotificationFailed"

EVENT_TYPES = frozenset(
    {
        SHIFT_DEFINED,
        SHIFT_UPDATED,
        SHIFT_CANCELLED,
        AVAILABILITY_CONSTRAINT_SUBMITTED,
        ASSIGNMENT_REQUESTED,
        SWAP_REQUESTED,
        AGENT_EVALUATION_COMPLETED,
        ASSIGNMENT_APPROVED,
        ASSIGNMENT_REJECTED,
        SWAP_APPROVED,
        SWAP_REJECTED,
        NOTIFICATION_SENT,
        NOTIFICATION_FAILED,
    }
)


class Event(db.Model):
    __tablename__ = "events"

    event_id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    aggregate_type = db.Column(db.String(64), nullable=False)
    aggregate_id = db.Column(db.String(64), nullable=False)
    event_type = db.Column(db.String(64), nullable=False)
    payload_json = db.Column(db.Text, nullable=False)
    version = db.Column(db.Integer, nullable=False)
    occurred_at = db.Column(db.DateTime, nullable=False)
    actor_id = db.Column(db.String(64), nullable=False)


def parse_ado_net(raw):
    values = {}
    index = 0
    text = raw.strip()
    while index < len(text):
        while index < len(text) and text[index] in "; \t":
            index += 1
        if index >= len(text):
            break
        separator = text.find("=", index)
        if separator == -1:
            break
        key = text[index:separator].strip().lower()
        index = separator + 1
        if index < len(text) and text[index] == '"':
            index += 1
            end = text.find('"', index)
            if end == -1:
                raise ValueError("The connection string has an unterminated quoted value.")
            value = text[index:end]
            index = end + 1
        else:
            end = text.find(";", index)
            if end == -1:
                value = text[index:]
                index = len(text)
            else:
                value = text[index:end]
                index = end + 1
        values[key] = value.strip()
    return values


def preferred_sql_server_driver():
    import pyodbc

    installed = set(pyodbc.drivers())
    for name in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"):
        if name in installed:
            return name
    raise RuntimeError(
        "Install ODBC Driver 18 for SQL Server, or keep ODBC Driver 17 installed."
    )


def sqlalchemy_uri_from_somee(raw):
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("mssql+"):
        return raw

    fields = parse_ado_net(raw)
    server = fields.get("data source") or fields.get("server")
    database = fields.get("initial catalog") or fields.get("database")
    user = fields.get("user id") or fields.get("uid")
    password = fields.get("pwd")
    if password is None:
        password = fields.get("password")
    if not server or not database or not user or password is None:
        raise ValueError(
            "The Somee connection string is missing the server, database, user, or password."
        )

    driver = preferred_sql_server_driver()
    odbc = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )
    return "mssql+pyodbc:///?odbc_connect=" + quote_plus(odbc)


def append_event(aggregate_type, aggregate_id, event_type, payload, actor_id):
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown event type: {event_type}")

    current = (
        db.session.query(db.func.max(Event.version))
        .filter(Event.aggregate_id == aggregate_id)
        .scalar()
    )
    event = Event(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload_json=json.dumps(payload),
        version=(current or 0) + 1,
        occurred_at=datetime.now(timezone.utc).replace(tzinfo=None),
        actor_id=actor_id,
    )
    db.session.add(event)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    db.session.refresh(event)
    return event
