# Nova

<p align="center">
  <img src="webui/logo/nova.png" alt="Nova" width="180">
</p>

<p align="center">
  <strong>你的自然语言处理学习与实践助手</strong><br>
  从一个问题开始，让学习、练习和复盘变得更简单。
</p>

<p align="center">
  <a href="README.en.md">English</a> · <a href="#快速开始">快速开始</a> · <a href="CONTRIBUTING.md">贡献</a>
</p>

<p align="center">
  <img src="https://img.shields.io/github/license/liunor/nlp-agent" alt="license">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="python">
  <img src="https://img.shields.io/github/actions/workflow/status/liunor/nlp-agent/ci.yml?branch=develop" alt="ci">
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" alt="fastapi">
  <img src="https://img.shields.io/badge/LangGraph-orange" alt="langgraph">
  <img src="https://img.shields.io/badge/React-20232A?logo=react&logoColor=61DAFB" alt="react">
</p>

---

## 目录

- [Nova 是什么](#nova-是什么)
- [功能预览](#功能预览)
- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [四个核心模块](#四个核心模块)
- [技术栈](#技术栈)
- [仓库结构](#仓库结构)
- [快速开始](#快速开始)
- [Docker 部署](#docker-部署)
- [配置说明](#配置说明)
- [测试与评测](#测试与评测)
- [开发流程](#开发流程)
- [贡献](#贡献)
- [许可证](#许可证)

---

## Nova 是什么

Nova 是一个面向**自然语言处理（NLP）学习与教学场景**的智能体平台。它由
**Coordinator（协调者）+ Worker（执行者）** 双智能体协作驱动，在 LangGraph
上编排多轮对话、工具调用与任务分解，让学习者可以围绕一个知识点完成
「提问 → 理解 → 练习 → 复盘」的完整闭环。

| 角色 | 面向谁 | 负责什么 |
| --- | --- | --- |
| **学习者** | 学生 | 提问、学习知识点、完成练习与复习、查看学习记录 |
| **教师** | 老师 | 组织主题/知识点/练习蓝图、查看学习分析与薄弱点 |
| **开发者** | 平台管理员 | 查看运行状态、调试对话、检查接口、工具与事件 |
| **运行监控** | 运维/开发 | 观察服务健康度、请求状态、链路 Trace 与实时运行信息 |

Nova 当前主要用于内网演示与教学实践，采用「教师拥有教学内容、服务端分配练习、
快照保证可复现」的设计，把教学模式（讲解/练习/复习/苏格拉底引导）沉淀为可控的
Prompt 运行时。

---

## 功能预览

| 学习者 | 开发者控制台 |
| --- | --- |
| <img src="docs/assets/screenshot-learner.png" alt="学习者对话页面" width="640"> | <img src="docs/assets/screenshot-developer.png" alt="开发者控制台" width="640"> |

| 教师分析 | 运行监控 |
| --- | --- |
| <img src="docs/assets/screenshot-teacher.png" alt="教师分析页面" width="640"> | <img src="docs/assets/screenshot-monitor.png" alt="运行监控页面" width="640"> |

对话与流式输出的动态演示：

![对话流式演示](docs/assets/demo-chat.gif)

---

## 核心特性

- **Coordinator / Worker 双智能体架构**：Coordinator 负责任务编排与拆解，Worker
  按 Profile 执行（联网检索、链接阅读、图片理解、NLP 计算等），二者共享同一套
  不可变工具授权（`ToolSet`）。
- **NLP 教学工具集**：内置 TF-IDF、精确率-召回率曲线、Precision@N、N-gram、
  BLEU 等确定性 NLP 计算工具，用于演示和讲解 NLP 概念。
- **教学模式闭环**：主题（Topic）、知识点（Knowledge Point）、练习/复习蓝图
  （Blueprint）与快照（Snapshot）、评分量表（Rubric），支撑苏格拉底引导会话与
  出题—作答—评分闭环。
- **作用域记忆**：按 workspace / user / session 分层记忆，直接注入 Markdown，
  **不使用向量检索 / RAG**；联网事实走搜索，记忆只保留用户连续性与项目决策。
- **可靠的工具运行时**：统一重试、并发控制、高风险授权、权限审计，来源冲突
  默认失败关闭。
- **端到端图片理解**：OCR（本地 RapidOCR）+ VLM（视觉大模型），支持表格/图表/
  描述/问答，返回置信度与引用。
- **安全沙箱执行**：模型触发的代码在隔离沙箱中运行（gVisor runsc 默认，
  可选 Firecracker/Kata），带暖池、快照与制品托管。
- **额度与计费**：识图、联网搜索、链接读取等按工具计费，支持策略、预留、
  汇总与管理。
- **可观测与评测**：独立监控进程 + 链路 Trace，以及一套可复现的评测系统
  （工具路由 / 编排 / 引导 / 蓝图多轮）。

---

## 系统架构

```text
                 ┌─────────────────────────────────────────────┐
                 │              Browser / CLI / 未来渠道          │
                 └──────────────────────┬──────────────────────┘
                                        │  HTTP / WebSocket
                 ┌──────────────────────▼──────────────────────┐
                 │        FastAPI 适配层（同源网络边界）           │
                 │   鉴权 / CSRF / 请求校验 / 事件命名             │
                 └──────────────────────┬──────────────────────┘
                                        │
                 ┌──────────────────────▼──────────────────────┐
                 │            BackendGateway（唯一生命周期所有者） │
                 │   Turn 存储 · 事件流 · LangGraph 引擎           │
                 └───┬──────────────┬──────────────┬───────────┘
                     │              │              │
              MySQL(业务状态)   Redis(队列/事件)   SQLite(.data 本地态)
                     │              │              │
                     ▼              ▼              ▼
               Coordinator ──► Worker(s) ──► 工具 / MCP / 沙箱 / 模型

        ┌──────────┬──────────┬──────────┬──────────┐
        │  模型运行时 │  工具运行时 │  记忆运行时 │  可观测性  │
        └──────────┴──────────┴──────────┴──────────┘
```

- **单进程所有权**：每个进程只构造一个 `BackendGateway`，它独占 Coordinator、
  Worker、LangGraph、工具、记忆、遥测与本地持久化的生命周期；FastAPI 只是同源
  网络适配器，不得二次构造 LangGraph/Worker 运行时。
- **持久化流式**：每个事件带单调 `sequence`，客户端断线后按序列号 `replay_events()`
  恢复，事件日志是权威回溯来源。
- **生产拓扑**：Web 与 Worker 可拆分为独立进程，通过 Redis Streams 投递 Turn
  （at-least-once），详见 [docs/redis-worker-deployment.md](docs/redis-worker-deployment.md)。

---

## 四个核心模块

| 模块 | 地址 | 说明 |
| --- | --- | --- |
| 学习者 | `/` | 对话提问、知识点学习、练习/复习、学习记录与知识教材 |
| 教师 | `/teacher` | 教学目标、学生提问分类、高频问题、薄弱知识点、主题/难度/题型统计 |
| 开发者 | `/developer` | 管理员控制台：Agent、Worker、工具、模型、MCP、Skill、工作区与运行时快照 |
| 运行监控 | `:8766` | 独立进程与端口，健康度、请求状态、链路 Trace、授权审计、沙箱监控 |

学习者与教师、开发者共享同一 FastAPI/WebUI 源（同源 Cookie 鉴权）；运行监控
是一个**独立构建、独立进程**的只读平台，避免监控流量干扰学生对话。

---

## 技术栈

### 后端

- **语言 / 框架**：Python 3.11+，FastAPI，Uvicorn
- **Agent 编排**：LangGraph + LangChain Core
- **模型**：DeepSeek、Qwen（DashScope）、Volcengine（豆包）、Qwen-VL（视觉）、
  阿里云 Embedding，统一的 Provider 抽象
- **数据**：SQLAlchemy 2.0（MySQL / aiomysql），Alembic 迁移，Redis 5
- **工具 / 协议**：MCP（stdio / SSE / streamable_http），自定义工具入口点
- **认证**：Argon2 密码散列、数据库会话鉴权、腾讯云短信验证码、图片验证码
- **观测**：structlog，自定义 `ObservabilityService` 链路
- **OCR / 视觉**：RapidOCR（ONNX Runtime）、OpenCV
- **沙箱**：Docker / gVisor（runsc，默认），可选 Firecracker / Kata

### 前端

- **框架**：React 18 + TypeScript + Vite + Tailwind CSS
- **构建产物**：学生端 `webui/dist` + 监控端 `webui/monitor-dist`，由 FastAPI 托管
- **能力**：Markdown / GFM / KaTeX 数学 / Mermaid 图 / 代码高亮，流式渲染，
  WebSocket 重连与序列恢复

### 基础设施

- Docker Compose：Nginx（边缘反代）、MySQL 8.4、Redis 7.4、Web、Worker、
  Sandbox Manager、Monitor

---

## 仓库结构

```text
nlp-agent/
├── core/                  # 模型运行时、NLP 工具、观测、Prompt 模板、工具/记忆/技能运行时
│   ├── model_runtime/     # Provider / 注册表 / 路由 / Preset
│   ├── nlp/               # TF-IDF、BLEU、N-gram、PR 曲线等 NLP 计算
│   ├── prompt_runtime/    # 版本化 Prompt 模板（Coordinator/Worker/学习/记忆）
│   └── observability/     # 本地链路与 MySQL 仓
├── gateway/               # Backend Gateway 核心（生命周期唯一所有者）
├── server/                # FastAPI 适配与各业务域
│   ├── agent/             # LangGraph 节点、压缩、会话存储
│   ├── memory/            # 作用域记忆（Curator / 注入 / 提取）
│   ├── tools/             # 工具 API、Vision/OCR/VLM、Web 读取
│   ├── sandbox/           # 沙箱运行时、暖池、制品、快照
│   ├── teacher/           # 教学分析、内容、蓝图
│   ├── quota/             # 额度、计费、策略
│   ├── rbac/              # 角色权限与审计
│   ├── user/ workspace/   # 用户、工作区、短信、验证码
│   ├── monitor/           # 监控服务与页面
│   └── worker/            # 独立 Worker 运行时
├── sandbox-runtime/       # 沙箱可执行入口
├── schemas/               # 共享 Pydantic 数据模型
├── configs/               # agent_config.yaml 与 Settings
├── migrations/            # Alembic 迁移（MySQL schema 唯一所有者）
├── scripts/               # 种子、校验、重置、运维脚本
├── evaluation/            # 评测系统（工具路由/编排/引导/蓝图）
├── webui/                 # React 前端（学生 + 教师 + 开发者 + 监控）
├── tests/                 # 后端测试
├── nginx/                 # 边缘反向代理配置
├── docs/                  # 架构文档 + ADR + Specs
├── compose.yaml           # Docker Compose 编排
├── main.py                # CLI 入口（serve/chat/monitor/worker/...）
└── pyproject.toml         # 项目与依赖定义
```

---

## 快速开始

### 前置条件

- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/)（依赖与虚拟环境管理）
- MySQL 8.x（业务状态库）与可选的 Redis

### 1. 安装依赖

```powershell
uv sync
```

### 2. 创建配置文件

```powershell
Copy-Item .env-example .env
notepad .env
```

至少确认以下配置已经填写：

```dotenv
DEEPSEEK_API_KEY=你的模型服务密钥
NLP_AGENT_DATABASE_URL=mysql+aiomysql://用户:密码@主机:3306/nlp_agent?charset=utf8mb4
```

> `.env` 只用于本机配置，不要提交到 Git。运行时 schema 由 Alembic 独占管理，
> 应用不执行 `create_all`。

### 3. 初始化数据库结构

```powershell
uv run python main.py bootstrap-db
```

`bootstrap-db` 先执行 Alembic 迁移，再幂等安装内置定价并检查生产路由覆盖，是本地与
线上统一的数据库入口。

### 4. 创建首个开发者账号

数据库迁移完成后，用交互式命令创建唯一的开发者账号；密码不会写入仓库或环境文件：

```powershell
uv run python main.py bootstrap-developer
```

### 5. 启动 Nova Web

```powershell
uv run python main.py serve
```

看到启动提示后，在浏览器打开 <http://127.0.0.1:8765>。

### 6. 访问不同模块

- 学习者：<http://127.0.0.1:8765/>
- 教师：<http://127.0.0.1:8765/teacher>
- 开发者：<http://127.0.0.1:8765/developer>
- 运行监控：先执行 `uv run python main.py monitor`，再打开 <http://127.0.0.1:8766/>

### 7. 其他启动方式

```powershell
uv run python main.py chat               # 命令行对话
uv run python main.py monitor            # 运行监控服务
uv run python main.py worker             # 独立 Worker（Redis 模式）
uv run python main.py sandbox-manager    # 沙箱管理器（挂载 Docker 套接字）
```

停止服务时，在对应窗口按 `Ctrl+C` 即可。

### 8. （可选）写入教材演示样例

开发环境需要快速查看教材页面时，可以为指定的空 workspace 写入一套可重复执行的
PyTorch 教材样例（3 个主题、9 个知识点、代码块和一张图片）：

```powershell
uv run python -m scripts.seed_knowledge_book_demo --workspace-id <workspace-id>
```

脚本只允许写入没有现有教学内容的 workspace；刷新本脚本已有的演示目录须显式使用
`--refresh-demo`。

---

## Docker 部署

用于内网服务器的一站式部署，详见 [compose.yaml](compose.yaml)。

1. 复制并填写部署配置：

```powershell
Copy-Item .env-example .env
notepad .env
```

至少替换 `DEEPSEEK_API_KEY`、`NLP_AGENT_MYSQL_PASSWORD`、
`NLP_AGENT_MYSQL_ROOT_PASSWORD`，并将 `SERVER_IP_OR_DOMAIN` 替换为服务器实际
内网 IP 或域名。部署服务器的 `NLP_AGENT_DATABASE_URL` 必须指向该服务器的 MySQL
（同一 Compose 部署用 `mysql:3306`；托管数据库则改为其私网地址），不要填开发机地址。

2. 构建并启动主服务：

```powershell
docker compose up -d --build
```

Compose 会先启动 MySQL 8.4，再由 `nova-migrate` 运行 `main.py bootstrap-db`：先执行
Alembic，再幂等安装内置定价并检查生产路由覆盖；全部成功后 Web、Worker 与 Monitor 才
会启动。业务表仍只由 Alembic 创建，应用进程不会运行时建表。MySQL 数据保存在卷
`mysql-data`，Redis 只保存队列与实时传输状态。

3. 如需启动运行监控：

```powershell
docker compose --profile monitor up -d
```

- 主服务访问地址：`http://服务器IP:8765`
- 监控地址：默认仅绑定本机 `http://127.0.0.1:8766`；如需通过内网反向代理或 VPN
  暴露，显式设置 `NOVA_MONITOR_BIND_ADDRESS`

查看运行状态与日志：

```powershell
docker compose ps
docker compose logs -f nova-web
```

> 测试与生产必须使用不同的 Compose 项目名、数据库、Redis、密钥与网络；监控端口
> 只开放给内网或 VPN。

---

## 配置说明

配置分两层：**环境的敏感配置**放在 `.env`，**应用的结构化配置**放在
[`configs/agent_config.yaml`](configs/agent_config.yaml)。

### `.env` 关键项（节选）

| 变量 | 说明 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek 模型服务密钥 |
| `QWEN_API_KEY` | Qwen / DashScope 密钥 |
| `NLP_AGENT_DATABASE_URL` | MySQL 业务状态库连接串 |
| `NLP_AGENT_MYSQL_PASSWORD` | Compose 中 MySQL 用户密码 |
| `NLP_AGENT_MYSQL_ROOT_PASSWORD` | Compose 中 MySQL root 密码 |
| `NLP_AGENT_GATEWAY_TRANSPORT` | `in_process` 或 `redis`（Web/Worker 分离时用 redis） |
| `NLP_AGENT_REDIS_URL` | Redis 连接串 |
| `NLP_AGENT_WEB_SECRET` | Web 会话签名密钥 |
| `NLP_AGENT_SANDBOX_RUNTIME_MODE` | 沙箱模式（`disabled` / `docker` / …） |
| `NLP_AGENT_WEB_ALLOWED_HOSTS/ORIGINS` | 边缘部署的域名/来源白名单 |
| `NOVA_MONITOR_BIND_ADDRESS` | 监控服务绑定地址（默认仅本机，暴露到内网/VPN 时设置） |

完整的 `.env` 模板见 [.env-example](.env-example)。

### `agent_config.yaml` 概览

- `defaults` / `model_presets` / `model_routes` / `model_profiles`：模型路由与 Preset
- `gateway`：生命周期、事件保留、Redis 投递、租约、额度开关
- `web` / `monitor`：端口、Cookie、同源白名单、静态目录
- `memory`：记忆注入上限、归档触发
- `agent_runtime`：Coordinator / Worker 的迭代、超时、Token 预算
- `tools`：工具策略、自定义模块、MCP 服务、Web 读取、Vision/OCR
- `worker_profiles`：联网检索、链接阅读、图片理解、NLP 计算等 Worker Profile

---

## 测试与评测

### 后端

```powershell
uv run ruff check configs core gateway server tests scripts migrations
uv run pytest
```

### 前端

```powershell
cd webui
npm install
npm run typecheck
npm run lint
npm test
npm run build
npm run build:monitor
```

前端开发模式：

```powershell
npm run dev            # 学生端，Vite :5173
npm run dev:monitor    # 监控端，Vite :5174
```

### 评测系统

评测运行器位于 `evaluation/`，测试集版本化保存在 `.jbeval/suites/`，真实运行产物
在 `.jbeval/runs/`。先启动 Web 与 Monitor，再执行：

```powershell
# 校验测试集
uv run python -m evaluation validate .jbeval/suites/nlp-tool-routing-v1/dataset.yaml

# 运行工具路由套件（--live 必须显式指定）
uv run python -m evaluation run nlp-tool-routing-v1 --live

# 蓝图多轮评测（出题 / 复习）
uv run python -m evaluation.exercise_blueprint .jbeval/suites/exercise-blueprint-multiturn-v1/dataset.yaml --live --workspace evaluation-exercise --provision-fixture
uv run python -m evaluation.review_blueprint .jbeval/suites/review-blueprint-multiturn-v1/dataset.yaml --live --workspace evaluation-review --provision-fixture
```

评测通过独立 session、真实 Trace 与规则判定，评估「面对问题是否调用了正确的工具」
等能力，详见 [docs/evaluation-system.md](docs/evaluation-system.md) 与
[evaluation/README.md](evaluation/README.md)。

---

## 开发流程

项目采用 Git Flow 分支模型：

```text
feature/*  ──PR──►  develop  ──PR──►  main
```

- `feature/*`：从 `develop` 创建，完成后以 Pull Request 合并回 `develop`
- `develop`：集成分支，CI 成功后构建并推送测试镜像
- `main`：受保护的可发布分支，只接受来自 `develop` 的 PR

CI（GitHub Actions）包含后端测试与静态检查、前端 lint/测试/构建、Docker 构建校验
三个任务。更完整的约定见 [git-flow 开发流程.md](git-flow%20开发流程.md) 与
[docs/ci-cd.md](docs/ci-cd.md)。

---

## 贡献

欢迎参与！无论是修复文档错字、补充测试，还是新增功能，请先阅读
[CONTRIBUTING.md](CONTRIBUTING.md)，了解分支模型、提交规范与 PR 流程。

---

## 文档索引

- 架构：[backend-gateway.md](docs/backend-gateway.md) · [web-api.md](docs/web-api.md) · [session-context.md](docs/session-context.md)
- 模型：[model-runtime.md](docs/model-runtime.md)
- 工具：[tool-runtime.md](docs/tool-runtime.md) · [tool-reliability.md](docs/tool-reliability.md)
- 记忆：[memory-runtime.md](docs/memory-runtime.md)
- 观测：[observability.md](docs/observability.md)
- 部署：[redis-worker-deployment.md](docs/redis-worker-deployment.md) · [ci-cd.md](docs/ci-cd.md) · [migrations.md](docs/migrations.md)
- 教学：[teacher-mode.md](docs/teacher-mode.md) · [specs/learning-teaching-workflow.md](docs/specs/learning-teaching-workflow.md)
- 图片：[image-understanding.md](docs/image-understanding.md)
- 沙箱：[sandbox-phase5.md](docs/sandbox-phase5.md)
- 评测：[evaluation-system.md](docs/evaluation-system.md)
- 决策：[docs/adr/](docs/adr/)（蓝图快照、服务端分配蓝图、主题生命周期）

---

## 许可证

本项目采用 [MIT 许可证](LICENSE)。

---

Nova 当前主要用于内网演示、教学和自然语言处理学习实践。欢迎在使用过程中提出建议，
一起把它变成更好用的学习伙伴。