# OCP Inventory - Cluster Inventory & Compliance Management

A modern web application for managing OpenShift cluster inventory and running automated compliance audits.

## Features
- **Cluster Fleet Dashboard**: At-a-glance health status, version info, and node counts for your entire OpenShift fleet.
- **License Management**:
    - **Calculations**: Logic for tracking Red Hat OpenShift licensing usage (vCPU based).
    - **Configurable Rules**: Define include/exclude rules for nodes based on names or labels.
    - **Analytics Dashboard**: global view of license consumption trends, cluster breakdown, and MAPID/LOB specific views.
- **Compliance Scanning**: 
    - **Flexible Engine**: Run "Ad-hoc" checks or grouped "Bundles" of rules.
    - **Targeting**: Scope rules to specific environments, datacenters, or tags.
    - **Deep Inspection**: Fetch and validate any Kubernetes resource using dynamic clients.
- **Reporting System**: Generate and download detailed reports (Excel/CSV) based on environment, datacenter, and other criteria.
- **Security & Access**: 
    - **Simple RBAC**: Role-based access control with "Admin" privilege separation.
    - **LDAP Support**: Optional integration for enterprise authentication.

## Tech Stack
- **Backend**: FastAPI, SQLModel (SQLite)
- **Frontend**: Vanilla CSS, HTML, Jinja2
- **Infrastructure**: OpenShift/Kubernetes Python Dynamic Client
- **Reporting**: Pandas/CSV generation

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
