MAX_RATIONALE = 500
SEMANTIC_WARNING = "Evaluation without semantic context"


def decide(*, role_matches, conflict, hours, tavily, retrieval_ok, shift_hours):
    """Choose approve, warning, or reject from tool results.

    A failed hours or availability call is not handled here. The caller leaves
    that request for the next cycle.
    """
    hard_block = bool(conflict["result"]["hard_block"])
    soft_block = bool(conflict["result"]["soft_block"])
    exceeds = bool(hours["result"]["exceeds_limit"])
    holiday = bool(tavily.get("ok") and tavily["result"].get("found"))
    if hard_block or not role_matches:
        decision = "reject"
    elif (not retrieval_ok) or exceeds or soft_block or holiday:
        decision = "warning"
    else:
        decision = "approve"
    rationale = _rationale(
        decision,
        role_matches=role_matches,
        conflict=conflict,
        hours=hours,
        tavily=tavily,
        retrieval_ok=retrieval_ok,
        shift_hours=shift_hours,
        holiday=holiday,
    )
    return {
        "decision": decision,
        "rationale": rationale,
        "hours_warning": exceeds,
    }


def _rationale(decision, *, role_matches, conflict, hours, tavily, retrieval_ok, shift_hours, holiday):
    sentences = []
    if decision == "reject":
        reasons = []
        if not role_matches:
            reasons.append("the professional role does not match")
        if conflict["result"]["hard_block"]:
            reasons.extend(conflict["result"]["reasons"])
        sentences.append("Rejected: {0}.".format("; ".join(reasons) or "the request is blocked"))
    elif decision == "warning":
        reasons = []
        if not retrieval_ok:
            reasons.append(SEMANTIC_WARNING)
        if hours["result"]["exceeds_limit"]:
            reasons.append("weekly hours would exceed {0}".format(hours["result"]["weekly_limit"]))
        if conflict["result"]["soft_block"]:
            tool_reasons = conflict["result"]["reasons"]
            note_reasons = [item for item in tool_reasons if item.startswith("The employee note")]
            if any(item.startswith("The employee is unavailable") for item in tool_reasons):
                reasons.append("this conflicts with the employee's constraints")
            elif any("prefers not" in item for item in tool_reasons) or not note_reasons:
                reasons.append("the employee prefers not to work this date")
            reasons.extend(note_reasons)
        if holiday:
            title = tavily["result"].get("title") or "an exceptional event"
            reasons.append("holiday or exceptional event: {0}".format(title))
        sentences.append("Warning: {0}.".format("; ".join(reasons)))
    else:
        sentences.append("Approved.")
    if decision == "reject" and not retrieval_ok:
        sentences.append(SEMANTIC_WARNING + ".")
    if not tavily.get("ok"):
        sentences.append("Holiday data was unavailable.")
    figures = hours["result"]
    sentences.append(
        "Approved hours {0}, projected hours {1}, shift hours {2}, weekly limit {3}.".format(
            figures["approved_hours"],
            figures["projected_hours"],
            shift_hours,
            figures["weekly_limit"],
        )
    )
    text = " ".join(sentences)
    if len(text) <= MAX_RATIONALE:
        return text
    return text[: MAX_RATIONALE - 3].rstrip() + "..."
