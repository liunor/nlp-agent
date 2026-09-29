<div align="center">

<img src="webui/logo/nova.png" alt="Nova 标志" width="150" />

<h1>Nova</h1>

**面向 NLP 课堂的教学与学习助手。**

<p>
  <img src="https://img.shields.io/github/stars/liunor/nlp-agent?logo=github" alt="GitHub stars">
  <img src="https://github.com/liunor/nlp-agent/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/FastAPI-005571?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/LangGraph-1C3C3C" alt="LangGraph">
  <img src="https://img.shields.io/badge/MySQL-4479A1?logo=mysql&logoColor=white" alt="MySQL">
  <img src="https://img.shields.io/badge/Redis-DC382D?logo=redis&logoColor=white" alt="Redis">
  <img src="https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/docs-docs%2F-blue" alt="Docs">
  <img src="https://img.shields.io/badge/License-MIT-0969da" alt="MIT License">
</p>

<p>
  <a href="#功能">功能</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="README.md">English</a> ·
  <a href="CONTRIBUTING.zh.md">贡献指南</a> ·
  <a href="LICENSE">许可证</a>
</p>

</div>

<img src="docs/assets/demo-flow.gif" alt="Nova 功能联动演示" width="100%" />

Nova 是面向 NLP 教学与学习的助手。一次部署即服务四种角色——学习者、教师、开发者、运行监控，一个实例即可覆盖整个班级。

教师先编写主题、Markdown 知识点与练习蓝图；学生用自然语言提问、获得一步一步的讲解，完成自动生成的练习，并对照教师设定的评分细则复盘每份作答。开发者配置模型与基础设施，运行监控纵览整条流水线的每一步。

## 从这里开始

| 你想... | 去看 |
| --- | --- |
| 几分钟内本地跑起来 | [快速开始](#快速开始) |
| 看看长什么样 | [四个端](#四个端) |
| 了解功能 | [功能](#功能) |
| 配置模型与数据库 | [`.env-example.zh`](./.env-example.zh) |
| 参与贡献或扩展 | [CONTRIBUTING.zh.md](./CONTRIBUTING.zh.md) |

## 四个端

Nova 在一个部署上运行四个基于角色的视图，整个课堂只需一套实例：

### 学习者

学习者视图用于完成课程学习。

- 用自然语言提问，跟着一步一步的讲解推进。
- 完成由教师蓝图自动生成的练习。
- 对照教师设定的加权评分细则复盘每份作答。

<p align="center">
  <img src="docs/assets/screenshot-learner.png" alt="学习者界面" width="900">
</p>

### 教师

教师视图用于编写课程内容。

- 编写主题与 Markdown 知识点，让每次提问恰好注入所需范围。
- 设计练习蓝图，生成题目并按加权评分细则打分。
- 用同一份评分细则，保证批改口径一致。

<p align="center">
  <img src="docs/assets/screenshot-teacher.png" alt="教师界面" width="900">
</p>

### 开发者

开发者视图管理模型与运行时配置。

- 集中增删模型服务商及其 API 密钥。
- 调整 web、worker 与 sandbox 的运行时配置。
- 管理运行生成代码的沙箱，与宿主机隔离。

<p align="center">
  <img src="docs/assets/screenshot-developer.png" alt="开发者界面" width="900">
</p>

### 运行监控

运行监控视图是可观测面板。

- 实时查看 web、worker 与 sandbox 的轮次、追踪与指标。
- 检查整条流水线的任务活动。
- 从客户端提问一路追踪到最终答案。

<p align="center">
  <img src="docs/assets/screenshot-monitor.png" alt="运行监控界面" width="900">
</p>

## 功能

- **四个基于角色的视图**：学习者、教师、开发者、运行监控，由访问控制隔离，各角色只见各自该见的内容。
- **引导式学习**：问答式会话，一步一步拆解问题，而不是一次性抛出答案。
- **自动批改练习**：教师定义蓝图生成题目，并按加权评分细则给每份作答打分。
- **知识点目录**：教师编写主题与 Markdown 知识点，每次提问恰好注入所需范围。
- **可观测性**：内建监控纵览 web、worker 与 sandbox 的轮次、追踪与指标。
- **模块化运行时**：基于 LangGraph 的协调者 / 工作者引擎，含工具、记忆与隔离代码执行。
- **网页与命令行**：浏览器或终端都能对话，师生不局限于单一界面。

## 快速开始

### 环境要求

- Python 3.11 或更高版本，以及 [uv](https://docs.astral.sh/uv/)。
- 一个 MySQL 数据库，连接信息在 `.env` 中配置。

> [!NOTE]
> 先把 [`.env-example.zh`](./.env-example.zh) 复制为 `.env`，填好模型服务密钥与数据库连接再启动。

> [!TIP]
> 完整分布式栈（nginx、MySQL、Redis、web、worker、sandbox manager）见 [`compose.yaml`](./compose.yaml)。

### 安装与运行

```powershell
uv sync                                   # 安装依赖
Copy-Item .env-example .env               # 准备配置，填写模型服务密钥与数据库连接
uv run python main.py bootstrap-db        # 初始化数据库
uv run python main.py bootstrap-developer # 创建第一个账号
uv run python main.py serve               # 启动服务
```

启动成功后，在浏览器打开 <http://127.0.0.1:8765>。

### 登录与使用

用 `bootstrap-developer` 创建的账号登录后即可开始：

- 学习者 —— <http://127.0.0.1:8765/>
- 教师 —— <http://127.0.0.1:8765/teacher>
- 开发者 —— <http://127.0.0.1:8765/developer>

也可以直接在命令行里对话：

```powershell
uv run python main.py chat
```

需要运行监控时，先执行 `uv run python main.py monitor`，再打开 <http://127.0.0.1:8766/>。

## 文档

- [贡献指南](./CONTRIBUTING.zh.md)
- [配置与指南](./docs/)
- [许可证](./LICENSE)

---

本仓库采用 [MIT](./LICENSE) 许可证开源。