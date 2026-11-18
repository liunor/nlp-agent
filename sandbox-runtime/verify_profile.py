"""Offline functional acceptance for the CPU classroom image (no model download)."""
import importlib.metadata
import io
import json
import warnings


def main():
    import jieba
    import matplotlib.pyplot as plt
    import numpy as np
    import networkx as nx
    import pandas as pd
    import regex
    import scipy.linalg
    import seaborn as sns
    import sentencepiece
    import tokenizers
    import sympy
    import torch
    import yaml
    from PIL import Image
    from safetensors.torch import load, save
    from sklearn.feature_extraction.text import TfidfVectorizer
    from tqdm import tqdm
    from transformers import BertConfig, BertModel

    x = np.array([1, 2, 3], dtype=np.float32)
    np.testing.assert_array_equal(torch.from_numpy(x).numpy(), x)
    assert not torch.cuda.is_available()
    frame = pd.read_csv(io.StringIO("name,score\nAlice,95\nBob,85\n"))
    assert frame.score.mean() == 90
    excel = io.BytesIO()
    frame.to_excel(excel, index=False, engine="openpyxl")
    excel.seek(0)
    assert pd.read_excel(excel).shape == (2, 2)
    np.testing.assert_allclose(scipy.linalg.solve([[2, 0], [0, 2]], [2, 4]), [1, 2])
    assert TfidfVectorizer().fit_transform(["hello world", "hello python"]).shape == (2, 3)
    assert "中文" in jieba.lcut("中文分词测试")
    assert regex.findall(r"\p{Han}+", "abc中文") == ["中文"]
    assert yaml.safe_load("enabled: true")["enabled"] is True
    assert list(tqdm(range(2), disable=True)) == [0, 1]
    assert nx.shortest_path(nx.path_graph(3), 0, 2) == [0, 1, 2]
    symbol = sympy.Symbol("x")
    assert sympy.expand((symbol + 1)**2) == symbol**2 + 2*symbol + 1
    assert tokenizers.Tokenizer is not None and sentencepiece.SentencePieceProcessor is not None
    assert load(save({"x": torch.tensor([1])}))["x"].item() == 1
    config = BertConfig(vocab_size=32, hidden_size=16, num_hidden_layers=1,
                        num_attention_heads=2, intermediate_size=32)
    with torch.no_grad():
        assert BertModel(config)(torch.tensor([[1, 2]])).last_hidden_state.shape == (1, 2, 16)
    figure, ax = plt.subplots()
    sns.lineplot(x=[1, 2], y=[2, 3], ax=ax)
    ax.set_title("中文绘图测试")
    png = io.BytesIO()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        figure.savefig(png, format="png")
    assert not any("Glyph" in str(item.message) and "missing" in str(item.message) for item in captured)
    png.seek(0)
    assert Image.open(png).format == "PNG"
    plt.close(figure)
    packages = ("numpy", "pandas", "scipy", "scikit-learn", "matplotlib", "seaborn",
                "pillow", "openpyxl", "jieba", "regex", "tqdm", "pyyaml", "transformers",
                "tokenizers", "sentencepiece", "safetensors", "sympy", "networkx", "torch", "ipykernel")
    print(json.dumps({"status": "passed", "versions": {
        name: importlib.metadata.version(name) for name in packages}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
