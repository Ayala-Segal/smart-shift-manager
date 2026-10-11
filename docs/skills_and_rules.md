# Skills and Rules
## Smart Shift Manager

| Field | Value |
|---|---|
| Document | Skills and rules |
| System | Smart Shift Manager |
| Version | 1.0 |
| Methodology | Specification-Driven Application Development (SDAD) |
| Status | Approved for development |
| Related | [requirements.md](requirements.md), [architecture.md](architecture.md), [specification.md](specification.md) |

This document defines the agent guidance for the project: one skill adopted from [Skills.sh](https://www.skills.sh/), one project skill, and one project rule. Requirements stay in [requirements.md](requirements.md). Runtime structure stays in [architecture.md](architecture.md).

---

## 1. How the three pieces divide the work

| Piece | Source | Applies when | Owns |
|---|---|---|---|
| `flask` | Skills.sh, `jezweb/claude-skills` | Flask application code | Application factory, Blueprints, Flask-SQLAlchemy, current Flask versions |
| `shift-scheduling` | This repository | Scheduling commands, constraints, projectors, and the agent | Business rules BR-1–BR-10 and the recommendation decisions |
| `shift-domain` | This repository | Every session | Invariants that must not be broken while coding |

The Flask skill does not decide whether a shift is approved. The scheduling skill does not choose a web framework. The rule repeats only the invariants that must stay visible even when a skill is not loaded.

---

## 2. Skill integrated from Skills.sh

### 2.1 Identity

| Field | Value |
|---|---|
| Name | `flask` |
| Catalog page | https://www.skills.sh/jezweb/claude-skills/flask |
| Repository | https://github.com/jezweb/claude-skills |
| Install target in this project | `.cursor/skills/flask` |
| Why this skill | The web application is Flask. The skill supplies the application factory, Blueprint layout, and Flask-SQLAlchemy patterns used in [architecture.md](architecture.md). |

Catalog summary, as published on Skills.sh: production-tested Flask patterns with the application factory, Blueprints, and Flask-SQLAlchemy. Versions cited there, verified January 2026: Flask 3.1.2, Flask-SQLAlchemy 3.1.1, Flask-Login 0.6.3, Flask-WTF 1.2.2, Werkzeug 3.1.5, Python 3.9 or newer.

### 2.2 Install

From the repository root, with Cursor selected as the target agent:

```bash
npx skills add https://github.com/jezweb/claude-skills/tree/v1-final --skill flask --agent cursor
```

The `flask` skill is on the `v1-final` tag. The current `main` branch of that repository no longer includes it. The installer writes `.agents/skills/flask`, and this project also keeps an unedited copy at `.cursor/skills/flask`.

Confirm that `.cursor/skills/flask/SKILL.md` exists, then start a new Cursor session so the skill is discovered. Do not copy the skill into `~/.cursor/skills-cursor/`. That directory is reserved for Cursor built-in skills.

### 2.3 What this project adopts

- Build the web app with an application factory in `app/__init__.py`.
- Split HTTP surfaces into Blueprints: `auth`, `commands`, `queries`, and `notifications`.
- Use Flask-SQLAlchemy for the Somee connection and for read-model tables.
- Use Flask-Login for the session and the role check.
- Stay on the Flask 3.1 line and Python 3.9 or newer named by the skill.

### 2.4 What this project overrides

The skill's general persistence examples do not replace the domain rules.

- The system of record is the `events` table on SQL Server at Somee, not a local SQLite file.
- SQLAlchemy models for `read_shift_board` and the other read models are projections. Command handlers do not update them.
- `agent_worker` is a separate process. It must not rely on a Flask request context or an application context that exists only inside an HTTP request.
- Connection strings and MCP secrets come from environment variables.

When a Flask example in the installed skill writes the current row directly, follow that example only for account records such as the user and password hash. For shifts, constraints, requests, and decisions, follow section 3 instead.

---

## 3. Project skill

Canonical file: [.cursor/skills/shift-scheduling/SKILL.md](../.cursor/skills/shift-scheduling/SKILL.md)

The skill loads when work touches shifts, availability, assignment requests, swaps, weekly hours, or the evaluation agent.

```markdown
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
```

---

## 4. Project rule

Canonical file: [.cursor/rules/shift-domain.mdc](../.cursor/rules/shift-domain.mdc)

The rule always applies. It is short on purpose so the invariants stay in context for every change in the repository.

````markdown
---
description: Domain invariants for Smart Shift Manager commands, events, and the evaluation agent
alwaysApply: true
---

# Shift domain invariants

Follow [docs/requirements.md](docs/requirements.md) and [docs/architecture.md](docs/architecture.md).

- Append events. Do not update or delete rows in `events`.
- Command handlers do not write read models. Projectors do.
- Screen queries do not read `events`.
- `agent_worker` may append only `AgentEvaluationCompleted`.
- Reject overlaps, rest under 8 hours, `unavailable`, and role mismatches.
- Weekly hours above 42 are a warning, not an automatic rejection.
- Take `employee_id` from the session on employee commands.
- Keep the database connection string and MCP secrets in environment variables.
- User-facing copy is English.

```python
# Bad: the command updates the board in place
shift.assigned_count += 1
db.session.commit()

# Good: the command appends one event; the projector updates the board
append_event(aggregate_id, "AssignmentApproved", payload)
```
````

---

## 5. Adoption checklist

- [x] `npx skills add https://github.com/jezweb/claude-skills/tree/v1-final --skill flask --agent cursor` has been run with Cursor selected.
- [x] `.cursor/skills/flask/SKILL.md` is present and was not edited to weaken the factory or Blueprint guidance.
- [ ] `.cursor/skills/shift-scheduling/SKILL.md` matches section 3.
- [ ] `.cursor/rules/shift-domain.mdc` matches section 4 and `alwaysApply` is true.
- [ ] A scheduling change that conflicts with the Flask skill is resolved using section 2.4.
