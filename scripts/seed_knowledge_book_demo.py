"""Seed a small, idempotent knowledge-book demo into one MySQL workspace.

This is deliberately a command-line fixture rather than an Alembic data
migration: teacher-authored catalogues must not be created for every production
workspace.  The command refuses to overwrite a workspace that already has
topics or book pages unless the explicit demo refresh flag is used for the
known demo catalogue.

Usage:
    uv run python -m scripts.seed_knowledge_book_demo --workspace-id <id>
    uv run python -m scripts.seed_knowledge_book_demo --workspace-id <id> --refresh-demo
"""

from __future__ import annotations

import argparse
import hashlib
import io
from typing import Any
from urllib.parse import quote

from PIL import Image, ImageDraw

from configs.settings import settings
from gateway.mysql_repository import MySQLGatewayRepository


def _workflow_png() -> bytes:
    image = Image.new("RGB", (960, 300), "#f8fafc")
    draw = ImageDraw.Draw(image)
    cards = [
        ("文本", "分词 / 编码", "#dbeafe"),
        ("Tensor", "批量化表示", "#dcfce7"),
        ("模型", "PyTorch 前向", "#fef3c7"),
        ("评估", "Loss / Accuracy", "#fce7f3"),
    ]
    left = 40
    for index, (title, subtitle, fill) in enumerate(cards):
        x = left + index * 230
        draw.rounded_rectangle((x, 90, x + 180, 210), radius=18, fill=fill, outline="#94a3b8", width=3)
        draw.text((x + 54, 120), title, fill="#0f172a")
        draw.text((x + 28, 158), subtitle, fill="#475569")
        if index < len(cards) - 1:
            draw.line((x + 180, 150, x + 215, 150), fill="#64748b", width=4)
            draw.polygon(((x + 215, 150), (x + 205, 143), (x + 205, 157)), fill="#64748b")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _catalog() -> dict[str, Any]:
    return {
        "topics": [
            {
                "id": "demo-python-pytorch",
                "name": "Python 与 PyTorch 基础",
                "description": "从张量、自动微分到数据加载，建立实操基础。",
                "status": "enabled",
                "knowledge_points": [
                    {"id": "demo-tensor", "name": "张量与形状", "markdown": "", "status": "enabled", "sort_order": 0},
                    {"id": "demo-autograd", "name": "自动微分", "markdown": "", "status": "enabled", "sort_order": 1},
                    {"id": "demo-dataloader", "name": "Dataset 与 DataLoader", "markdown": "", "status": "enabled", "sort_order": 2},
                ],
            },
            {
                "id": "demo-transformer",
                "name": "Transformer 核心组件",
                "description": "用可运行的 PyTorch 片段理解注意力与编码器。",
                "status": "enabled",
                "knowledge_points": [
                    {"id": "demo-embedding", "name": "Embedding 表示", "markdown": "", "status": "enabled", "sort_order": 0},
                    {"id": "demo-attention", "name": "Scaled Dot-Product Attention", "markdown": "", "status": "enabled", "sort_order": 1},
                    {"id": "demo-encoder", "name": "Transformer Encoder", "markdown": "", "status": "enabled", "sort_order": 2},
                ],
            },
            {
                "id": "demo-nlp-practice",
                "name": "NLP 实战流程",
                "description": "把文本处理、建模、训练和评估串成一个闭环。",
                "status": "enabled",
                "knowledge_points": [
                    {"id": "demo-tokenize", "name": "文本预处理", "markdown": "", "status": "enabled", "sort_order": 0},
                    {"id": "demo-classifier", "name": "文本分类器", "markdown": "", "status": "enabled", "sort_order": 1},
                    {"id": "demo-evaluate", "name": "损失与评估指标", "markdown": "", "status": "enabled", "sort_order": 2},
                ],
            },
        ],
        "exercise_blueprints": [],
        "review_blueprints": [],
        "guided_blueprints": [],
    }


