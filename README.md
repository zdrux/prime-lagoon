# OCP Inventory - Cluster Inventory & Compliance Management

A modern web application for managing OpenShift cluster inventory and running automated compliance audits.

## Features
- **Cluster Inventory**: Visual dashboard of all managed OpenShift/Kubernetes clusters.
- **Automated Audits**: Create "Ad-hoc" or "Bundle" rules to verify cluster configurations.
- **Dynamic Resource Fetching**: Uses the OpenShift dynamic client to fetch and evaluate any resource kind.
- **Compliance Scores**: Track compliance history and scores across the entire fleet.

## Tech Stack
- **Backend**: FastAPI, SQLModel (SQLite)
- **Frontend**: Vanilla CSS, HTML, Jinja2
- **Infrastructure**: OpenShift/Kubernetes Python Clients

## Getting Started
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Run the application:
   ```bash
   python main.py
   ```
   (Note: Database will initialize automatically on first run)

---
*Created with the help of Antigravity AI.*
