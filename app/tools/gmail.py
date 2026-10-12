import os

from app.tools.contract import error, ok


def send_message(*, to, subject, body, transport=None):
    """Send mail through the Gmail MCP server. The message body is not stored."""
    if not str(to or "").strip():
        return error("missing_address")
    if not str(subject or "").strip() or not str(body or "").strip():
        return error("invalid_message")
    sender = transport or _gmail_mcp
    try:
        sent = sender(to=to, subject=subject, body=body)
    except Exception:
        return error("gmail_unavailable")
    if sent is None:
        return error("not_configured")
    if sent.get("ok") is False:
        return error(sent.get("error_code") or "gmail_unavailable")
    return ok({"sent": True})


def _gmail_mcp(*, to, subject, body):
    command = os.environ.get("MCP_GMAIL_COMMAND", "").strip()
    credentials = os.environ.get("MCP_GMAIL_CREDENTIALS", "").strip()
    if not command or not credentials:
        return None
    from app.mcp.stdio import call_tool

    return call_tool(
        command,
        "send_email",
        {"to": to, "subject": subject, "body": body},
        extra_env={"MCP_GMAIL_CREDENTIALS": credentials},
    )
