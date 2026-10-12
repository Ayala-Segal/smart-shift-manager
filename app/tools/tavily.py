import json
import os
import urllib.request

from app.tools.contract import error, ok

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def search_date_context(shift_date, locality="Israel", search=None):
    """Ask Tavily whether the date has a holiday, event, or weather warning.

    The call uses the same search the Tavily MCP tool performs. A missing key
    or a failed call returns an error code and does not raise.
    """
    if not str(shift_date or "").strip():
        return error("invalid_date")
    place = (locality or "Israel").strip() or "Israel"
    query = (
        "Public holidays, special events, and weather warnings in {0} on {1}"
    ).format(place, shift_date)
    runner = search or _tavily_search
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


def _tavily_search(query):
    api_key = os.environ.get("MCP_TAVILY_API_KEY", "").strip()
    if not api_key:
        return None
    body = json.dumps(
        {
            "api_key": api_key,
            "query": query,
            "max_results": 3,
            "include_answer": True,
            "search_depth": "basic",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        TAVILY_SEARCH_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _kind(text):
    lowered = text.lower()
    if any(word in lowered for word in ("storm", "weather", "rain", "heat", "snow", "wind")):
        return "weather"
    if "holiday" in lowered:
        return "holiday"
    return "event"
