# Contributing Guide

**English** | [中文](./CONTRIBUTING.zh.md)

Thanks for your interest in Nova! This guide helps you land your first contribution smoothly, whether it's a documentation typo fix or a new feature.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [What You Can Do](#what-you-can-do)
- [Development Environment](#development-environment)
- [Branching Model](#branching-model)
- [Commit Conventions](#commit-conventions)
- [Submitting a Pull Request](#submitting-a-pull-request)
- [Code Style and Tests](#code-style-and-tests)

## Code of Conduct

Please be respectful, focus on the issue rather than the person, and keep communication honest and constructive. Submissions containing insulting, discriminatory, or offensive content will be rejected outright.

## What You Can Do

- **Fix docs / typos**: open a PR directly and label it with the `docs:` type.
- **Fix a bug**: confirm it reproduces reliably first, and spell out the trigger conditions and user impact in the PR description.
- **Add a feature**: describe the motivation and approach in an Issue first, reach agreement, then start — this avoids rework.
- **Evaluations / tests**: contributions adding cases for tool routing, orchestration, teaching guidance, etc. are welcome — see [`evaluation/`](evaluation/).

## Development Environment

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), MySQL 8.x (Redis optional).

```powershell
uv sync
Copy-Item .env-example .env   # fill in model keys and database connection
uv run python main.py bootstrap-db        # initialize database schema
uv run python main.py bootstrap-developer # create the first developer account
uv run python main.py serve               # start the web app
```

The frontend (student + monitor) lives in `webui/`:

```powershell
cd webui
npm install
npm run dev            # student Vite dev server :5173
npm run dev:monitor    # monitor Vite dev server :5174
```

## Branching Model

The project follows Git Flow:

```text
feature/*  ──PR──►  develop  ──PR──►  main
```

- Branch `feature/<summary>` or `fix/<summary>` off `develop`.
- Once complete and verified locally, open a PR against `develop`.
- `main` is a protected branch and only accepts merges from `develop`.

## Commit Conventions

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/), consistent with the repository history:

```text
<type>(<scope>): <one-line summary>

optional body and footers
```

Common types:

| type | purpose |
| --- | --- |
| `feat` | a new feature |
| `fix` | a bug fix |
| `docs` | documentation changes |
| `refactor` | refactoring (no behavior change) |
| `test` | adding or correcting tests |
| `chore` | build, dependencies, misc |

> Database schema changes must be managed exclusively by Alembic migrations (see [`docs/migrations.md`](docs/migrations.md)).
> The application does not run `create_all`, so please do not include hand-written table creation in your PR.

## Submitting a Pull Request

1. Run the local checks first (see [Code Style and Tests](#code-style-and-tests) below) and confirm nothing fails.
2. Fill in the PR description following the 7-section structure in [`.github/pull_request_template.md`](.github/pull_request_template.md):
   one-line summary, background & motivation, feature description, implementation approach, files changed, testing & verification, impact & risk.
3. Tick the Reviewer Checklist and honestly fill in "uncovered items" — write "none" if there are none.
4. Wait for CI (backend tests + static checks, frontend lint/test/build, Docker build verification) to pass.

## Code Style and Tests

Backend:

```powershell
uv run ruff check configs core gateway server tests scripts migrations
uv run pytest
```

Frontend:

```powershell
cd webui
npm run typecheck
npm run lint
npm test
npm run build
npm run build:monitor
```

You're not required to pass every test before submitting, but please state honestly in the PR which parts are uncovered and why.
