# 图片理解（Image Understanding）

Nova 支持端到端的图片理解流程：用户上传图片 → 提问 → Coordinator 派发
`visual_researcher` Worker → `image_analyze` 工具真实完成
OCR/表格/图表/描述/问答 → 结果带置信度和引用返回前端。

## 架构概览

```
┌─────────┐     multipart      ┌──────────┐      WS chat.send
│  前端    │ ─── POST ──────→  │ Upload   │  ←── + attachments ──→ Gateway
│ Composer │                   │ API      │
└────┬─────┘                   └──────────┘           │
     │ GET /uploads/{s}/{f}                           ▼
     │                                         ┌─────────────┐
     │                                         │ Coordinator  │
     │                                         │ (附件块注入) │
     │                                         └──────┬──────┘
     │                                                │ spawn_worker
     │                                                ▼
     │                                         ┌─────────────┐
     │                                         │ visual_     │
     │◄──── 置信度 + 引用 ────────────────────── │ researcher  │
     │                                         └──────┬──────┘
     │                                                │ image_analyze
     │                                                ▼
     │                                ┌───────────────────────────────┐
     │                                │     ImageAnalyzeService       │
     │                                │  ┌─────────┐  ┌───────────┐  │
     │                                │  │ OCR     │  │ Signal    │  │
     │                                │  │ RapidOCR│  │ OpenCV    │  │
     │                                │  └─────────┘  └───────────┘  │
     │                                │  ┌─────────┐  ┌───────────┐  │
     │                                │  │ VLM     │  │ Router    │  │
     │                                │  │ Qwen VL │  │ 确定性    │  │
     │                                │  └─────────┘  └───────────┘  │
     │                                └───────────────────────────────┘
```

## 上传 API

### POST `/api/v1/uploads`

**认证**: Cookie + CSRF Token（`X-CSRF-Token` header）

**请求**: `multipart/form-data`

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | 当前会话 ID |
| `file` | binary | 图片文件（JPEG/PNG/WebP，≤10MB） |

**响应**: `201 Created`

```json
{
  "file_name": "a1b2c3d4e5f6.jpg",
  "url": "/api/v1/uploads/{session_id}/a1b2c3d4e5f6.jpg",
  "media_type": "image/jpeg",
  "size_bytes": 245760,
  "width": 1024,
  "height": 768,
  "sha256": "e3b0c44298fc1c149afbf4c8996fb924..."
}
```

### GET `/api/v1/uploads/{session_id}/{file_name}`

**认证**: Cookie

返回原图，带 `X-Content-Type-Options: nosniff`。

## auto 路由信号

| 信号 | 检测方法 | 触发路由 |
|---|---|---|
| `has_grid_lines` | 形态学开运算检测长水平/垂直线交叉 | → `table` (fusion) |
| `has_axes` | 左侧垂直线 + 底部水平线组成 L 形 | → `chart` (fusion) |
| `text_coverage ≥ 0.15` | OCR 文本区域占图片面积比 | → `ocr` (ocr) |
| `aligned_text_ratio ≥ 0.65` | 文本行左边缘形成多列对齐 | → `table` (fusion) |
| `image_category = formula` | 低文本密度 + 高数学符号占比 | → `formula` (fusion) |
| 以上均不满足 | 默认 | → `describe` (vlm) |

## OCR 引擎

- **Provider**: RapidOCR（`rapidocr-onnxruntime`，ONNX 推理）
- **模型**: PP-OCRv4（中英混合），首次运行自动下载 ~10MB
- **预处理**: EXIF 自动旋转、超 `max_dimension` 降采样
- **重试**: 低置信度时 2x 上采样重试一次
- **配置**: `configs/agent_config.yaml` → `tools.vision.ocr`

## VLM 与本地计价前置条件

- 选择 DeepSeek、Qwen、Kimi 或 GLM 对话档案不会改变图片模型；图片语义理解固定走 `vision-worker` → `qwen3-vl-plus`。
- 百炼华北 2（北京）`qwen3-vl-plus` 的 32K 以内官方标准价为输入 ¥1/MTok、输出 ¥10/MTok、缓存命中输入 ¥0.2/MTok。图片 Token 包含在输入 Token 中。
- 本地数据库必须存在有效的 `qwen/qwen3-vl-plus` PricingRule。部署和本地升级统一运行 `python main.py bootstrap-db`，该命令会在 Alembic 迁移后自动安装并检查规则；手动定价脚本只保留为运维工具。缺少该规则且配额已开启时，引导会失败，不会等到用户上传图片后才暴露问题。
- 图片能力只依赖 `QWEN_API_KEY`。`KIMI_API_KEY` 或 `GLM_API_KEY` 是否存在不会参与视觉路由构建；反过来，Qwen Key 缺失也不会阻止其他模型的纯文本聊天。
- 价格证据：[qwen3-vl-plus 模型信息](https://help.aliyun.com/zh/model-studio/qwen3-vl-plus)、[阿里云百炼模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)。

## 附件消息流

1. 前端 `POST /api/v1/uploads` 上传图片，获得 `file_name`
2. 前端 `chat.send` WebSocket 消息携带 `attachments: [{file_name}]`
3. Gateway 校验附件存在于会话上传目录，注入附件说明块到 `input_text`
4. Coordinator 看到附件块，派发 `visual_researcher` Worker
5. Worker 调用 `image_analyze` 工具处理图片

附件块格式（注入到 `input_text`）:
```
---附件---
[图片] a1b2c3d4.jpg
路径: .data/uploads/<workspace>/<user>/<session>/a1b2c3d4.jpg
---附件结束---
```
