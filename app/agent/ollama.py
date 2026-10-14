"""Ask a local Ollama model to phrase a rationale. It does not choose the decision."""

import json
import os
import urllib.error
import urllib.request

from flask import current_app

from app.agent.decision import MAX_RATIONALE

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.2"


def rewrite_rationale(fallback, facts, *, generate=None):
    """Return (rationale, used_model). A failure keeps the rule-written fallback."""
    if generate is None and _tests_are_running():
        return fallback, False
    generator = generate or _generate
    try:
        raw = generator(fallback, facts)
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return fallback, False
    text = _limit(raw)
    if not text:
        return fallback, False
    return text, True


def rationale_facts(*, decision, role_matches, conflict, hours, tavily, shift_hours, fallback):
    result = tavily.get("result") or {}
    figures = hours["result"]
    return {
        "decision": decision,
        "role_matches": bool(role_matches),
        "hard_block": bool(conflict["result"]["hard_block"]),
        "soft_block": bool(conflict["result"]["soft_block"]),
        "reasons": list(conflict["result"]["reasons"]),
        "approved_hours": figures["approved_hours"],
        "projected_hours": figures["projected_hours"],
        "weekly_limit": figures["weekly_limit"],
        "exceeds_limit": bool(figures["exceeds_limit"]),
        "shift_hours": shift_hours,
        "holiday": bool(tavily.get("ok") and result.get("found")),
        "holiday_title": result.get("title") or "",
        "draft": fallback,
    }


def _generate(fallback, facts):
    base = os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    body = json.dumps(
        {
            "model": model,
            "prompt": _prompt(fallback, facts),
            "stream": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        base.rstrip("/") + "/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Ollama returned an unexpected payload.")
    return payload.get("response") or ""


def _prompt(fallback, facts):
    return (
        "You write a short staffing recommendation for a manager. "
        "The decision is already {decision}. Do not change it. "
        "Use only these facts: role_matches={role_matches}, hard_block={hard_block}, "
        "soft_block={soft_block}, reasons={reasons}, approved_hours={approved_hours}, "
        "projected_hours={projected_hours}, shift_hours={shift_hours}, "
        "weekly_limit={weekly_limit}, exceeds_limit={exceeds_limit}, "
        "holiday={holiday}, holiday_title={holiday_title}. "
        "Write at most 500 characters in English. Include the hour numbers. "
        "Do not add a new decision.\n\nDraft:\n{draft}"
    ).format(**facts)


def _limit(value):
    text = " ".join(str(value or "").split())
    if len(text) <= MAX_RATIONALE:
        return text
    return text[: MAX_RATIONALE - 3].rstrip() + "..."


def _tests_are_running():
    try:
        return bool(current_app.config.get("TESTING"))
    except RuntimeError:
        return False
