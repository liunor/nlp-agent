from scripts.seed_knowledge_book_demo import _pages


def test_attention_demo_covers_the_full_practice_path() -> None:
    page = next(item for item in _pages("workspace-1") if item["knowledge_point_id"] == "demo-attention")
    content = str(page["content_markdown"])

    for heading in (
        "## 10.1 注意力提示",
        "## 10.2 注意力汇聚：从核回归到可学习权重",
        "## 10.3 注意力评分函数",
        "## 10.4 Bahdanau 注意力",
        "## 10.5 多头注意力",
        "## 10.6 自注意力和位置编码",
        "## 10.7 Transformer",
    ):
        assert heading in content
    assert content.count("```python") >= 10
    assert "## 动手练习" in content
