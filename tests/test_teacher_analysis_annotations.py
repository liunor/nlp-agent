import pytest
from pydantic import ValidationError

from server.teacher.models import TeacherAnalysisAnnotations, UpdateTeacherAnalysisAnnotations
from server.teacher.service import TeacherService


class FakeGateway:
    def __init__(self):
        self.settings: dict[str, object] = {}
        self.revision = 0

    async def get_user_settings(self, _principal):
        return {
            "revision": self.revision,
            "settings": dict(self.settings),
            "updated_at": "2026-08-31T00:00:00Z",
        }

    async def update_user_settings(self, _principal, changes):
        self.settings.update(changes)
        self.revision += 1
        return {"revision": self.revision, "updated_at": "2026-08-31T00:00:01Z"}


def make_service():
    service = TeacherService()
    service.require_teacher = lambda *_args, **_kwargs: None
    return service


@pytest.mark.asyncio
async def test_analysis_annotations_defaults_when_unset():
    service = make_service()
    gateway = FakeGateway()

    result = await service.analysis_annotations(None, gateway, "workspace-1")

    assert result["annotations"] == {
        "workspace_id": "workspace-1",
        "focused": [],
        "ignored": [],
        "notes": {},
    }
    assert result["revision"] == 0


@pytest.mark.asyncio
async def test_update_analysis_annotations_persists_into_user_settings():
    service = make_service()
    gateway = FakeGateway()
    body = UpdateTeacherAnalysisAnnotations(
        focused=["kp-1"],
        ignored=["kp-2"],
        notes={"kp-3": "跟进课后练习"},
    )

    updated = await service.update_analysis_annotations(None, gateway, "workspace-1", body)

    assert updated["annotations"]["focused"] == ["kp-1"]
    assert updated["annotations"]["ignored"] == ["kp-2"]
    assert updated["annotations"]["notes"] == {"kp-3": "跟进课后练习"}
    # Stored under the per-workspace settings key.
    assert "teacher_analysis_annotations:workspace-1" in gateway.settings


@pytest.mark.asyncio
async def test_analysis_annotations_returns_previously_stored_value():
    service = make_service()
    gateway = FakeGateway()
    await service.update_analysis_annotations(
        None,
        gateway,
        "workspace-1",
        UpdateTeacherAnalysisAnnotations(focused=["kp-1"], ignored=["kp-2"], notes={"kp-3": "跟进"}),
    )

    result = await service.analysis_annotations(None, gateway, "workspace-1")

    assert result["annotations"]["focused"] == ["kp-1"]
    assert result["annotations"]["ignored"] == ["kp-2"]
    assert result["annotations"]["notes"] == {"kp-3": "跟进"}
    assert result["revision"] == 1


@pytest.mark.asyncio
async def test_update_does_not_clobber_unrelated_settings():
    service = make_service()
    gateway = FakeGateway()
    gateway.settings["teacher_goals:workspace-1"] = {"course_title": "NLP 基础"}

    await service.update_analysis_annotations(
        None,
        gateway,
        "workspace-1",
        UpdateTeacherAnalysisAnnotations(focused=["kp-1"], ignored=[], notes={}),
    )

    assert gateway.settings["teacher_goals:workspace-1"]["course_title"] == "NLP 基础"
    assert gateway.settings["teacher_analysis_annotations:workspace-1"]["focused"] == ["kp-1"]


def test_analysis_annotations_reject_overlong_note():
    with pytest.raises(ValidationError):
        UpdateTeacherAnalysisAnnotations(focused=[], ignored=[], notes={"kp-1": "x" * 2001})


def test_analysis_annotations_accept_note_at_limit():
    body = UpdateTeacherAnalysisAnnotations(focused=[], ignored=[], notes={"kp-1": "x" * 2000})
    assert body.notes["kp-1"] == "x" * 2000


def test_analysis_annotations_reject_too_many_notes():
    with pytest.raises(ValidationError):
        TeacherAnalysisAnnotations(
            workspace_id="workspace-1",
            notes={f"kp-{index}": "跟进" for index in range(201)},
        )