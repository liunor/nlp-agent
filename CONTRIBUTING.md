# 贡献指南

感谢你对 Nova 的关注！这份指南会帮你顺畅地提交第一份贡献，无论是一个文档错字的修复，还是一个新功能。

## 目录

- [行为准则](#行为准则)
- [我能做什么](#我能做什么)
- [开发环境](#开发环境)
- [分支模型](#分支模型)
- [提交规范](#提交规范)
- [提交 Pull Request](#提交-pull-request)
- [代码风格与测试](#代码风格与测试)

## 行为准则

请互相尊重、对事不对人，保持坦诚、建设性的沟通。提交侮辱、歧视或攻击性内容会被直接拒绝。

## 我能做什么

- **修复文档 / 错字**：直接开 PR 即可，标注 `docs:` 类型。
- **修复 bug**：先确认能稳定复现，在 PR 描述里写清触发条件与用户影响。
- **新增功能 / 特性**：先在 Issue 里说明动机与方案，达成一致后再动手，避免返工。
- **评测 / 测试补充**：欢迎为工具路由、编排、教学引导等补充用例，见 [`evaluation/`](evaluation/)。

## 开发环境

前置条件：Python 3.11+、[uv](https://docs.astral.sh/uv/)、MySQL 8.x（可选 Redis）。

```powershell
uv sync
Copy-Item .env-example .env   # 填入模型密钥与数据库连接
uv run python main.py bootstrap-db        # 初始化数据库结构
uv run python main.py bootstrap-developer # 创建首个开发者账号
uv run python main.py serve               # 启动 Web
```

前端（学生端 + 监控端）在 `webui/`：

```powershell
cd webui
npm install
npm run dev            # 学生端 Vite :5173
npm run dev:monitor    # 监控端 Vite :5174
```

## 分支模型

项目采用 Git Flow，详见 [`git-flow 开发流程.md`](git-flow%20开发流程.md)：

```text
feature/*  ──PR──►  develop  ──PR──►  main
```

- 从 `develop` 拉出 `feature/<简述>` 或 `fix/<简述>` 分支。
- 完成并通过本地验证后，向 `develop` 提 PR。
- `main` 是受保护分支，只接受来自 `develop` 的合并。

## 提交规范

提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/)，与仓库历史保持一致：

```text
<type>(<scope>): <一句话描述>

可选的正文与脚注
```

常见 type：

| type | 用途 |
| --- | --- |
| `feat` | 新增功能 |
| `fix` | 修复 bug |
| `docs` | 文档变更 |
| `refactor` | 重构（不改行为） |
| `test` | 测试补充或修正 |
| `chore` | 构建、依赖、杂项 |

> 数据库 schema 变更必须由 Alembic 迁移独占管理（见 [`docs/migrations.md`](docs/migrations.md)），
> 应用不执行 `create_all`，PR 里请勿包含手写建表。

## 提交 Pull Request

1. 先跑本地验证（见下方「代码风格与测试」），确认无失败。
2. 按 [`.github/pull_request_template.md`](.github/pull_request_template.md) 的 7 段结构填写 PR 描述：
   一句话概述、背景与动机、功能说明、实现方式、改动文件、测试与验证、影响与风险。
3. 勾选 Reviewer Checklist，如实填写「未覆盖项」，没有时写「无」。
4. 等待 CI（后端测试 + 静态检查、前端 lint/测试/构建、Docker 构建校验）通过。

## 代码风格与测试

后端：

```powershell
uv run ruff check configs core gateway server tests scripts migrations
uv run pytest
```

前端：

```powershell
cd webui
npm run typecheck
npm run lint
npm test
npm run build
npm run build:monitor
```

不强制要求所有测试都跑过才提交，但请在 PR 里如实说明哪些未覆盖、以及原因。