import json
import asyncio

import httpx
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.dependencies import get_current_user
from app.main import app
from app.models import Cluster, ReportRun, ReportRunCluster, User
from app.routers import reporting
from app.services import reporting as reporting_service


def test_reporting_template_run_and_export_lifecycle(tmp_path, monkeypatch):
    database_path = tmp_path / "reporting.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(Cluster(
            name="lab-sno",
            api_url="https://example.invalid:6443",
            token="not-used",
            environment="PROD",
            datacenter="HCI",
            tags=json.dumps({"owner": "platform"}),
        ))
        session.commit()

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: User(username="report-admin", role="admin")
    monkeypatch.setattr(reporting, "start_report_run", lambda run_id: None)

    try:
        async def request(method, path, **kwargs):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, path, **kwargs)

        clusters = asyncio.run(request("GET", "/api/reporting/clusters"))
        assert clusters.status_code == 200
        cluster_id = clusters.json()[0]["id"]

        page = asyncio.run(request("GET", "/reports"))
        assert page.status_code == 200
        assert "New Template" in page.text
        assert "Saved Templates" in page.text
        assert 'id="template-menu"' in page.text
        assert 'id="run-menu"' in page.text
        assert 'id="report-results-columns"' in page.text
        assert 'class="reports-layout"' not in page.text

        config = {
            "cluster_roles": ["admin", "edit"],
            "namespace_exclusions": ["openshift-*"],
            "user_condition": {
                "operator": "contains",
                "values": ["ssid", "shared-support"],
                "case_sensitive": False,
            },
        }
        created = asyncio.run(request("POST", "/api/reporting/templates", json={
            "name": "Production SSID access",
            "description": "Review privileged access",
            "config": config,
        }))
        assert created.status_code == 200
        assert created.json()["config"]["identity_exclusions"] == ["system:serviceaccount:*"]
        template_id = created.json()["id"]

        started = asyncio.run(request("POST", "/api/reporting/runs", json={
            "template_id": template_id,
            "cluster_ids": [cluster_id],
        }))
        assert started.status_code == 200
        run_id = started.json()["id"]

        with Session(engine) as session:
            cluster_run = session.exec(
                select(ReportRunCluster).where(ReportRunCluster.run_id == run_id)
            ).one()
            cluster_run.status = "COMPLETED"
            cluster_run.row_count = 1
            cluster_run.results_json = json.dumps([{
                "cluster": "lab-sno",
                "environment": "PROD",
                "datacenter": "HCI",
                "scope": "payments",
                "namespace": "payments",
                "binding_kind": "RoleBinding",
                "binding_name": "admins",
                "cluster_role": "admin",
                "subject_kind": "Group",
                "subject_name": "mixed-admins",
                "total_users": 2,
                "matching_users": ["ssid-support"],
                "nonmatching_users": ["alice"],
                "finding": "NONMATCHING_USER",
                "detail": "1 group member does not satisfy the condition",
            }])
            session.add(cluster_run)
            session.commit()

        details = asyncio.run(request("GET", f"/api/reporting/runs/{run_id}"))
        assert details.status_code == 200
        assert details.json()["clusters"][0]["row_count"] == 1

        results = asyncio.run(request("GET", f"/api/reporting/runs/{run_id}/results"))
        assert results.status_code == 200
        assert results.json()[0]["nonmatching_users"] == ["alice"]

        exported = asyncio.run(request("GET", f"/api/reporting/runs/{run_id}/export?format=csv"))
        assert exported.status_code == 200
        assert "NONMATCHING_USER" in exported.text
        assert "alice" in exported.text
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_report_worker_persists_results_summary_and_expiration(tmp_path, monkeypatch):
    database_path = tmp_path / "worker.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        cluster = Cluster(
            name="worker-lab",
            api_url="https://example.invalid:6443",
            token="not-used",
            environment="PROD",
            datacenter="HCI",
        )
        session.add(cluster)
        session.commit()
        session.refresh(cluster)
        run = ReportRun(
            template_name="Worker test",
            config_json=json.dumps({
                "cluster_roles": ["admin"],
                "user_condition": {"operator": "contains", "values": ["ssid", "shared-support"]},
            }),
            cluster_ids_json=json.dumps([cluster.id]),
            requested_by="tester",
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        session.add(ReportRunCluster(
            run_id=run.id,
            cluster_id=cluster.id,
            cluster_name=cluster.name,
        ))
        session.commit()
        run_id = run.id

    expected_rows = [{"finding": "NONMATCHING_USER", "cluster": "worker-lab"}]
    monkeypatch.setattr(reporting_service, "engine", engine)
    monkeypatch.setattr(reporting_service, "collect_rbac_report", lambda cluster, config: expected_rows)

    reporting_service.execute_report_run(run_id)

    with Session(engine) as session:
        run = session.get(ReportRun, run_id)
        cluster_run = session.exec(
            select(ReportRunCluster).where(ReportRunCluster.run_id == run_id)
        ).one()
        assert run.status == "COMPLETED"
        assert json.loads(run.summary_json) == {
            "row_count": 1,
            "findings": {"NONMATCHING_USER": 1},
            "cluster_count": 1,
            "completed_clusters": 1,
            "failed_clusters": 0,
        }
        assert 29 <= (run.expires_at - run.completed_at).days <= 30
        assert cluster_run.status == "COMPLETED"
        assert cluster_run.row_count == 1
        assert json.loads(cluster_run.results_json) == expected_rows

    engine.dispose()
