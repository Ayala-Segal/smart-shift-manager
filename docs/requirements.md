# Requirements Specification
## Smart Shift Manager

| Field | Value |
|---|---|
| Document | Requirements specification |
| System | Smart Shift Manager |
| Version | 1.0 |
| Methodology | Specification-Driven Application Development (SDAD) |
| Status | Approved for design |
| Related | [architecture.md](architecture.md), [skills_and_rules.md](skills_and_rules.md), [specification.md](specification.md) |

This document states what the system must do and the quality constraints it must meet. Component design, event storage, and agent runtime are specified in [architecture.md](architecture.md).

---

## 1. Purpose

Smart Shift Manager lets one service branch plan shifts, collect employee availability, and decide assignment and swap requests. A background agent recommends approve, reject, or warning. A manager makes the final decision.

## 2. Scope

### 2.1 In scope

- Two human roles: shift manager and employee.
- Shift definition, a weekly and monthly board, and a shift card.
- Search and table display.
- Constraint entry, assignment requests, and swap requests.
- A dashboard of shift status and candidate counts.
- An autonomous evaluation recommendation for each request.
- Email notification after a decision that affects an employee.

### 2.2 Out of scope

- Payroll, biometric attendance, and recruitment.
- A native mobile application. The web interface must work on a desktop browser and on a narrow screen.
- Automatic assignment approval without a manager decision.
- Multiple branches or cross-organization permissions.

---

## 3. Users

### 3.1 Shift manager

The manager staffs the board. The manager can define, update, and cancel a shift; view the weekly board, the monthly board, and the dashboard; accept or reject an assignment or a swap; and read the agent recommendation and its rationale.

The manager cannot change an event that has already been stored and cannot bypass the overlapping-shift check.

### 3.2 Employee

The employee submits availability and requests. The employee can enter constraints and preferences; search for an open shift by date or role; request assignment to an open shift; request a swap for a shift they are assigned to; and view their own requests, statuses, and approved shifts.

The employee cannot approve an assignment for anyone and cannot see another employee's full profile or constraints.

### 3.3 Evaluation agent

The agent is a service identity, not a human user. It can read requests, profiles, board history, and its tools, and it can record a recommendation. It cannot approve or reject an assignment.

---

## 4. Business rules

| ID | Rule |
|---|---|
| BR-1 | A shift has a date, start time, end time, required role, and required headcount. The end is later than the start on the same day, or the shift crosses midnight explicitly. |
| BR-2 | Board status is open (nobody assigned), partial (some assigned, headcount not reached), staffed (headcount reached), or cancelled. |
| BR-3 | An employee is not assigned to two overlapping shifts. |
| BR-4 | The same employee needs at least 8 hours of rest between the end of one shift and the start of the next. |
| BR-5 | The default weekly cap is 42 hours. An overrun produces a warning. It does not reject the request by itself. |
| BR-6 | An employee can request assignment only to an open or partial shift, and only when their professional role matches the required role. |
| BR-7 | A swap is allowed only between two qualified employees, only for a future shift, and only after an explicit request. |
| BR-8 | A recommendation informs the manager. The assignment takes effect only after manager approval. |
| BR-9 | An `unavailable` constraint on the shift date blocks an approve recommendation. A `prefer not` constraint produces a warning. A `prefer` constraint does not block and does not warn. |
| BR-10 | A stored event is not deleted and is not edited. A correction is a new event. |

A work week runs from Sunday 00:00 through Saturday 23:59, Israel time.

---

## 5. Functional requirements

### 5.1 Search and table display

**FR-1.** The shift board is a table in two modes: week (Sunday–Saturday) and month.

Required columns: date and weekday, hours, role, assigned / required, status, number of waiting candidates.

**FR-2.** The user can filter by start date, end date, role, and status. An open-shift search limits results to open or partial shifts.

**FR-3.** Selecting a row opens a shift card with the shift definition, approved assignees, waiting requests, and the agent recommendation for each request. An employee sees assignees by first name and role only, and does not see recommendations about other employees.

**FR-4.** An empty table shows "No shifts in the selected range" and a link to widen the range. The manager also sees a shortcut to define a shift.

### 5.2 Data entry

**FR-5.** The constraint form stores a date or a range, a type (`unavailable`, `prefer`, or `prefer not`), and a note of at most 300 characters.

**FR-6.** An assignment request opens from an open or partial shift card and may include an employee note.

**FR-7.** A swap request names the shift offered for swap and the replacement employee. If the replacement is blocked by their constraints, the form shows the block before submission.

**FR-8.** After a successful submission the system shows a request number and the status "Waiting for agent evaluation".

### 5.3 Dashboard

**FR-9.** The manager dashboard defaults to the next 7 days and shows counts of open shifts, partial shifts, staffed shifts, requests waiting for a manager decision, and requests still waiting for the agent.

**FR-10.** For each unstaffed shift, the dashboard table shows how many candidates registered and are waiting.

**FR-11.** The employee dashboard shows that employee's approved shifts in the current week, the week's hour total, and their open requests.

### 5.4 Manager decision

**FR-12.** The pending-requests screen shows, for each request, the employee name, the shift, the request type (assignment or swap), the agent recommendation, the rationale, and an hours warning when one exists.

**FR-13.** The manager can approve, or reject with a note. Both actions are recorded.

### 5.5 Authentication and access

**FR-14.** A user with an active account signs in with email and password and is routed to the dashboard for their role.

**FR-15.** Wrong credentials produce one failure message. The message does not say which field is wrong.

