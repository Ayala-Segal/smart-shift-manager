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

Set the environment variables in the same shell before starting either process:

```powershell
$env:SECRET_KEY = "replace-with-a-session-secret"
$env:SOMEE_SQLSERVER_CONNECTION_STRING = "mssql+pyodbc://USER:PASSWORD@HOST/DB?driver=ODBC+Driver+18+for+SQL+Server"
$env:MCP_TAVILY_API_KEY = "replace-with-the-tavily-key"
$env:MCP_GMAIL_CREDENTIALS = "replace-with-the-gmail-mcp-credentials"
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

In a second terminal, with the virtual environment activated and the same variables set, start the agent:

```powershell
python agent_worker.py
```

`agent_worker.py` runs outside the HTTP server on a 30-second cycle. The web process still starts when the database and MCP variables are empty. The worker stays idle until `SOMEE_SQLSERVER_CONNECTION_STRING` is set.
