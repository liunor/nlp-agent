# Nova

<p align="center">
  <img src="webui/logo/nova.png" alt="Nova" width="180">
</p>

<p align="center">
  <strong>Your natural-language-processing learning and practice assistant</strong><br>
  Start from a single question, and make learning, practice and review effortless.
</p>

<p align="center">
  <a href="README.md">中文</a> · <a href="#quick-start">Quick Start</a> · <a href="CONTRIBUTING.md">Contributing</a>
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

## Table of contents

- [What is Nova](#what-is-nova)
- [Screenshots](#screenshots)
- [Core features](#core-features)
- [Architecture](#architecture)
- [Four core modules](#four-core-modules)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [Docker deployment](#docker-deployment)
- [Configuration](#configuration)
- [Testing & evaluation](#testing--evaluation)
- [Development workflow](#development-workflow)
- [Contributing](#contributing)
- [License](#license)

---

## What is Nova

Nova is an agentic platform for **natural-language-processing (NLP) learning and
teaching**. It is driven by a **Coordinator + Worker** dual-agent architecture that
orchestrates multi-turn conversations, tool calls and task decomposition on LangGraph,
letting learners close the loop of
「ask → understand → practice → review」 around any single concept.

| Role | For | Responsibility |
| --- | --- | --- |
| **Learner** | Students | Ask questions, learn concepts, complete exercises and review, consult history |
| **Teacher** | Teachers | Organize topics / knowledge points / exercise blueprints, review analytics and weak spots |
| **Developer** | Platform admins | Inspect runtime status, debug conversations, check APIs, tools and events |
| **Monitor** | Ops / devs | Observe service health, request status, traces and live runtime info |

Nova is currently used for intranet demos and teaching practice. It follows a design of
"teachers own the content, the server assigns exercises, snapshots guarantee
reproducibility", turning teaching modes (lecture / practice / review / Socratic
guidance) into a controllable Prompt runtime.

---

## Screenshots

| Learner | Developer console |
| --- | --- |
| <img src="docs/assets/screenshot-learner.png" alt="Learner chat page" width="640"> | <img src="docs/assets/screenshot-developer.png" alt="Developer console" width="640"> |

| Teacher analytics | Runtime monitor |
| --- | --- |
| <img src="docs/assets/screenshot-teacher.png" alt="Teacher analytics page" width="640"> | <img src="docs/assets/screenshot-monitor.png" alt="Runtime monitor page" width="640"> |

Streaming conversation demo:

![Streaming conversation demo](docs/assets/demo-chat.gif)

---

## Core features

- **Coordinator / Worker dual-agent architecture**: the Coordinator handles task
  planning and decomposition; Workers execute by Profile (web search, link reading,
  image understanding, NLP computation, …). Both share one immutable tool grant (`ToolSet`).
- **NLP teaching toolkit**: built-in deterministic NLP tools — TF-IDF, precision-recall
  curves, Precision@N, N-gram, BLEU — for demonstrating and explaining NLP concepts.
- **Teaching-mode loop**: Topic, Knowledge Point, exercise/review Blueprint, Snapshot and
  Rubric, supporting Socratic-guided sessions and an ask–answer–grade loop.
- **Scoped memory**: layered memory by workspace / user / session, injected directly as
  Markdown with **no vector search / RAG**; web facts go through search, memory keeps only
  user continuity and project decisions.
- **Reliable tool runtime**: unified retry, concurrency control, high-risk authorization and
  permission audit; source conflicts fail closed by default.
- **End-to-end image understanding**: OCR (local RapidOCR) + VLM (vision LLM) with table /
  chart / description / QA support, returning confidence and citations.
- **Safe sandbox execution**: model-triggered code runs in an isolated sandbox (gVisor runsc
  by default, optional Firecracker/Kata), with warm pools, snapshots and artifact hosting.
- **Quota & billing**: image, web search and link reading are metered per tool, with
  policies, reservations, rollups and management.
- **Observability & evaluation**: a dedicated monitor process + traces, plus a reproducible
  evaluation suite (tool routing / orchestration / guidance / blueprint multi-turn).

---

## Architecture

```text
                 ┌─────────────────────────────────────────────┐
                 │              Browser / CLI / future channels  │
                 └──────────────────────┬──────────────────────┘
                                        │  HTTP / WebSocket
                 ┌──────────────────────▼──────────────────────┐
                 │        FastAPI adapter (same-origin boundary)│
                 │   auth / CSRF / validation / event naming    │
                 └──────────────────────┬──────────────────────┘
                                        │
                 ┌──────────────────────▼──────────────────────┐
                 │     BackendGateway (sole lifecycle owner)     │
                 │   Turn store · event stream · LangGraph engine│
                 └───┬──────────────┬──────────────┬───────────┘
                     │              │              │
              MySQL (business)  Redis (queue)   SQLite (.data)
                     │              │              │
                     ▼              ▼              ▼
               Coordinator ──► Worker(s) ──► tools / MCP / sandbox / models

        ┌──────────┬──────────┬──────────┬──────────┐
        │  model   │   tool   │  memory  │  observe │
        └──────────┴──────────┴──────────┴──────────┘
```

- **Single-process ownership**: each process builds exactly one `BackendGateway`, which is
  the sole owner of the Coordinator, Worker, LangGraph, tools, memory, telemetry and local
  persistence lifecycle; FastAPI is only a same-origin network adapter and must not build a
  second LangGraph/Worker runtime.
- **Persistent streaming**: every event carries a monotonic `sequence`; clients recover by
  `replay_events()` after a disconnect, and the event log is the authoritative replay source.
- **Production topology**: Web and Worker can be split into separate processes, delivering
  turns over Redis Streams (at-least-once); see
  [docs/redis-worker-deployment.md](docs/redis-worker-deployment.md).

---

## Four core modules

| Module | Path | Description |
| --- | --- | --- |
| Learner | `/` | Chat, concept learning, practice/review, history and textbooks |
| Teacher | `/teacher` | Teaching goals, question classification, frequent questions, weak points, topic/difficulty/type stats |
| Developer | `/developer` | Admin console: Agent, Worker, tools, models, MCP, Skill, workspace and runtime snapshot |
| Monitor | `:8766` | Separate process/port: health, request status, traces, auth audit, sandbox monitor |

Learner, teacher and developer share one FastAPI/WebUI origin (same-origin cookie auth);
the monitor is a **separately built, separate process** read-only platform so monitoring
traffic never interferes with student conversations.

---

## Tech stack

### Backend

- **Language / framework**: Python 3.11+, FastAPI, Uvicorn
- **Agent orchestration**: LangGraph + LangChain Core
- **Models**: DeepSeek, Qwen (DashScope), Volcengine (Doubao), Qwen-VL (vision), Alibaba
  Cloud Embedding, behind a unified Provider abstraction
- **Data**: SQLAlchemy 2.0 (MySQL / aiomysql), Alembic migrations, Redis 5
- **Tools / protocols**: MCP (stdio / SSE / streamable_http), custom tool entry points
- **Auth**: Argon2 password hashing, DB-backed session auth, Tencent Cloud SMS and image
  captcha
- **Observability**: structlog and a custom `ObservabilityService` trace
- **OCR / vision**: RapidOCR (ONNX Runtime), OpenCV
- **Sandbox**: Docker / gVisor (runsc, default), optional Firecracker / Kata

### Frontend

- **Framework**: React 18 + TypeScript + Vite + Tailwind CSS
- **Build outputs**: student `webui/dist` + monitor `webui/monitor-dist`, served by FastAPI
- **Capabilities**: Markdown / GFM / KaTeX / Mermaid / code highlighting, streaming render,
  WebSocket reconnect and sequence recovery

### Infrastructure

- Docker Compose: Nginx (edge proxy), MySQL 8.4, Redis 7.4, Web, Worker, Sandbox Manager,
  Monitor

---

## Repository layout

```text
nlp-agent/
├── core/                  # model runtime, NLP tools, observability, Prompt templates, tool/memory/skill runtime
│   ├── model_runtime/     # Provider / registry / routing / Preset
│   ├── nlp/               # TF-IDF, BLEU, N-gram, PR curves, …
│   ├── prompt_runtime/    # versioned Prompt templates (Coordinator/Worker/learning/memory)
│   └── observability/     # local traces and MySQL store
├── gateway/               # Backend Gateway core (sole lifecycle owner)
├── server/                # FastAPI adapter and business domains (agent/memory/tools/sandbox/teacher/quota/rbac/user/monitor/worker)
├── sandbox-runtime/       # sandbox executable entry
├── schemas/               # shared Pydantic models
├── configs/               # agent_config.yaml and Settings
├── migrations/            # Alembic migrations (sole owner of MySQL schema)
├── scripts/               # seed / verify / reset / ops scripts
├── evaluation/            # evaluation system (routing/orchestration/guidance/blueprint)
├── webui/                 # React frontend (student + teacher + developer + monitor)
├── tests/                 # backend tests
├── nginx/                 # edge reverse proxy config
├── docs/                  # architecture docs + ADR + specs
├── compose.yaml           # Docker Compose orchestration
├── main.py                # CLI entry (serve/chat/monitor/worker/...)
└── pyproject.toml         # project and dependency definition
```

---

## Quick start

### Prerequisites

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/) (dependency and venv management)
- MySQL 8.x (business state store) and optional Redis

### 1. Install dependencies

```powershell
uv sync
```

### 2. Create the config file

```powershell
Copy-Item .env-example .env
notepad .env
```

Make sure at least the following are filled in:

```dotenv
DEEPSEEK_API_KEY=your model API key
NLP_AGENT_DATABASE_URL=mysql+aiomysql://user:password@host:3306/nlp_agent?charset=utf8mb4
```

> `.env` is local-only and must not be committed. The runtime schema is owned exclusively
> by Alembic; the app never calls `create_all`.

### 3. Initialize the database schema

```powershell
uv run python main.py bootstrap-db
```

`bootstrap-db` first runs Alembic migrations, then idempotently installs built-in pricing and
checks production route coverage — the single DB entry point for local and production alike.

### 4. Create the first developer account

After migrations, create the single developer account interactively; the password is never
written to the repo or env files:

```powershell
uv run python main.py bootstrap-developer
```

### 5. Start Nova Web

```powershell
uv run python main.py serve
```

Once you see the startup banner, open <http://127.0.0.1:8765> in a browser.

### 6. Visit the modules

- Learner: <http://127.0.0.1:8765/>
- Teacher: <http://127.0.0.1:8765/teacher>
- Developer: <http://127.0.0.1:8765/developer>
- Monitor: run `uv run python main.py monitor` first, then open <http://127.0.0.1:8766/>

### 7. Other entry points

```powershell
uv run python main.py chat               # CLI chat
uv run python main.py monitor            # monitor service
uv run python main.py worker             # standalone Worker (Redis mode)
uv run python main.py sandbox-manager    # sandbox manager (mounts Docker socket)
```

Stop a service with `Ctrl+C` in its window.

### 8. (Optional) Seed demo textbook content

To quickly preview the textbook page in development, seed a repeatable PyTorch demo (3
topics, 9 knowledge points, code blocks and an image) into a given empty workspace:

```powershell
uv run python -m scripts.seed_knowledge_book_demo --workspace-id <workspace-id>
```

The script only writes into workspaces with no existing teaching content; re-seeding its own
demo directory requires the explicit `--refresh-demo` flag.

---

## Docker deployment

One-stop deployment for intranet servers; see [compose.yaml](compose.yaml).

1. Copy and fill in the deployment config:

```powershell
Copy-Item .env-example .env
notepad .env
```

Replace at least `DEEPSEEK_API_KEY`, `NLP_AGENT_MYSQL_PASSWORD`,
`NLP_AGENT_MYSQL_ROOT_PASSWORD`, and set `SERVER_IP_OR_DOMAIN` to the real intranet IP or
domain. The `NLP_AGENT_DATABASE_URL` must point at the MySQL on that server (use `mysql:3306`
for the same Compose deployment, or the private address for a managed database) — never a dev
machine address.

2. Build and start the main services:

```powershell
docker compose up -d --build
```

Compose starts MySQL 8.4 first, then `nova-migrate` runs `main.py bootstrap-db`: Alembic
first, then idempotent pricing install and production route coverage check; Web, Worker and
Monitor only start after success. Business tables are still created solely by Alembic. MySQL
data lives in the `mysql-data` volume; Redis only holds queues and real-time transport state.

3. To start the monitor:

```powershell
docker compose --profile monitor up -d
```

- Main service: `http://SERVER_IP:8765`
- Monitor: bound to `http://127.0.0.1:8766` by default; set `NOVA_MONITOR_BIND_ADDRESS` to
  expose it through an intranet proxy or VPN.

Check status and logs:

```powershell
docker compose ps
docker compose logs -f nova-web
```

> Test and production must use different Compose project names, databases, Redis instances,
> secrets and networks; the monitor port is only for intranet/VPN.

---

## Configuration

Configuration is split into two layers: **sensitive env config** lives in `.env`, **structured
application config** lives in [`configs/agent_config.yaml`](configs/agent_config.yaml).

### Key `.env` entries (excerpt)

| Variable | Description |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek model API key |
| `QWEN_API_KEY` | Qwen / DashScope API key |
| `NLP_AGENT_DATABASE_URL` | MySQL business-store connection string |
| `NLP_AGENT_MYSQL_PASSWORD` | MySQL user password (Compose) |
| `NLP_AGENT_MYSQL_ROOT_PASSWORD` | MySQL root password (Compose) |
| `NLP_AGENT_GATEWAY_TRANSPORT` | `in_process` or `redis` (use redis to split Web/Worker) |
| `NLP_AGENT_REDIS_URL` | Redis connection string |
| `NLP_AGENT_WEB_SECRET` | Web session signing secret |
| `NLP_AGENT_SANDBOX_RUNTIME_MODE` | Sandbox mode (`disabled` / `docker` / …) |
| `NLP_AGENT_WEB_ALLOWED_HOSTS/ORIGINS` | Domain/origin allowlist for edge deployment |
| `NOVA_MONITOR_BIND_ADDRESS` | Monitor bind address (loopback by default) |

The full `.env` template is in [.env-example](.env-example).

### `agent_config.yaml` overview

- `defaults` / `model_presets` / `model_routes` / `model_profiles`: model routing and presets
- `gateway`: lifecycle, event retention, Redis delivery, leases, quota switch
- `web` / `monitor`: ports, cookies, origin allowlists, static dirs
- `memory`: injection limits and archival triggers
- `agent_runtime`: Coordinator / Worker iteration, timeout and token budgets
- `tools`: tool policies, custom modules, MCP servers, web reading, Vision/OCR
- `worker_profiles`: web search, link reading, image understanding, NLP computation, …

---

## Testing & evaluation

### Backend

```powershell
uv run ruff check configs core gateway server tests scripts migrations
uv run pytest
```

### Frontend

```powershell
cd webui
npm install
npm run typecheck
npm run lint
npm test
npm run build
npm run build:monitor
```

Frontend dev mode:

```powershell
npm run dev            # student, Vite :5173
npm run dev:monitor    # monitor, Vite :5174
```

### Evaluation system

The runner lives in `evaluation/`; suites are versioned under `.jbeval/suites/`, real runs
under `.jbeval/runs/`. Start Web and Monitor first, then run:

```powershell
# validate a suite
uv run python -m evaluation validate .jbeval/suites/nlp-tool-routing-v1/dataset.yaml

# run the tool-routing suite (--live must be explicit)
uv run python -m evaluation run nlp-tool-routing-v1 --live

# blueprint multi-turn evaluation (exercise / review)
uv run python -m evaluation.exercise_blueprint .jbeval/suites/exercise-blueprint-multiturn-v1/dataset.yaml --live --workspace evaluation-exercise --provision-fixture
uv run python -m evaluation.review_blueprint .jbeval/suites/review-blueprint-multiturn-v1/dataset.yaml --live --workspace evaluation-review --provision-fixture
```

Evaluation uses independent sessions, real traces and rule-based judgment to assess questions
like "was the correct tool called?"; see [docs/evaluation-system.md](docs/evaluation-system.md)
and [evaluation/README.md](evaluation/README.md).

---

## Development workflow

The project uses the Git Flow branching model:

```text
feature/*  ──PR──►  develop  ──PR──►  main
```

- `feature/*`: cut from `develop`, merged back via a Pull Request
- `develop`: integration branch; CI builds and pushes test images on success
- `main`: protected release branch, accepts PRs from `develop` only

CI (GitHub Actions) runs backend tests + static checks, frontend lint/test/build, and Docker
build validation. See [git-flow 开发流程.md](git-flow%20开发流程.md) and
[docs/ci-cd.md](docs/ci-cd.md) for the full conventions.

---

## Contributing

Contributions are welcome — doc fixes, tests, or new features. Read
[CONTRIBUTING.md](CONTRIBUTING.md) first for the branch model, commit conventions and PR
process.

---

## Document index

- Architecture: [backend-gateway.md](docs/backend-gateway.md) · [web-api.md](docs/web-api.md) · [session-context.md](docs/session-context.md)
- Model: [model-runtime.md](docs/model-runtime.md)
- Tools: [tool-runtime.md](docs/tool-runtime.md) · [tool-reliability.md](docs/tool-reliability.md)
- Memory: [memory-runtime.md](docs/memory-runtime.md)
- Observability: [observability.md](docs/observability.md)
- Deployment: [redis-worker-deployment.md](docs/redis-worker-deployment.md) · [ci-cd.md](docs/ci-cd.md) · [migrations.md](docs/migrations.md)
- Teaching: [teacher-mode.md](docs/teacher-mode.md) · [specs/learning-teaching-workflow.md](docs/specs/learning-teaching-workflow.md)
- Image: [image-understanding.md](docs/image-understanding.md)
- Sandbox: [sandbox-phase5.md](docs/sandbox-phase5.md)
- Evaluation: [evaluation-system.md](docs/evaluation-system.md)
- Decisions: [docs/adr/](docs/adr/)

---

## License

This project is licensed under the [MIT License](LICENSE).

---

Nova is currently used for intranet demos, teaching and NLP learning practice. Suggestions
are welcome — let us turn it into an even better learning companion.