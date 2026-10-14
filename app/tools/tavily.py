import os

from app.mcp.stdio import bundled_command
from app.tools.contract import error, ok


def search_date_context(shift_date, locality="Israel", search=None):
    """Ask the Tavily MCP server whether the date has a holiday or event.

    A missing API key or a failed MCP call returns an error code and does not raise.
    """
    if not str(shift_date or "").strip():
        return error("invalid_date")
    place = (locality or "Israel").strip() or "Israel"
    query = (
        "Public holidays, special events, and weather warnings in {0} on {1}"
    ).format(place, shift_date)
    runner = search or _tavily_mcp
    try:
        payload = runner(query)
    except Exception:
        return error("tavily_unavailable")
    if payload is None:
        return error("not_configured")
    if payload.get("error_code"):
        return error(payload["error_code"])
    results = payload.get("results") or []
    if not results:
        return ok({"found": False, "title": "", "source": "", "kind": ""})
    first = results[0]
    text = " ".join(
        [
            str(first.get("title") or ""),
            str(first.get("content") or ""),
            str(payload.get("answer") or ""),
        ]
    )
    return ok(
        {
            "found": True,
            "title": str(first.get("title") or "")[:160],
            "source": str(first.get("url") or ""),
            "kind": _kind(text),
        }
    )


def _tavily_mcp(query):
    api_key = os.environ.get("MCP_TAVILY_API_KEY", "").strip()
    if not api_key:
        return None
    command = os.environ.get("MCP_TAVILY_COMMAND", "").strip() or bundled_command("tavily_mcp_server.py")
    from app.mcp.stdio import call_tool

    return call_tool(
        command,
        "tavily_search",
        {"query": query},
        extra_env={"MCP_TAVILY_API_KEY": api_key},
        failure_code="tavily_unavailable",
    )


def _kind(text):
    lowered = text.lower()
    if any(word in lowered for word in ("storm", "weather", "rain", "heat", "snow", "wind")):
        return "weather"
    if "holiday" in lowered:
        return "holiday"
    return "event"
