from app.services.license import calculate_mapid_usage
from app.routers.dashboard import _add_project_mapids_to_breakdown
from types import SimpleNamespace
import json


class _FakeExecResult:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class _FakeSession:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def exec(self, _statement):
        return _FakeExecResult(self.snapshot)


def test_project_only_mapid_is_included_in_usage():
    projects = [
        {
            "metadata": {
                "name": "lir-ita-search",
                "labels": {
                    "mapid": "66294",
                    "lob": "retail"
                }
            }
        }
    ]

    usage = calculate_mapid_usage([], projects=projects)

    assert usage == [
        {
            "mapid": "66294",
            "lob": "retail",
            "node_count": 0,
            "total_vcpu": 0.0,
            "license_count": 0
        }
    ]


def test_project_mapid_does_not_double_count_node_usage():
    nodes = [
        {
            "metadata": {
                "name": "worker-1",
                "labels": {
                    "mapid": "66294",
                    "lob": "retail"
                }
            },
            "status": {
                "capacity": {
                    "cpu": "8"
                }
            }
        }
    ]
    projects = [
        {
            "metadata": {
                "name": "lir-ita-search",
                "labels": {
                    "mapid": "66294"
                }
            }
        }
    ]

    usage = calculate_mapid_usage(nodes, default_include=True, projects=projects)

    assert usage == [
        {
            "mapid": "66294",
            "lob": "retail",
            "node_count": 1,
            "total_vcpu": 8.0,
            "license_count": 2
        }
    ]


def test_breakdown_project_counts_are_added_per_cluster():
    snapshot = SimpleNamespace(
        data_json=json.dumps({
            "projects": [
                {"metadata": {"name": "a", "labels": {"mapid": "66294"}}},
                {"metadata": {"name": "b", "labels": {"mapid": "66294"}}},
                {"metadata": {"name": "c", "labels": {"mapid": "12345"}}},
            ]
        })
    )
    cluster = SimpleNamespace(id=7, name="HCI OnPrem NonProd", environment="DEV", datacenter="HCI")
    mapid_stats = {
        "66294": {
            "mapid": "66294",
            "lob": "-",
            "total_licenses": 0,
            "total_nodes": 0,
            "total_projects": 0,
            "total_vcpu": 0.0,
            "clusters": []
        }
    }

    _add_project_mapids_to_breakdown(_FakeSession(snapshot), {cluster.id: cluster}, mapid_stats)

    assert mapid_stats["66294"]["total_projects"] == 2
    assert mapid_stats["66294"]["clusters"][0]["projects"] == 2
    assert mapid_stats["12345"]["total_projects"] == 1
    assert mapid_stats["12345"]["clusters"][0]["projects"] == 1
