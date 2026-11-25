"""Read versioned file snapshots from the existing durable dispatch task."""
import json


def task_file_refs(payload) -> list[dict]:
    try:
        task = json.loads(payload) if isinstance(payload, str) else payload
        refs = task.get('file_refs', []) if isinstance(task, dict) else []
        if not isinstance(refs, list):
            return []
        return [dict(ref) for ref in refs[:5] if isinstance(ref, dict)
                and isinstance(ref.get('id'), str) and isinstance(ref.get('name'), str)]
    except (ValueError, TypeError):
        return []
