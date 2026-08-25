from pathlib import Path

import yaml


def test_web_and_worker_share_controlled_image_storage() -> None:
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "compose.yaml").read_text(encoding="utf-8"))

    assert "nova-data:/app/.data" in compose["services"]["nova-web"]["volumes"]
    assert "nova-data:/app/.data" in compose["services"]["nova-worker"]["volumes"]
    assert "nova-data" in compose["volumes"]
