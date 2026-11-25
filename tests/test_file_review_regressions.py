from server.documents.generation import wrap_pdf_lines


def test_pdf_wrap_uses_measured_width_and_preserves_empty_lines():
    lines = wrap_pdf_lines("中" * 70 + "\n\nend", lambda s: len(s) * 11, 500)
    assert all(len(line) * 11 <= 500 for line in lines)
    assert "" in lines
    assert "".join(lines) == "中" * 70 + "end"
