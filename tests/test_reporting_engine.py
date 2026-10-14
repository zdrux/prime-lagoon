from app.models import Cluster, ReportRun, ReportTemplate
from app.services.reporting import analyze_rbac_access


def _cluster():
    return Cluster(
        id=7,
        name="lab-sno",
        api_url="https://example.invalid:6443",
        token="unused",
        environment="PROD",
        datacenter="HCI",
    )


def _config(operator="contains"):
    return {
        "cluster_roles": ["admin", "edit"],
        "namespace_exclusions": ["openshift-*"],
        "user_condition": {
            "quantifier": "ALL",
            "operator": operator,
            "value": "ssid",
            "case_sensitive": False,
        },
    }


def test_report_timestamp_defaults_are_timezone_aware():
    template = ReportTemplate(name="test", config_json="{}", created_by="tester")
    run = ReportRun(
        template_name="test",
        config_json="{}",
        cluster_ids_json="[]",
        requested_by="tester",
    )

    assert template.created_at.utcoffset() is not None
    assert template.updated_at.utcoffset() is not None
    assert run.created_at.utcoffset() is not None


def test_rbac_report_joins_groups_and_collects_all_subject_kinds():
    role_bindings = [
        {
            "metadata": {"name": "prod-admins", "namespace": "payments"},
            "roleRef": {"kind": "ClusterRole", "name": "admin"},
            "subjects": [
                {"kind": "Group", "name": "ssid-operators"},
                {"kind": "Group", "name": "mixed-operators"},
                {"kind": "User", "name": "alice"},
                {"kind": "ServiceAccount", "name": "deployer", "namespace": "payments"},
            ],
        },
        {
            "metadata": {"name": "excluded", "namespace": "openshift-monitoring"},
            "roleRef": {"kind": "ClusterRole", "name": "admin"},
            "subjects": [{"kind": "User", "name": "not-ssid"}],
        },
        {
            "metadata": {"name": "wrong-role", "namespace": "payments"},
            "roleRef": {"kind": "ClusterRole", "name": "view"},
            "subjects": [{"kind": "User", "name": "alice"}],
        },
    ]
    cluster_role_bindings = [
        {
            "metadata": {"name": "global-edit"},
            "roleRef": {"kind": "ClusterRole", "name": "edit"},
            "subjects": [
                {"kind": "Group", "name": "empty-group"},
                {"kind": "Group", "name": "system:authenticated"},
            ],
        }
    ]
    groups = [
        {"metadata": {"name": "ssid-operators"}, "users": ["SSID-one", "ssid-two"]},
        {"metadata": {"name": "mixed-operators"}, "users": ["ssid-three", "alice"]},
        {"metadata": {"name": "empty-group"}, "users": []},
    ]

    rows = analyze_rbac_access(_cluster(), role_bindings, cluster_role_bindings, groups, _config())

    assert len(rows) == 6
    by_subject = {(row["subject_kind"], row["subject_name"]): row for row in rows}
    assert by_subject[("Group", "ssid-operators")]["finding"] == "CLEAR"
    assert by_subject[("Group", "mixed-operators")]["finding"] == "NONMATCHING_USER"
    assert by_subject[("Group", "mixed-operators")]["nonmatching_users"] == ["alice"]
    assert by_subject[("User", "alice")]["finding"] == "NONMATCHING_USER"
    assert by_subject[("ServiceAccount", "payments/deployer")]["finding"] == "SERVICE_ACCOUNT"
    assert by_subject[("Group", "empty-group")]["finding"] == "EMPTY_GROUP_REVIEW"
    assert by_subject[("Group", "system:authenticated")]["finding"] == "SYSTEM_GROUP_REVIEW"


def test_not_contains_condition_is_applied_to_every_group_member():
    bindings = [{
        "metadata": {"name": "review", "namespace": "finance"},
        "roleRef": {"kind": "ClusterRole", "name": "admin"},
        "subjects": [{"kind": "Group", "name": "humans"}],
    }]
    groups = [{"metadata": {"name": "humans"}, "users": ["alice", "ssid-support"]}]

    rows = analyze_rbac_access(_cluster(), bindings, [], groups, _config("not_contains"))

    assert rows[0]["finding"] == "NONMATCHING_USER"
    assert rows[0]["nonmatching_users"] == ["ssid-support"]
