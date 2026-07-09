from app.services.license import calculate_mapid_usage


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
