from __future__ import annotations

import uuid

from gateway.whiteboard_bundled import load_bundled_whiteboard_items


def test_bundled_whiteboard_items_are_stable_named_shared_records() -> None:
    first = load_bundled_whiteboard_items()
    second = load_bundled_whiteboard_items()

    assert first
    assert first == second
    assert len({item["id"] for item in first}) == len(first)
    assert all(uuid.UUID(item["id"]) for item in first)
    assert all(item["asset_code"].startswith("WB-") for item in first)
    assert all(item["name"].strip() for item in first)
    assert all(item["status"] == "published" for item in first)
    assert all(item["elements"] for item in first)


def test_legacy_bubbles_receive_explicit_names_instead_of_random_client_ids() -> None:
    bubbles = [
        item
        for item in load_bundled_whiteboard_items()
        if item["source_key"].startswith("bubbles:")
    ]

    assert [item["name"] for item in bubbles] == [
        "对话气泡 01",
        "对话气泡 02",
        "对话气泡 03",
        "对话气泡 04",
    ]
    assert len({item["id"] for item in bubbles}) == 4
