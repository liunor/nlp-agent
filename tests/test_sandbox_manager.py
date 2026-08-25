from __future__ import annotations


def test_refill_plan_counts_only_pristine_ready_slots() -> None:
    from server.sandbox.manager import refill_deficit

    assert refill_deficit(target=3, ready_count=1, creating_count=1) == 1
    assert refill_deficit(target=2, ready_count=3, creating_count=0) == 0


def test_manager_reconcile_never_adopts_an_orphaned_container() -> None:
    from server.sandbox.manager import reconcile_actions

    actions = reconcile_actions(database_ids={"known"}, docker_ids={"known", "orphan"})

    assert actions.mark_missing_failed == set()
    assert actions.destroy_orphans == {"orphan"}
