from app.services.compliance import condition_matches


def test_negative_string_conditions():
    assert condition_matches("production", "not_contains", "ssid")
    assert not condition_matches("prod-SSID-user", "not_contains", "ssid")
    assert condition_matches("prod", "not_equals", "dev")
    assert not condition_matches("prod", "not_equals", "prod")


def test_negative_list_conditions_require_no_matching_item():
    users = ["alice", "service-SSID-user"]

    assert condition_matches(users, "contains", "ssid")
    assert not condition_matches(users, "not_contains", "ssid")
    assert condition_matches(["alice", "bob"], "not_contains", "ssid")


def test_negative_existence_condition():
    assert condition_matches(None, "not_exists", "")
    assert not condition_matches([], "not_exists", "")
    assert not condition_matches("", "not_exists", "")