def _page_markdown(workspace_id: str, point_id: str, title: str, body: str, image_path: str | None = None) -> str:
    image = ""
    if image_path:
        image_url = f"/api/v1/learning/book/{quote(workspace_id, safe='')}/assets/{quote(image_path, safe='/')}"
        image = f"\n\n![NLP 实战流程图]({image_url})"
    return f"# {title}\n\n## 核心概念\n\n{body}{image}\n\n## 动手练习\n\n请运行上面的 PyTorch 示例，修改一个参数并记录输出形状的变化。\n"


def _attention_body() -> str:
    return """注意力机制的核心不是让模型读取更多信息，而是让模型在当前步骤为不同信息分配不同权重。下面的例子沿着“查询—键—值”的数据流，从一个可解释的加权平均开始，逐步走到 Transformer 的编码器。

## 10.1 注意力提示

### 查询、键和值

把当前需要解决的问题看成查询（query），把可供匹配的线索看成键（key），把真正要汇总的内容看成值（value）。评分函数先比较 query 与每个 key 的相关程度，再把分数归一化成权重，最后对 value 加权求和。

```python
import torch

query = torch.tensor([[1.0, 0.0]])
keys = torch.tensor([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]])
values = torch.tensor([[10.0, 1.0], [2.0, 8.0], [6.0, 6.0]])

scores = query @ keys.T
weights = torch.softmax(scores, dim=-1)
context = weights @ values
print(weights)
print(context)
```

### 可视化权重

权重矩阵的每一行对应一个 query，每一列对应一个 key。实际调试时，可以先检查权重的形状、每行和是否为 1，再判断模型是否把注意力集中在合理位置。

```python
import torch

scores = torch.tensor([[1.2, 0.1, -0.4], [0.0, 0.8, 0.2]])
weights = torch.softmax(scores, dim=-1)
print(weights.shape)
print(weights.sum(dim=-1))
```

## 10.2 注意力汇聚：从核回归到可学习权重

### 生成一个带噪声的数据集

注意力汇聚可以先用回归问题理解：对于一个新的查询点，模型从附近的训练样本中取值。距离越近的样本通常应该得到更大的权重，但权重函数也可以通过训练学习出来。

```python
import torch

torch.manual_seed(7)
x_train = torch.sort(torch.rand(32) * 4 - 2).values
y_train = torch.sin(x_train) + 0.15 * torch.randn_like(x_train)
x_query = torch.linspace(-2, 2, 9)
print(x_train.shape, y_train.shape, x_query.shape)
```

### 非参数注意力汇聚

下面的高斯核只使用查询点与键之间的距离，不引入待学习参数。温度越小，权重越集中；温度越大，输出会更平滑。

```python
import torch

def gaussian_attention(query, keys, values, temperature=0.2):
    distance = (query[:, None] - keys[None, :]) ** 2
    weights = torch.softmax(-distance / temperature, dim=-1)
    return weights @ values

prediction = gaussian_attention(x_query, x_train, y_train)
print(prediction.shape)
```

### 带参数注意力汇聚

如果让温度成为可学习参数，模型就能根据数据调整“关注范围”。参数化前要保证温度为正，否则指数运算会失去稳定性。

```python
import torch
from torch import nn

class KernelRegressor(nn.Module):
    def __init__(self):
        super().__init__()
        self.log_temperature = nn.Parameter(torch.tensor(-1.0))

    def forward(self, query, keys, values):
        temperature = self.log_temperature.exp()
        scores = -(query[:, None] - keys[None, :]) ** 2 / temperature
        return torch.softmax(scores, dim=-1) @ values

model = KernelRegressor()
print(model(x_query, x_train, y_train).shape)
```

## 10.3 注意力评分函数

### 掩蔽 softmax

序列任务中，padding 位置或未来位置不能参与当前计算。常见做法是在 softmax 前把不可见位置替换成一个很小的数，这样归一化后它们的权重就接近零。

```python
import torch

scores = torch.tensor([[2.0, 1.0, 0.5], [0.3, 0.8, 1.4]])
visible = torch.tensor([[True, True, False], [True, False, False]])
masked_scores = scores.masked_fill(~visible, torch.finfo(scores.dtype).min)
weights = torch.softmax(masked_scores, dim=-1)
print(weights)
```

### 加性注意力

当 query 和 key 的维度不同时，可以先映射到共同的隐藏空间，再用一个小型前馈网络输出标量分数。这种方式表达力强，但计算量通常高于一次矩阵乘法。

```python
import torch
from torch import nn

class AdditiveScore(nn.Module):
    def __init__(self, query_size, key_size, hidden_size):
        super().__init__()
        self.query_proj = nn.Linear(query_size, hidden_size, bias=False)
        self.key_proj = nn.Linear(key_size, hidden_size, bias=False)
        self.score = nn.Linear(hidden_size, 1, bias=False)

    def forward(self, query, key):
        hidden = torch.tanh(self.query_proj(query) + self.key_proj(key))
        return self.score(hidden).squeeze(-1)

scorer = AdditiveScore(8, 12, 16)
print(scorer(torch.randn(2, 8), torch.randn(2, 12)).shape)
```

### 缩放点积注意力

当 query 和 key 具有相同维度时，点积可以高效地计算所有两两匹配分数。除以特征维度的平方根可以控制分数方差，避免 softmax 过早饱和。

```python
import math
import torch

query = torch.randn(2, 3, 16)
key = torch.randn(2, 5, 16)
value = torch.randn(2, 5, 24)
scores = query @ key.transpose(-2, -1) / math.sqrt(query.size(-1))
weights = torch.softmax(scores, dim=-1)
context = weights @ value
print(scores.shape, context.shape)
```

## 10.4 Bahdanau 注意力

### 编码器—解码器中的对齐

在机器翻译中，解码器每生成一个词，都需要从编码器的所有隐状态中选择相关信息。Bahdanau 注意力使用当前解码器状态作为 query，编码器隐状态作为 key 和 value，并将上下文向量送回解码器。

```python
import torch
from torch import nn

class BahdanauAttention(nn.Module):
    def __init__(self, query_size, key_size, hidden_size):
        super().__init__()
        self.query_proj = nn.Linear(query_size, hidden_size, bias=False)
        self.key_proj = nn.Linear(key_size, hidden_size, bias=False)
        self.score = nn.Linear(hidden_size, 1, bias=False)

    def forward(self, query, keys, values):
        hidden = torch.tanh(self.query_proj(query[:, None, :]) + self.key_proj(keys))
        scores = self.score(hidden).squeeze(-1)
        weights = torch.softmax(scores, dim=-1)
        return weights @ values, weights

attention = BahdanauAttention(16, 16, 32)
context, weights = attention(torch.randn(2, 16), torch.randn(2, 6, 16), torch.randn(2, 6, 16))
print(context.shape, weights.shape)
```

### 训练时要观察什么

先确认 padding mask 的方向和 batch 维度，再观察 loss 是否下降以及注意力是否集中在有效 token。可视化权重是定位“模型学不到对齐”问题的低成本手段，但不能代替验证集指标。

## 10.5 多头注意力

### 为什么需要多个头

单个注意力头只能形成一种加权关系。多头机制把隐藏维度切成若干子空间，让不同头分别关注局部邻近、长距离依赖或句法关系，最后再拼接并投影回原维度。

```python
import torch
from torch import nn

multi_head = nn.MultiheadAttention(embed_dim=32, num_heads=4, batch_first=True)
tokens = torch.randn(2, 7, 32)
context, weights = multi_head(tokens, tokens, tokens, need_weights=True)
print(context.shape, weights.shape)
```

### 形状检查

多头注意力要求 `embed_dim` 能被 `num_heads` 整除。输入采用 batch-first 后，约定为 `[batch, sequence, feature]`，这样与大多数数据加载器的输出更一致。

```python
import torch

batch, steps, features, heads = 2, 7, 32, 4
x = torch.randn(batch, steps, features)
head_size = features // heads
reshaped = x.reshape(batch, steps, heads, head_size).transpose(1, 2)
print(reshaped.shape)
```

## 10.6 自注意力和位置编码

### 自注意力与其他序列层的区别

自注意力让同一序列中的每个 token 都能直接访问其他 token。它对长距离依赖很友好，但标准实现需要计算序列位置之间的两两关系，序列很长时显存开销会明显增加。

```python
import torch
from torch import nn

encoder_layer = nn.TransformerEncoderLayer(d_model=32, nhead=4, batch_first=True)
encoder = nn.TransformerEncoder(encoder_layer, num_layers=2)
x = torch.randn(2, 12, 32)
y = encoder(x)
print(y.shape)
```

### 位置编码

注意力本身不区分 token 的先后顺序，因此需要显式加入位置信息。最小实践可以先用可学习的位置向量，长度超过上限时要在数据管道中截断或重新设计位置方案。

```python
import torch
from torch import nn

position = nn.Parameter(torch.zeros(1, 128, 32))
x = torch.randn(4, 20, 32)
y = x + position[:, :x.size(1)]
print(y.shape)
```

## 10.7 Transformer

### 编码器的组合

Transformer 编码器通常重复堆叠“多头自注意力、残差连接、层归一化、位置前馈网络”这条路径。每一层都保持序列长度和隐藏维度不变，便于继续堆叠。

```python
import torch
from torch import nn

layer = nn.TransformerEncoderLayer(d_model=64, nhead=8, dim_feedforward=128, batch_first=True)
encoder = nn.TransformerEncoder(layer, num_layers=3)
tokens = torch.randn(2, 16, 64)
encoded = encoder(tokens)
print(encoded.shape)
```

### 残差连接和层规范化

残差连接让梯度可以沿短路径传播，层规范化则把每个 token 的隐藏状态稳定在适合训练的范围。使用框架模块时，应先弄清楚它采用的是 pre-norm 还是 post-norm，再和论文结构对照。

```python
import torch
from torch import nn

x = torch.randn(2, 5, 32)
block = nn.Sequential(nn.LayerNorm(32), nn.Linear(32, 64), nn.GELU(), nn.Linear(64, 32))
y = x + block(x)
print(y.shape)
```

### 从注意力到可运行模型

学习顺序建议是：先用随机张量跑通形状，再加入 mask，然后检查梯度和 loss，最后才接入真实 tokenizer 与数据集。每一步都保留一个最小 PyTorch 例子，后续接入代码沙箱时可以直接作为实验入口。

"""


