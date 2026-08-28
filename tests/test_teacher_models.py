import pytest

from server.teacher.models import TeacherCatalog, UpdateTeacherCatalog


def _topic(topic_id: str, point_ids: list[str]) -> dict[str, object]:
    return {
        "id": topic_id,
        "name": topic_id,
        "knowledge_points": [
            {"id": point_id, "name": point_id}
            for point_id in point_ids
        ],
    }


@pytest.mark.parametrize("model", [TeacherCatalog, UpdateTeacherCatalog])
def test_catalog_rejects_duplicate_topic_ids(model):
    data = {
        "topics": [_topic("topic", ["point-1"]), _topic("topic", ["point-2"])],
    }
    if model is TeacherCatalog:
        data["workspace_id"] = "workspace-1"

    with pytest.raises(ValueError, match="主题 ID 必须唯一"):
        model.model_validate(data)


@pytest.mark.parametrize("model", [TeacherCatalog, UpdateTeacherCatalog])
def test_catalog_rejects_duplicate_knowledge_point_ids_across_topics(model):
    data = {
        "topics": [_topic("topic-1", ["point"]), _topic("topic-2", ["point"])],
    }
    if model is TeacherCatalog:
        data["workspace_id"] = "workspace-1"

    with pytest.raises(ValueError, match="知识点 ID 必须唯一"):
        model.model_validate(data)
