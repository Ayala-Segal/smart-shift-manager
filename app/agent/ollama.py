"""Ask a local Ollama model to phrase a rationale. It does not choose the decision."""

import json
import os
import re
import urllib.error
import urllib.request

from flask import current_app

from app.agent.decision import MAX_RATIONALE

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.2"
NOTE_LABELS = ("nights", "shabbat_eve", "holiday", "personal")
_HOUR_KEYS = ("approved_hours", "projected_hours", "shift_hours", "weekly_limit")
_NEGATED_EXCEED = re.compile(
    r"\b(?:does not|do not|did not|will not|would not|cannot|can't|won't|don't|doesn't|didn't|not|never)\s+exceed\w*",
    re.IGNORECASE,
)
_EXCEED = re.compile(r"\bexceed(?:s|ed|ing)?\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def rewrite_rationale(fallback, facts, *, generate=None):
    """Return (rationale, used_model). A failure keeps the rule-written fallback."""
    if generate is None and _tests_are_running():
        return fallback, False
    generator = generate or _generate
    for _attempt in range(2):
        try:
            raw = generator(fallback, facts)
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
            continue
        text = _limit(raw)
        if _acceptable(text, fallback, facts):
            return text, True
    return fallback, False


def _acceptable(text, fallback, facts):
    if not text or text == _limit(fallback):
        return False
    if any("\u0590" <= char <= "\u05ff" for char in text):
        return False
    if not _includes_hour_numbers(text, facts):
        return False
    if facts.get("exceeds_limit") is False and _claims_hours_exceed_the_limit(text):
        return False
    return True


def _includes_hour_numbers(text, facts):
    if not all(key in facts for key in _HOUR_KEYS):
        return True
    found = [float(token) for token in _NUMBER.findall(text)]
    for key in _HOUR_KEYS:
        expected = float(facts[key])
        if not any(abs(item - expected) < 1e-6 for item in found):
            return False
    return True


def _claims_hours_exceed_the_limit(text):
    return _EXCEED.search(_NEGATED_EXCEED.sub("", text)) is not None


def classify_constraint_note(note, *, generate=None):
    """Return nights, shabbat_eve, holiday, or personal. A failure stays personal."""
    text = " ".join(str(note or "").split())
    if not text:
        return "personal"
    if generate is None and _tests_are_running():
        return "personal"
    generator = generate or _classify
    try:
        raw = generator(text)
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return "personal"
    word = str(raw or "").strip().lower().split()
    label = word[0].strip(".,:;") if word else ""
    if label in NOTE_LABELS:
        return label
    return "personal"


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
        "constraint_labels": _constraint_labels(conflict),
        "draft": fallback,
    }


def _constraint_labels(conflict):
    labels = []
    for item in (conflict.get("result") or {}).get("constraint_notes") or []:
        label = item.get("label") or "personal"
        if label not in labels:
            labels.append(label)
    return ", ".join(labels) or "none"


def _classify(note):
    base = os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    prompt = (
        "Classify this employee constraint note. "
        "Reply with exactly one word: nights, shabbat_eve, holiday, or personal. "
        "nights means they do not want night work. "
        "shabbat_eve means Friday evening or the eve of Shabbat. "
        "holiday means a holiday or festival. "
        "personal means any other reason, including a wedding or family event.\n\n"
        "Note:\n{0}"
    ).format(note)
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    request = urllib.request.Request(
        base.rstrip("/") + "/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Ollama returned an unexpected payload.")
    return payload.get("response") or ""


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
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Ollama returned an unexpected payload.")
    return payload.get("response") or ""


def _prompt(fallback, facts):
    payload = dict(facts)
    payload.setdefault("constraint_labels", "none")
    return (
        "You write a short staffing recommendation for a manager. "
        "The decision is already {decision}. Do not change it and do not add a new decision. "
        "Rewrite the draft in different words. Do not copy the draft. "
        "Use only these facts: role_matches={role_matches}, hard_block={hard_block}, "
        "soft_block={soft_block}, reasons={reasons}, approved_hours={approved_hours}, "
        "projected_hours={projected_hours}, shift_hours={shift_hours}, "
        "weekly_limit={weekly_limit}, exceeds_limit={exceeds_limit}, "
        "holiday={holiday}, holiday_title={holiday_title}. "
        "Include these hour numbers exactly: approved hours {approved_hours}, "
        "projected hours {projected_hours}, shift hours {shift_hours}, "
        "weekly limit {weekly_limit}. "
        "Write at most 500 characters in English. "
        "Say the week exceeds the weekly limit only when exceeds_limit is True. "
        "When exceeds_limit is False, say the week stays within the limit. "
        "constraint_labels={constraint_labels}. "
        "If that is not none, mention that the employee left a constraint note. "
        "Do not repeat a non-English note.\n\n"
        "Draft:\n{draft}"
    ).format(**payload)


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
