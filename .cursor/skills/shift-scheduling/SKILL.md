---
name: shift-scheduling
description: >-
  Applies Smart Shift Manager rules for shifts, availability constraints,
  assignment requests, swaps, weekly hours, and agent recommendations. Use when
  implementing or changing scheduling commands, projectors, constraint checks,
  the hours tool, or the evaluation agent.
---

# Shift scheduling and constraints

Read [docs/requirements.md](../../../docs/requirements.md) before changing scheduling behavior. Business rules BR-1 through BR-10 win over a local shortcut.

## Write path

1. Authorize the command from the session role.
2. Rebuild the aggregate from its events.
3. Reject the command when a business rule fails. Append nothing.
4. Append one event with the next version.
5. Leave read models to the projector.

Employee commands use `employee_id` from the session.

## Constraint types

| Type | Effect |
|---|---|
| `unavailable` | Hard block. Recommendation is `reject`. |
| `prefer not` | Soft signal. Recommendation is `warning` when nothing harder applies. |
| `prefer` | Stored preference. No block and no warning. |

Also reject on overlap, rest under 8 hours, or a role mismatch. Warn, and still send the request to the manager, when projected weekly hours exceed 42.

## Agent

The worker calls `check_availability_conflicts`, `calculate_weekly_hours`, and Tavily before it decides. It appends only `AgentEvaluationCompleted`. It does not approve, reject, send mail, or change headcount.

If vector retrieval fails, record `warning` with the rationale "Evaluation without semantic context" and continue with the tool results.

## Example

Input: an employee requests a shift that overlaps an approved shift.

Result: do not append `AssignmentRequested` if the overlap is already known at submit time. If the request was stored before the other approval, the agent appends `AgentEvaluationCompleted` with decision `reject`. The manager screen shows that recommendation. No `AssignmentApproved` is written by the agent.
