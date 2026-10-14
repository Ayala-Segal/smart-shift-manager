import json


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "smart-shift-tools"


def handle_message(message, app):
    method = message.get("method")
    if method and str(method).startswith("notifications/"):
        return None
    request_id = message.get("id")
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
            },
        )
    if method == "tools/list":
        return _result(request_id, {"tools": _tool_definitions()})
    if method == "tools/call":
        params = message.get("params") or {}
        return _result(request_id, _call_tool(app, params.get("name"), params.get("arguments") or {}))
    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"},
    }


def _call_tool(app, name, arguments):
    from app.tools.conflicts import check_availability_conflicts
    from app.tools.hours import calculate_weekly_hours

    with app.app_context():
        if name == "calculate_weekly_hours":
            envelope = calculate_weekly_hours(
                arguments.get("employee_id"),
                arguments.get("week_start"),
                arguments.get("additional_shift_hours"),
            )
        elif name == "check_availability_conflicts":
            envelope = check_availability_conflicts(
                arguments.get("employee_id"),
                arguments.get("shift_date"),
                arguments.get("start_time"),
                arguments.get("end_time"),
            )
        else:
            envelope = {"ok": False, "error_code": "unknown_tool"}
    return {
        "content": [{"type": "text", "text": json.dumps(envelope)}],
        "isError": not envelope.get("ok", False),
    }


def _tool_definitions():
    return [
        {
            "name": "calculate_weekly_hours",
            "description": "Calculate approved weekly hours and whether a shift would pass 42 hours.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "employee_id": {"type": "string"},
                    "week_start": {"type": "string", "description": "Sunday, YYYY-MM-DD"},
                    "additional_shift_hours": {"type": "number"},
                },
                "required": ["employee_id", "week_start", "additional_shift_hours"],
            },
        },
        {
            "name": "check_availability_conflicts",
            "description": "Cross-check availability constraints, overlapping shifts, and the 8 hour rest.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "employee_id": {"type": "string"},
                    "shift_date": {"type": "string"},
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                },
                "required": ["employee_id", "shift_date", "start_time", "end_time"],
            },
        },
    ]


def dispatch_external(message, server_name, definitions, caller):
    """MCP handler for a server that is not the in-app shift-tool server."""
    method = message.get("method")
    if method and str(method).startswith("notifications/"):
        return None
    request_id = message.get("id")
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": server_name, "version": "1.0.0"},
            },
        )
    if method == "tools/list":
        return _result(request_id, {"tools": definitions})
    if method == "tools/call":
        params = message.get("params") or {}
        envelope = caller(params.get("name"), params.get("arguments") or {})
        return _result(
            request_id,
            {
                "content": [{"type": "text", "text": json.dumps(envelope)}],
                "isError": "error_code" in envelope and not envelope.get("ok", False),
            },
        )
    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"},
    }


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}
