from configs.settings import Settings
from server.quota.rollout import QuotaRollout


def test_rollout_allowlists_can_enable_specific_users_or_workspaces():
    rollout = QuotaRollout(
        global_enabled=False,
        percentage=0,
        user_ids=frozenset({"user-a"}),
        workspace_ids=frozenset({"workspace-b"}),
    )

    assert rollout.configured is True
    assert rollout.enabled_for("user-a", "workspace-a") is True
    assert rollout.enabled_for("user-b", "workspace-b") is True
    assert rollout.enabled_for("user-b", "workspace-a") is False


def test_rollout_percentage_is_deterministic_for_a_user_workspace_pair():
    rollout = QuotaRollout(
        global_enabled=True,
        percentage=25,
        user_ids=frozenset(),
        workspace_ids=frozenset(),
    )

    first = rollout.enabled_for("user-a", "workspace-a")
    assert first == rollout.enabled_for("user-a", "workspace-a")
    assert rollout.enabled_for("user-a", "workspace-a") in {True, False}


def test_rollout_percentage_can_be_enabled_without_global_allocation():
    rollout = QuotaRollout(percentage=100)

    assert rollout.enabled_for("user-a", "workspace-a") is True


def test_global_switch_uses_all_users_when_percentage_is_zero():
    rollout = QuotaRollout.from_config(
        {"quota_enforcement_percentage": 0}, global_enabled=True
    )

    assert rollout.enabled_for("user-a", "workspace-a") is True


def test_direct_global_rollout_with_zero_percentage_enables_all_users():
    assert QuotaRollout(global_enabled=True).enabled_for(
        "user-a", "workspace-a"
    ) is True


def test_settings_reports_rollout_configuration_without_turning_everyone_on():
    configured = Settings(_env_file=None)
    configured._config = {
        "gateway": {
            "quota_enforcement": False,
            "quota_enforcement_users": ["user-a"],
            "quota_enforcement_workspaces": [],
            "quota_enforcement_percentage": 0,
        }
    }

    assert configured.quota_enforcement_enabled is True
    assert configured.quota_enforcement_for("user-a", "workspace-a") is True
    assert configured.quota_enforcement_for("user-b", "workspace-a") is False
