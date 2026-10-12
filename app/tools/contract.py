"""MCP tool results. Every tool returns ok plus a body, or an error code."""


def ok(result):
    return {"ok": True, "result": result}


def error(error_code):
    return {"ok": False, "error_code": error_code}