**FR-16.** An inactive account cannot sign in. Its history remains available to the manager through existing records.

### 5.6 Shift lifecycle

**FR-17.** The manager can define a shift with date, start, end, role, required headcount, and an optional note. A valid definition appears on the board as open.

**FR-18.** A define command is rejected, and nothing is recorded, when the date is in the past, the hours are invalid, or the required headcount is below 1.

**FR-19.** The manager can update hours, role, headcount, or note, and can cancel a shift.

### 5.7 Requests and evaluation

**FR-20.** An employee can submit an assignment request when the shift is open or partial and it does not overlap a shift already approved for them. The request waits for evaluation.

**FR-21.** An employee can submit a swap for one of their future assigned shifts and a replacement employee. After a recommendation and manager approval, the original assignment is replaced and the board updates.

**FR-22.** Each assignment or swap request that has no recommendation yet receives exactly one recommendation: `approve`, `reject`, or `warning`, plus a rationale of at most 500 characters and the sources used.

**FR-23.** The recommendation is `reject` when shifts overlap, rest is under 8 hours, an `unavailable` constraint applies, or the role does not match.

**FR-24.** The recommendation is `warning` when the request is otherwise eligible and weekly hours would exceed 42, a `prefer not` constraint applies, or a holiday or exceptional event is found for the date.

**FR-25.** The recommendation is `approve` when the request is eligible and none of the warning conditions apply.

**FR-26.** The manager may decide against the recommendation. The decision stores the manager's note.

**FR-27.** Approval of an assignment that does not violate BR-3 increases the assigned count. Rejection stores the rejection and shows the new status to the employee.

**FR-28.** A decision that affects an employee sends that employee an email. If sending fails, the failure is recorded and the decision stays in effect.

---

## 6. Use cases

| ID | Name | Actor | Outcome |
|---|---|---|---|
| UC-1 | Sign in | Manager or employee | Role dashboard, or a generic credential failure |
| UC-2 | Define a shift | Manager | Open shift on the board, or a rejected command |
| UC-3 | Search and display the board | Manager or employee | Filtered table; a row opens the shift card. An employee open-shift search shows only shifts they qualify for |
| UC-4 | Enter a constraint | Employee | Constraint stored and available for later requests |
| UC-5 | Request assignment | Employee | Request waiting for evaluation |
| UC-6 | Request a swap | Employee | Swap waiting for evaluation; board changes only after approval |
| UC-7 | View the dashboard | Manager | Open, partial, and staffed counts, plus waiting candidates per shift |
| UC-8 | Evaluate a request | Agent | One recommendation event |
| UC-9 | Approve or reject | Manager | Assignment or swap approved or rejected |
| UC-10 | Notify by email | System | Notification sent or failure recorded, without undoing the decision |

---

## 7. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | The interface language is English. |
| NFR-2 | Passwords are stored as a hash. The database connection string and MCP secrets live in environment variables, not in source code. |
| NFR-3 | An employee cannot open manager routes. Every command and every query checks the signed-in role. |
| NFR-4 | An agent failure or an email failure does not block viewing the board or submitting a request. |
| NFR-5 | Every read model used by a screen can be rebuilt from the event history. |
| NFR-6 | Times shown to users use Israel time. Stored event timestamps use UTC. |
| NFR-7 | The agent log records the request identifier and the decision. It does not record a full profile body or email content. |
| NFR-8 | Screen queries stay available when the vector store or the agent process is down. |
| NFR-9 | Two concurrent evaluations of the same request produce one recommendation, not two. |
| NFR-10 | Employee commands use the signed-in user's identifier from the session. The form cannot choose another employee identifier. |

---

## 8. Acceptance criteria

1. A manager defines a shift and it appears on the board as open after the change is recorded and the board view is refreshed.
2. An employee filters open shifts by date and role, submits a request, and sees that request as waiting.
3. A request is not approved before a recommendation exists. After evaluation, the manager sees a recommendation and a rationale.
4. A request that overlaps an approved shift receives `reject`. A request that takes the week above 42 hours receives `warning` and still reaches the manager.
5. Manager approval increases the assigned count on the dashboard and in the table. A later request that would overlap is rejected.
6. The manager dashboard shows open, partial, and staffed shifts separately, and the number of candidates for each open shift.
7. The board screen reads the board view. Clearing that view and rebuilding it from history restores the same board.
8. On a holiday date, the rationale includes the holiday finding.
9. After approval, the employee is notified, or a send failure is recorded and the approval remains.
10. An employee who opens the manager decision screen is refused.

---

## 9. Traceability

| Assignment need | Requirements |
|---|---|
| Two user types, manager and employee | Section 3, FR-14, FR-16, NFR-3 |
| Search and table display | FR-1–FR-4, UC-3 |
| Data entry | FR-5–FR-8, FR-20, FR-21, UC-4–UC-6 |
| Dashboard | FR-9–FR-11, UC-7 |
| Autonomous evaluation | FR-22–FR-25, UC-8, NFR-4, NFR-9 |
| Manager decision and notification | FR-12, FR-13, FR-26–FR-28, UC-9, UC-10 |
| Quality, privacy, and recovery | NFR-1–NFR-10 |

---

## 10. Assumptions

- One branch and two human roles.
- A 42-hour overrun warns. It does not block.
- Rest under 8 hours blocks.
- The manager always decides. The agent only recommends.
- The interface language is English.

If an official requirements document states a different cap, rest gap, or language, update BR-4, BR-5, NFR-1, and section 10 together.
