"""Gmail MCP server.

Flask notifications call the send_email tool on this process. Credentials stay
in MCP_GMAIL_CREDENTIALS and are never written to the event store.
"""

import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

from app.mcp.protocol import dispatch_external
from app.mcp.stdio import run_stdio_server

load_dotenv(Path(__file__).resolve().parent / ".env")

SERVER_NAME = "gmail"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
TOOLS = [
    {
        "name": "send_email",
        "description": "Send one English email through Gmail.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    }
]


def main():
    run_stdio_server(lambda message: dispatch_external(message, SERVER_NAME, TOOLS, call))


def call(name, arguments):
    if name != "send_email":
        return {"ok": False, "error_code": "unknown_tool"}
    arguments = arguments or {}
    recipient = str(arguments.get("to") or "").strip()
    subject = str(arguments.get("subject") or "").strip()
    body = str(arguments.get("body") or "").strip()
    if not recipient:
        return {"ok": False, "error_code": "missing_address"}
    if not subject or not body:
        return {"ok": False, "error_code": "invalid_message"}
    credentials = _credentials()
    if credentials is None:
        return {"ok": False, "error_code": "not_configured"}
    address, app_password = credentials
    message = EmailMessage()
    message["From"] = address
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(address, app_password)
            smtp.send_message(message)
    except Exception:
        return {"ok": False, "error_code": "gmail_unavailable"}
    return {"ok": True, "sent": True}


def _credentials():
    raw = os.environ.get("MCP_GMAIL_CREDENTIALS", "").strip()
    if not raw:
        return None
    if not raw.startswith("{") and Path(raw).is_file():
        raw = Path(raw).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    address = str(payload.get("address") or payload.get("email") or payload.get("user") or "").strip()
    password = str(payload.get("app_password") or payload.get("password") or "").strip()
    if not address or not password:
        return None
    return address, password


if __name__ == "__main__":
    main()