def _pages(workspace_id: str) -> list[dict[str, Any]]:
    return [
        {"knowledge_point_id": "demo-tensor", "content_markdown": _page_markdown(workspace_id, "demo-tensor", "张量与形状", "张量是带有统一数据类型的多维数组。实际建模时，先确认 batch、sequence 和 feature 维度，很多运行时错误都来自形状不一致。\n\n```python\nimport torch\n\nx = torch.arange(12, dtype=torch.float32).reshape(3, 4)\nprint(x.shape)\nprint(x.mean(dim=0))\n```\n\n尝试修改 `reshape` 的参数，并观察元素总数必须保持不变。")},
        {"knowledge_point_id": "demo-autograd", "content_markdown": _page_markdown(workspace_id, "demo-autograd", "自动微分", "PyTorch 会记录张量运算，调用 `backward()` 后把梯度累积到叶子张量。训练循环通常是清空梯度、前向计算、反向传播、更新参数。\n\n```python\nimport torch\n\nw = torch.tensor(2.0, requires_grad=True)\nloss = (w - 5) ** 2\nloss.backward()\nprint(w.grad)\n```\n\n这里的梯度是当前 loss 对参数 `w` 的偏导数。")},
        {"knowledge_point_id": "demo-dataloader", "content_markdown": _page_markdown(workspace_id, "demo-dataloader", "Dataset 与 DataLoader", "`Dataset` 负责定义单条样本，`DataLoader` 负责批量化、打乱和并行读取。\n\n```python\nimport torch\nfrom torch.utils.data import DataLoader, TensorDataset\n\nfeatures = torch.randn(32, 8)\nlabels = torch.randint(0, 2, (32,))\nloader = DataLoader(TensorDataset(features, labels), batch_size=8, shuffle=True)\nfor batch_x, batch_y in loader:\n    print(batch_x.shape, batch_y.shape)\n    break\n```\n\n先确认一批数据的形状，再把它接入模型。")},
        {"knowledge_point_id": "demo-embedding", "content_markdown": _page_markdown(workspace_id, "demo-embedding", "Embedding 表示", "Embedding 把离散 token id 映射到连续向量。词表大小是第一维，隐藏维度是第二维。\n\n```python\nimport torch\nfrom torch import nn\n\nembedding = nn.Embedding(num_embeddings=1000, embedding_dim=64)\ntokens = torch.tensor([[2, 8, 13], [5, 1, 9]])\nprint(embedding(tokens).shape)  # [2, 3, 64]\n```\n\n输入 token id 必须落在 `[0, num_embeddings)` 范围内。")},
        {"knowledge_point_id": "demo-attention", "content_markdown": _page_markdown(workspace_id, "demo-attention", "Scaled Dot-Product Attention", _attention_body())},
        {"knowledge_point_id": "demo-encoder", "content_markdown": _page_markdown(workspace_id, "demo-encoder", "Transformer Encoder", "编码器层通常由多头自注意力、残差连接、层归一化和前馈网络组成。先用 PyTorch 内置模块验证输入输出，再逐步拆解内部结构。\n\n```python\nimport torch\nfrom torch import nn\n\nlayer = nn.TransformerEncoderLayer(d_model=32, nhead=4, batch_first=True)\nx = torch.randn(2, 10, 32)\ny = layer(x)\nprint(y.shape)\n```\n\n`batch_first=True` 让张量形状保持为 `[batch, sequence, feature]`。")},
        {"knowledge_point_id": "demo-tokenize", "content_markdown": _page_markdown(workspace_id, "demo-tokenize", "文本预处理", "文本分类的第一步是把字符串变成模型可接受的 token id。真实项目中还要处理未知词、截断、padding 和 attention mask。\n\n```python\nimport torch\n\nvocab = {'我': 1, '喜欢': 2, 'NLP': 3, '<unk>': 0}\ntext = '我 喜欢 NLP'\nids = torch.tensor([vocab.get(token, vocab['<unk>']) for token in text.split()])\nprint(ids)\n```\n\n预处理规则需要和推理阶段保持一致。")},
        {"knowledge_point_id": "demo-classifier", "content_markdown": _page_markdown(workspace_id, "demo-classifier", "文本分类器", "一个最小分类器可以使用 Embedding、池化和线性层。先让数据流跑通，再考虑更复杂的编码器。\n\n```python\nimport torch\nfrom torch import nn\n\nclass Classifier(nn.Module):\n    def __init__(self):\n        super().__init__()\n        self.embedding = nn.Embedding(100, 16)\n        self.head = nn.Linear(16, 2)\n\n    def forward(self, tokens):\n        return self.head(self.embedding(tokens).mean(dim=1))\n\nprint(Classifier()(torch.randint(0, 100, (4, 6))).shape)\n```\n\n输出形状 `[batch, classes]` 可以直接交给交叉熵损失。")},
        {"knowledge_point_id": "demo-evaluate", "content_markdown": _page_markdown(workspace_id, "demo-evaluate", "损失与评估指标", "训练时优化损失函数，评估时还要关注准确率、召回率和 F1。不要只看一个指标，尤其是类别不平衡的数据集。\n\n```python\nimport torch\nfrom torch import nn\n\nlogits = torch.tensor([[2.0, 0.5], [0.2, 1.3]])\ntarget = torch.tensor([0, 1])\nloss = nn.CrossEntropyLoss()(logits, target)\nprediction = logits.argmax(dim=-1)\nprint(loss.item(), prediction.tolist())\n```\n\n把评估指标和业务目标联系起来，才能判断模型是否真的有用。", "assets/pages/demo-evaluate/evaluation-workflow.png")},
    ]


