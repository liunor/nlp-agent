from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_LIBRARY_ROOT = _PROJECT_ROOT / "webui" / "public" / "excalidraw" / "libraries"
_BUNDLED_ITEM_NAMESPACE = uuid.UUID("89af7729-4628-5da5-9e97-cfa213329ab4")
_LEGACY_NAMES: dict[str, str] = {
    "bubbles": "对话气泡",
}


def _library_entries(payload: dict[str, Any]) -> list[Any]:
    entries = payload.get("libraryItems")
    if isinstance(entries, list):
        return entries
    entries = payload.get("library")
    return entries if isinstance(entries, list) else []


def load_bundled_whiteboard_items(
    library_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Load the vendored Excalidraw catalog as stable shared-library rows.

    Excalidraw v1 libraries store only arrays of elements. Loading those files
    in the browser creates a new random library-item id on every page load.
    Assigning ids and names here makes the database the single source of truth,
    so rename/delete/reference protection work for old and new materials alike.
    """

    root = library_root or _LIBRARY_ROOT
    result: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.excalidrawlib"), key=lambda item: item.name):
        payload = json.loads(path.read_text(encoding="utf-8"))
        stem = path.stem
        for index, entry in enumerate(_library_entries(payload), start=1):
            if isinstance(entry, list):
                elements = entry
                explicit_name = ""
                created = 1
            elif isinstance(entry, dict):
                elements = entry.get("elements")
                explicit_name = str(entry.get("name") or "").strip()
                created = entry.get("created") or 1
            else:
                continue
            if not isinstance(elements, list) or not elements:
                continue

            source_key = f"{stem}:{index:02d}"
            item_id = str(uuid.uuid5(_BUNDLED_ITEM_NAMESPACE, source_key))
            fallback = _LEGACY_NAMES.get(stem, stem.replace("-", " ").title())
            name = explicit_name or f"{fallback} {index:02d}"
            result.append(
                {
                    "id": item_id,
                    "asset_code": f"WB-{item_id.replace('-', '')[:8].upper()}",
                    "status": "published",
                    "created": created,
                    "name": name,
                    "elements": elements,
                    "source_key": source_key,
                }
            )
    return result
