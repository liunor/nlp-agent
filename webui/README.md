# NLP Agent WebUI

Independent React/Vite frontend for the Pro_NLP FastAPI gateway.

## Student experience

- Nanobot-inspired responsive session sidebar and streaming thread shell.
- Same-origin cookie authentication and CSRF-protected HTTP mutations.
- Multiplexed `/ws/v1` commands, reconnect, sequence resume, and gap recovery.
- Educational activity labels for reasoning, tools, and worker collaboration.
- Markdown, GFM, math, lazy syntax highlighting, learning context, concepts,
  practice prompts, progress, summaries, favorites, archives, and report export.

Backend Sessions, Turns, transcripts, and runtime events remain authoritative.
UI-only learning metadata (titles, topics, favorites, archives, summaries, and
concept chips) is isolated in `localStorage` under
`nlp-agent.learning-preferences.v1` until dedicated teacher/course APIs exist.

## Developer experience

- `/developer/*` is the administrator-only control plane on the student WebUI
  port. It exposes sanitized Agent, Worker, tool, model, MCP, Skill, workspace,
  and runtime snapshots.
- The observability/debug platform is a separate build and process. It uses
  port `8766` in production and Vite port `5174` during frontend development,
  keeping monitoring traffic away from student chat.

## Teacher experience

`/teacher/*` shares the student WebUI port and provides a teacher-only education
view: teaching goals, classified student questions, frequent questions, weak
knowledge points, and topic/difficulty/type statistics. Course, Prompt, and
durable report pages already have stable backend interface placeholders.

## Development

```powershell
npm install
npm run dev
npm run dev:monitor
```

FastAPI should run at `http://127.0.0.1:8765`; Vite runs at
`http://127.0.0.1:5173` and proxies `/api`, `/health`, and `/ws`.

For a production-style local run, build first and start FastAPI. The backend
serves `webui/dist` at `/` according to `configs/agent_config.yaml`.

To review the monitor with synthetic data, run this from the project root
after the MySQL migrations have been applied:

```powershell
uv run python scripts/seed_monitor_demo.py --count 96
```

This creates only disabled demo principals and rows marked
`monitor-demo-v1`; it does not create code, prompts, or model output. It also
seeds the sandbox capacity trend when `NLP_AGENT_REDIS_URL` is configured.
Remove only the demo rows with:

```powershell
uv run python scripts/seed_monitor_demo.py --clear
```

## Verification

```powershell
npm run typecheck
npm run lint
npm run test
npm run build
npm run build:monitor
```