def seed(workspace_id: str, *, refresh_demo: bool = False) -> int:
    repository = MySQLGatewayRepository(settings.NLP_AGENT_DATABASE_URL)
    try:
        current = repository.get_teaching_catalog(workspace_id)
        catalog = _catalog()
        expected_point_ids = {
            point["id"]
            for topic in catalog["topics"]
            for point in topic["knowledge_points"]
        }
        current_point_ids = {
            point["id"]
            for topic in current["catalog"].get("topics", [])
            for point in topic.get("knowledge_points", [])
        }
        if current_point_ids and current_point_ids != expected_point_ids:
            raise RuntimeError("目标 workspace 已存在其他教师主题，拒绝覆盖现有教学内容")
        existing_pages = repository.list_knowledge_pages(workspace_id)
        if existing_pages and {
            str(page["knowledge_point_id"]) for page in existing_pages
        } == expected_point_ids and all(page.get("published_markdown") for page in existing_pages):
            if not refresh_demo:
                return 0
            image = _workflow_png()
            current_pages = {str(page["knowledge_point_id"]): page for page in existing_pages}
            pages = [
                {**page, "expected_revision": int(current_pages[page["knowledge_point_id"]]["revision"])}
                for page in _pages(workspace_id)
            ]
            repository.apply_knowledge_book_import(
                workspace_id,
                pages,
                [{
                    "asset_path": "assets/pages/demo-evaluate/evaluation-workflow.png",
                    "media_type": "image/png",
                    "content": image,
                    "sha256": hashlib.sha256(image).hexdigest(),
                }],
            )
            for page in pages:
                repository.publish_knowledge_page(
                    workspace_id,
                    page["knowledge_point_id"],
                    expected_revision=int(page["expected_revision"]) + 1,
                )
            return len(pages)
        if existing_pages:
            raise RuntimeError("目标 workspace 已存在教材页面，拒绝覆盖现有教学内容")

        if not current_point_ids:
            repository.update_teaching_catalog(workspace_id, catalog)
        image = _workflow_png()
        pages = [
            {**page, "expected_revision": 0}
            for page in _pages(workspace_id)
        ]
        repository.apply_knowledge_book_import(
            workspace_id,
            pages,
            [{
                "asset_path": "assets/pages/demo-evaluate/evaluation-workflow.png",
                "media_type": "image/png",
                "content": image,
                "sha256": hashlib.sha256(image).hexdigest(),
            }],
        )
        for page in pages:
            repository.publish_knowledge_page(
                workspace_id,
                page["knowledge_point_id"],
                expected_revision=1,
            )
        return len(pages)
    finally:
        repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="为一个空 workspace 写入 Nova 教材开发样例")
    parser.add_argument("--workspace-id", required=True, help="教师和学生共用的 workspace ID")
    parser.add_argument("--refresh-demo", action="store_true", help="仅刷新本脚本创建的 demo 目录正文，不覆盖其他 workspace 内容")
    args = parser.parse_args()
    count = seed(args.workspace_id, refresh_demo=args.refresh_demo)
    print(f"已为 workspace {args.workspace_id} 写入并发布 {count} 个教材知识点。")


if __name__ == "__main__":
    main()
