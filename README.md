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

The system follows a modern event-driven architecture using Flask, CQRS, and Event Sourcing:
