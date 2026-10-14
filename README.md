# smart-shift-manager

An AI-powered Smart Shift Management & Scheduling System built with Python and Flask. The platform streamlines scheduling, availability tracking, and shift exchange workflows while utilizing autonomous AI agents and semantic matching to optimize resource allocation.

This project is designed and developed following the **Specification-Driven Application Development (SDAD)** methodology.

---

## 🌟 Key Features

* **Multi-Role Access Control:** Separate workflows and interfaces for Shift Managers and Employees/Staff.
* **Smart Shift Exchange:** Automated request management and availability-based shift matching.
* **Interactive Dashboard:** Real-time visibility into open shifts, registration counts, and scheduling metrics.
* **Autonomous AI Evaluation Agent:** Independent agent process that evaluates shift requests using external tools and historical database consulting.
* **Semantic RAG Search:** Vector storage integration to analyze employee profiles, preferences, and qualifications against shift requirements.
* **CQRS & Event Sourcing Architecture:** Strict separation between write commands and read queries with an immutable event log in cloud database storage.
* **MCP Integration:** Protocol implementation for external integrations (e.g., web search and communication services).

---

## 🏗️ Architecture Overview

The system follows a modern event-driven architecture using Flask, CQRS, and Event Sourcing. The Flask application serves the screens and accepts commands and queries through separate Blueprints. Commands append immutable events to SQL Server on Somee. Projectors build read models from that log, and screens read those models only. A separate vector store holds embeddings of employee profiles and labor-agreement clauses. A background agent process evaluates pending shift requests with that retrieval and with MCP tools, then writes a recommendation event. The manager makes the final decision.

- [Requirements](docs/requirements.md)
- [Architecture](docs/architecture.md)
- [Skills and rules](docs/skills_and_rules.md)

---

## Run

Requires Python 3.9 or newer.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy [.env.example](.env.example) to `.env` and fill in the Somee connection string from the Somee control panel. `.env` stays on this machine. The app, the agent worker, and the MCP server load it at startup.

```powershell
Copy-Item .env.example .env
```

Start the Flask app:

```powershell
$env:FLASK_APP = "app"
flask run
```

The app listens on http://127.0.0.1:5000. Create the account table once. On Somee, run [db/003_users.sql](db/003_users.sql). Locally, in another terminal:

```powershell
flask init-accounts
```

Register and sign in with JSON. `role` is `manager` or `employee`. An employee also sends `professional_role`.

```powershell
curl -X POST http://127.0.0.1:5000/auth/register -H "Content-Type: application/json" -d "{\"full_name\":\"Ayala Levi\",\"email\":\"ayala@example.com\",\"password\":\"password1\",\"role\":\"employee\",\"professional_role\":\"cashier\"}"
curl -X POST http://127.0.0.1:5000/auth/login -H "Content-Type: application/json" -c cookies.txt -d "{\"email\":\"ayala@example.com\",\"password\":\"password1\"}"
```

A manager is sent to `/queries/manager-dashboard`. An employee is sent to `/queries/my-dashboard`. Wrong credentials, and an inactive account, both return `Sign-in failed.`

Signed-in users can list shifts at `/queries/board` (`mode=week` or `mode=month`, plus `date`, `role`, and `status`) and open one shift at `/queries/shifts/<shift_id>`. An employee submits an availability constraint at `/commands/availability`, an assignment request at `/commands/assignments`, or a swap at `/commands/swaps`. On Somee, also run [db/004_read_shift_requests.sql](db/004_read_shift_requests.sql) once so the board can show those requests.

A manager indexes an employee profile into the Chroma vector store at `/commands/profiles`. The profile text (role, experience, skills, shift types, preferences, limitations, and manager notes) is stored in `embedding_profiles`. The vectors are not stored in SQL Server. On Somee, run [db/005_embedding_profiles.sql](db/005_embedding_profiles.sql) once. `flask index-policies` embeds the labor-agreement clauses. A manager matches candidates to a shift at `/queries/semantic-match` with a `shift_id`, or with `required_role` and `note`. Date-specific availability stays in the event log and is not embedded.

The weekly-hours and availability tools are an MCP server, not browser routes. Start it in another terminal:

```powershell
python mcp_server.py
```

It exposes `calculate_weekly_hours` and `check_availability_conflicts`. Both read the read models only. On Somee, run [db/006_read_employee_hours.sql](db/006_read_employee_hours.sql) once. A swap request calls the availability tool before it is stored.

Tavily and Gmail are separate MCP servers. Copy [.env.example](.env.example) to `.env`. Put a key from [tavily.com](https://tavily.com) in `MCP_TAVILY_API_KEY`. Leave `MCP_TAVILY_COMMAND` empty and the app starts `python tavily_mcp_server.py` for each search. For Gmail, set `MCP_GMAIL_CREDENTIALS` to `{"address":"you@gmail.com","app_password":"the-app-password"}` or to a path of a JSON file with those fields. Create the app password in the Google account. Leave `MCP_GMAIL_COMMAND` empty and the app starts `python gmail_mcp_server.py`. An empty key or empty credentials returns `not_configured` and does not stop the board. `flask dispatch-notifications` emails employees after an approval, rejection, or cancellation and records `NotificationSent` or `NotificationFailed`. The decision stays in effect either way.

A manager downloads the shift table, including assigned count and waiting candidates, from **Export report** on the board or the dashboard. The file is `shift-report.csv`.

In a second terminal, with the virtual environment activated, start the agent:

```powershell
python agent_worker.py
```

`agent_worker.py` runs outside the HTTP server on a 30-second cycle. The web process still starts when the database and MCP variables are empty. The worker stays idle until `SOMEE_SQLSERVER_CONNECTION_STRING` is set.
