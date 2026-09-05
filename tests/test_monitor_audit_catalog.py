from server.rbac.catalog import MENU_CATALOG, menu_row


def test_authorization_audit_is_catalogued_in_the_monitor_plane() -> None:
    audit_items = [item for item in MENU_CATALOG if item[1] == "审计日志"]

    assert len(audit_items) == 1
    item = audit_items[0]
    assert item[0] == "monitor.audit"
    assert item[2] == "/monitor?page=audit"
    assert menu_row(item)["client_scope"] == "monitor"
    assert all(item[2] != "/developer/audit" for item in MENU_CATALOG)
