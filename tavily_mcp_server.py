"""Tavily MCP server.

The Flask agent talks to this process with MCP. The Tavily HTTP API is used
only inside this server, and only with MCP_TAVILY_API_KEY from the environment.
"""

import json
import os
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

from app.mcp.protocol import dispatch_external
from app.mcp.stdio import run_stdio_server

load_dotenv(Path(__file__).resolve().parent / ".env")

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
SERVER_NAME = "tavily"
TOOLS = [
    {
        "name": "tavily_search",
        "description": "Search for holidays, events, and weather warnings on a date.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
]


def main():
    run_stdio_server(lambda message: dispatch_external(message, SERVER_NAME, TOOLS, call))


def call(name, arguments):
    if name != "tavily_search":
        return {"ok": False, "error_code": "unknown_tool"}
    query = str((arguments or {}).get("query") or "").strip()
    if not query:
        return {"error_code": "invalid_date"}
    api_key = os.environ.get("MCP_TAVILY_API_KEY", "").strip()
    if not api_key:
        return {"error_code": "not_configured"}
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
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return {"error_code": "tavily_unavailable"}
    if not isinstance(payload, dict) or payload.get("error"):
        return {"error_code": "tavily_unavailable"}
    return payload


if __name__ == "__main__":
    main()
