"""Tests for ownership tag logic."""
from src.models.kuma import LiveMonitor
from src.models.desired import OWNER_TAG_NAME, owner_tag_value
from src.services.ownership import is_managed, get_identity_key, filter_managed, find_parent_id


def _live(monitor_id: int, name: str, mtype: str = "http", tag_value: str = None) -> LiveMonitor:
    tags = []
    if tag_value is not None:
        tags = [{"name": OWNER_TAG_NAME, "value": tag_value}]
    return LiveMonitor({"id": monitor_id, "name": name, "type": mtype, "tags": tags})


class TestIsManaged:
    def test_managed_monitor(self):
        m = _live(1, "test", tag_value="vector:default/test")
        assert is_managed(m) is True

    def test_unmanaged_no_tag(self):
        m = _live(2, "manual")
        assert is_managed(m) is False

    def test_unmanaged_wrong_prefix(self):
        m = _live(3, "other", tag_value="other-tool:default/test")
        assert is_managed(m) is False

    def test_unmanaged_empty_tag_value(self):
        m = _live(4, "empty", tag_value="")
        assert is_managed(m) is False


class TestGetIdentityKey:
    def test_extracts_key(self):
        m = _live(1, "test", tag_value="vector:monitoring/api")
        assert get_identity_key(m) == "monitoring/api"

    def test_returns_none_for_unmanaged(self):
        m = _live(2, "manual")
        assert get_identity_key(m) is None

    def test_returns_none_for_wrong_prefix(self):
        m = _live(3, "other", tag_value="foreigntool:ns/name")
        assert get_identity_key(m) is None


class TestFilterManaged:
    def test_filters_correctly(self):
        managed = _live(1, "m1", tag_value="vector:default/m1")
        unmanaged = _live(2, "m2")
        result = filter_managed([managed, unmanaged])
        assert result == [managed]

    def test_empty_list(self):
        assert filter_managed([]) == []

    def test_all_unmanaged(self):
        monitors = [_live(i, f"m{i}") for i in range(3)]
        assert filter_managed(monitors) == []

    def test_all_managed(self):
        monitors = [_live(i, f"m{i}", tag_value=f"vector:default/m{i}") for i in range(3)]
        assert len(filter_managed(monitors)) == 3


class TestFindParentId:
    def test_finds_group_by_name(self):
        group = _live(10, "my-group", mtype="group")
        http = _live(11, "my-service", mtype="http")
        assert find_parent_id("my-group", [group, http]) == 10

    def test_returns_none_when_not_found(self):
        http = _live(1, "service", mtype="http")
        assert find_parent_id("missing-group", [http]) is None

    def test_does_not_match_non_group_type(self):
        monitor = _live(5, "my-group", mtype="http")  # same name, wrong type
        assert find_parent_id("my-group", [monitor]) is None
