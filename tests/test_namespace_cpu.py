import csv
import io
from datetime import date, datetime, timedelta
from unittest.mock import Mock

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.dependencies import get_current_user
from app.main import app
from app.models import AppConfig, Cluster, NamespaceCPUCollection, NamespaceCPUDaily, User
from app.services import namespace_cpu


def test_namespace_cpu_collection_retention_and_csv(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'cpu.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(namespace_cpu, "engine", engine)
    today = namespace_cpu.eastern_today()
    yesterday = today - timedelta(days=1)
    old_day = yesterday - timedelta(days=1)
    day_start, day_end = namespace_cpu.day_bounds(yesterday)
    with Session(engine) as session:
        session.add_all([
            Cluster(id=1, name="enabled", api_url="https://api.example.test", token="test",
                    thanos_url="https://thanos.example.test", namespace_cpu_enabled=True,
                    namespace_cpu_since=yesterday),
            Cluster(id=2, name="disabled", api_url="https://api.example.test", token="test"),
            AppConfig(key="SNAPSHOT_RETENTION_DAYS", value="1"),
            NamespaceCPUCollection(cluster_id=1, day=yesterday, status="Collected"),
            NamespaceCPUCollection(cluster_id=2, day=old_day, status="No data"),
        ])
        session.commit()

    def get_range(url, *, params, **options):
        assert url == "https://thanos.example.test/api/v1/query_range"
        assert params["start"] == (day_start + timedelta(hours=1)).isoformat()
        assert params["end"] == day_end.isoformat()
        assert params["step"] == "1h"
        assert options["allow_redirects"] is False
        assert options["verify"] is False
        first_hour = datetime.fromisoformat(params["start"]).timestamp()
        last_hour = datetime.fromisoformat(params["end"]).timestamp()
        samples = {
            namespace_cpu.USAGE_QUERY: [[first_hour, "0"], [last_hour, "2"]],
            namespace_cpu.QUOTA_QUERY: [[first_hour, "4"], [last_hour, "6"]],
            namespace_cpu.AMBIGUOUS_QUERY: [],
            namespace_cpu.MAPID_QUERY: [],
        }
        values = samples[params["query"]]
        matrix = [{"metric": {"namespace": "team"}, "values": values}] if values else []
        return Mock(status_code=200, json=lambda: {
            "status": "success", "data": {"resultType": "matrix", "result": matrix},
        })

    http = Mock()
    http.headers = {}
    http.get.side_effect = get_range
    session_context = Mock()
    session_context.__enter__ = Mock(return_value=http)
    session_context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(namespace_cpu.requests, "Session", lambda: session_context)
    namespace_cpu.collect_namespace_cpu()
    namespace_cpu.collect_namespace_cpu()
    assert http.get.call_count == 4
    assert http.headers["Authorization"] == "Bearer test"
    with Session(engine) as session:
        assert session.get(NamespaceCPUCollection, (2, old_day)) is None
        assert session.exec(select(NamespaceCPUDaily)).one().cluster_id == 1

    def override_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, override_session)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: User(username="admin", role="admin"))
    client = TestClient(app)
    base = "/api/reporting/namespace-cpu"
    assert [cluster["id"] for cluster in client.get(base + "/clusters").json()] == [1]
    params = {"start": yesterday.isoformat(), "end": yesterday.isoformat(), "cluster_ids": 1}
    report = client.get(base, params=params)
    assert report.status_code == 200
    row = report.json()["rows"][0]
    assert row["avg_vcpu"] == 1
    assert row["hours_with_data"] == 2
    assert row["mapid"] == ""
    assert row["quota_status"] == "Changed during day"
    assert row["cpu_limit_quota_vcpu"] == 6
    assert row["date_et"] == yesterday.isoformat()
    assert client.get(base).json()["rows"] == [row]
    assert client.get(base, params={"environment": "dev", "search": "TEAM"}).json()["rows"] == [row]
    assert client.get(base, params={"environment": "prod", "search": "team"}).json()["rows"] == []
    assert client.get(base, params={"search": "%"}).json()["rows"] == []

    export = client.get(base + "/export", params=params)
    assert export.status_code == 200
    exported = next(csv.DictReader(io.StringIO(export.text)))
    assert exported["avg_vcpu_00_et"] == "0.00"
    assert exported["avg_vcpu_01_et"] == ""
    assert exported["avg_vcpu_23_et"] == "2.00"
    assert exported["cpu_quota_vcpu_23_et"] == "6.00"
    subset = client.get(base + "/export", params={**params, "fields": ["date_et", "cpu_limit_quota_vcpu"]})
    assert list(csv.reader(io.StringIO(subset.text))) == [
        ["date_et", "cpu_limit_quota_vcpu"], [yesterday.isoformat(), "6.00"],
    ]
    assert client.get(base + "/export", params={**params, "fields": "invalid"}).status_code == 422
    filtered = client.get(base + "/export", params={"search": "missing", "fields": "namespace"})
    assert list(csv.reader(io.StringIO(filtered.text))) == [["namespace"]]
    assert client.get(base, params={**params, "end": today.isoformat()}).status_code == 422
    assert 'id="cpu-visible-scope"' in client.get("/reports?tab=namespace-cpu").text

    disabled = client.patch("/api/admin/clusters/1", json={"namespace_cpu_enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["namespace_cpu_enabled"] is False
    assert client.get(base + "/clusters").json()[0]["collecting"] is False
    assert len(client.get(base, params=params).json()["rows"]) == 1

    with Session(engine) as session:
        existing = session.exec(select(NamespaceCPUDaily)).one()
        extra = existing.model_dump()
        extra.update(namespace="another-team", environment="UAT", usage_json="[8]", quota_json="[12]")
        session.add(NamespaceCPUDaily(**extra))
        session.commit()

    filters = [("environment", "dev"), ("environment", "uat"), ("search", "TEAM"),
               ("sort", "avg_vcpu"), ("descending", "true")]
    sorted_rows = client.get(base, params=filters).json()["rows"]
    assert [row["namespace"] for row in sorted_rows] == ["another-team", "team"]
    download = client.get(base + "/export", params=[*filters, ("fields", "namespace")])
    assert list(csv.reader(io.StringIO(download.text))) == [["namespace"], ["another-team"], ["team"]]

    assert client.post(base + "/run-now/1").status_code == 409
    assert client.post(base + "/run-now/2").status_code == 409
    assert http.get.call_count == 4

    enabled = client.patch("/api/admin/clusters/1", json={"namespace_cpu_enabled": True})
    assert enabled.status_code == 200
    with Session(engine) as session:
        assert session.get(Cluster, 1).namespace_cpu_since == today
    assert 'id="cpu-open-run"' in client.get("/reports?tab=namespace-cpu").text

    run_url = base + "/run-now/1"
    with Session(engine) as session:
        session.add(AppConfig(key="NAMESPACE_CPU_AUTO_COLLECT", value="false"))
        session.delete(session.get(NamespaceCPUCollection, (1, yesterday)))
        session.get(Cluster, 1).namespace_cpu_since = yesterday
        session.commit()
    namespace_cpu.collect_namespace_cpu()
    assert http.get.call_count == 4
    started = client.post(run_url)
    assert started.status_code == 202
    assert started.json() == {"day": yesterday.isoformat(), "cluster_id": 1}
    status = client.get(run_url, params={"day": yesterday.isoformat()})
    assert status.json()["status"] == "Collected"
    assert http.get.call_count == 8
    assert len(list(csv.DictReader(io.StringIO(client.get(base + "/export", params=params).text)))) == 1

    http.get.side_effect = None
    http.get.return_value = Mock(status_code=503)
    monkeypatch.setattr(namespace_cpu, "sleep", lambda seconds: None)
    assert client.post(run_url).status_code == 202
    failed = client.get(run_url, params={"day": yesterday.isoformat()}).json()
    assert failed["status"] == "Failed"
    assert "HTTP 503" in failed["detail"]
    assert http.get.call_count == 11
    assert len(client.get(base, params=params).json()["rows"]) == 1

    http.get.side_effect = namespace_cpu.requests.ReadTimeout
    assert client.post(run_url).status_code == 202
    timed_out = client.get(run_url, params={"day": yesterday.isoformat()}).json()
    assert timed_out["status"] == "Failed"
    assert "timed out after 3 attempts" in timed_out["detail"]
    assert timed_out["collected_days"] == []
    assert http.get.call_count == 14

    from app.routers import namespace_cpu as report_api

    with Session(engine) as session:
        session.get(AppConfig, "SNAPSHOT_RETENTION_DAYS").value = "2"
        session.commit()
    collect = Mock(side_effect=[
        namespace_cpu.CollectionResult("Failed", "Thanos returned HTTP 503"),
        namespace_cpu.CollectionResult("Collected"),
    ])
    monkeypatch.setattr(report_api, "collect_day", collect)
    dates = {"start": old_day.isoformat(), "end": yesterday.isoformat()}
    assert client.post(run_url, params=dates).status_code == 202
    outcome = client.get(run_url, params=dates).json()
    assert outcome["status"] == "Partial"
    assert outcome["completed"] == outcome["total"] == 2
    assert outcome["collected_days"] == [yesterday.isoformat()]
    assert [call.args[1] for call in collect.call_args_list] == [old_day, yesterday]
    assert client.post(run_url, params={**dates, "end": today.isoformat()}).status_code == 422

    with Session(engine) as session:
        previous = session.exec(select(NamespaceCPUDaily)).one().model_dump()
        previous["day"] = old_day
        session.add(NamespaceCPUDaily(**previous))
        session.commit()
    monkeypatch.setattr(report_api, "PAGE_SIZE", 1)
    first = client.get(base, params=dates).json()
    assert first["page_count"] == 2
    assert first["targets"] == [{"cluster": "enabled", "environment": "DEV", "id": 1}]
    assert first["has_more"]
    assert first["days"] == [{"day": day, "count": 1} for day in (dates["end"], dates["start"])]
    assert client.get(base, params={**dates, "page": 2}).json()["rows"][0]["date_et"] == dates["start"]
    assert client.get(base, params={**dates, "report_day": dates["start"]}).json()["total"] == 1
    exported_range = client.get(base + "/export", params={**dates, "fields": "date_et"})
    assert list(csv.reader(io.StringIO(exported_range.text))) == [["date_et"], [dates["end"]], [dates["start"]]]
    run_results = {**dates, "collected_day": f"1:{outcome['collected_days'][0]}"}
    assert [row["date_et"] for row in client.get(base, params=run_results).json()["rows"]] == [dates["end"]]
    run_csv = client.get(base + "/export", params={**run_results, "fields": "date_et"})
    assert list(csv.reader(io.StringIO(run_csv.text))) == [["date_et"], [dates["end"]]]

    with Session(engine) as session:
        session.add(NamespaceCPUDaily(**{**previous, "cluster_id": 2, "cluster_name": "disabled"}))
        session.commit()
    filtered = client.get(base, params={**dates, "result_cluster_id": 2}).json()
    assert {target["id"] for target in filtered["targets"]} == {1, 2}
    assert filtered["total"] == 1
    assert filtered["rows"][0]["cluster"] == "disabled"
    filtered_csv = client.get(base + "/export", params={**dates, "result_cluster_id": 2, "fields": "cluster"})
    assert list(csv.reader(io.StringIO(filtered_csv.text))) == [["cluster"], ["disabled"]]

    fall_back_day = date(2024, 11, 3)
    monkeypatch.setattr(report_api, "eastern_today", lambda: fall_back_day + timedelta(days=1))
    with Session(engine) as session:
        session.add(NamespaceCPUDaily(**{
            **previous, "day": fall_back_day, "usage_json": "[0.0, 1.0, 10.0, 2.0]",
        }))
        session.commit()
    fall_export = client.get(base + "/export", params={
        "start": fall_back_day.isoformat(), "end": fall_back_day.isoformat(),
        "fields": ["sum_avg_vcpu_hours", "hourly_usage"],
    })
    exported = next(csv.DictReader(io.StringIO(fall_export.text)))
    assert len(exported) == 25
    assert exported["avg_vcpu_01_et"] == "1.00"
    assert exported["avg_vcpu_02_et"] == "2.00"
    assert "avg_vcpu_01_repeat_et" not in exported
    assert exported["sum_avg_vcpu_hours"] == "13.00"



def test_eastern_report_hours_follow_daylight_saving():
    spring = namespace_cpu.report_hours(date(2026, 3, 8))
    assert len(spring) == 23
    assert [hour.strftime("%H:%M %Z") for hour in spring[:3]] == ["00:00 EST", "01:00 EST", "03:00 EDT"]

    fall = namespace_cpu.report_hours(date(2026, 11, 1))
    assert len(fall) == 25
    assert [hour.strftime("%H:%M %Z") for hour in fall[:4]] == [
        "00:00 EDT", "01:00 EDT", "01:00 EST", "02:00 EST",
    ]
    assert [hour.fold for hour in fall[:4]] == [0, 0, 1, 0]
