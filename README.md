# Nova

<p align="center">
  <img src="webui/logo/nova.png" alt="Nova" width="210">
</p>

<p align="center">
  <strong>你的自然语言处理学习与实践助手</strong><br>
  从一个问题开始，让学习、练习和复盘变得更简单。
</p>

## Nova 是什么

Nova 是一个面向自然语言处理学习场景的智能体。你可以和它对话、提问、练习，也可以让它根据你的学习情况给出下一步建议。

## 四个核心模块

| 模块 | 你可以做什么 |
| --- | --- |
| 学习者 | 提问、学习知识点、完成练习、查看学习记录 |
| 教师 | 组织学习内容、查看学习情况、辅助准备教学活动 |
| 开发者 | 查看运行状态、调试对话、检查接口和事件 |
| 运行监控 | 观察服务健康度、请求状态和实时运行信息 |

## 快速启动

### 1. 准备环境

请先安装 Python 3.11 或更高版本，以及 [uv](https://docs.astral.sh/uv/)。然后打开 PowerShell，进入项目目录：

```powershell
cd E:\Github\Pro_NLP
```

### 2. 安装依赖

```powershell
uv sync
```

### 3. 创建配置文件

复制配置模板：

```powershell
Copy-Item .env-example .env
```

用记事本打开 `.env`：

```powershell
notepad .env
```

至少确认以下配置已经填写：

```dotenv
DEEPSEEK_API_KEY=你的模型服务密钥
NLP_AGENT_DATABASE_URL=mysql+aiomysql://用户:密码@主机:3306/nlp_agent?charset=utf8mb4
```

保存后关闭记事本。`.env` 只用于本机配置，不要提交到 Git。

数据库迁移完成后，首次部署使用交互式命令创建唯一的开发者账号；账号
密码不会写入仓库或环境文件：

```powershell
uv run python main.py bootstrap-developer
```

### 4. 启动 Nova Web

```powershell
uv run python main.py serve
```

看到服务启动提示后，在浏览器打开：

<http://127.0.0.1:8765>

### 5. 访问不同模块

- 学习者：<http://127.0.0.1:8765/>
- 教师：<http://127.0.0.1:8765/teacher>
- 开发者：<http://127.0.0.1:8765/developer>
- 运行监控：先执行 `uv run python main.py monitor`，再打开监控页面

## 其他启动方式

命令行对话：

```powershell
uv run python main.py chat
```

运行监控服务：

```powershell
uv run python main.py monitor
```

停止服务时，在对应 PowerShell 窗口按 `Ctrl+C` 即可。

## Docker 部署（内网服务器）

1. 复制并填写部署配置：

```powershell
Copy-Item .env-example .env
notepad .env
```

至少替换 `DEEPSEEK_API_KEY`、`NLP_AGENT_MYSQL_PASSWORD`、
`NLP_AGENT_MYSQL_ROOT_PASSWORD`，并将
`SERVER_IP_OR_DOMAIN` 替换为服务器实际内网 IP 或域名。部署服务器的
`NLP_AGENT_DATABASE_URL` 必须指向该服务器的 MySQL：同一 Compose 部署使用
`mysql:3306`；托管数据库则改为其私网地址。不要填开发机的数据库地址。

2. 构建并启动主服务：

```powershell
docker compose up -d --build
```

Compose 会先启动 MySQL 8.4，再运行一次 `nova-migrate` 执行 Alembic；只有迁移
成功后 Web、Worker 和 Monitor 才会启动。业务表由 Alembic 创建，应用进程不会
运行时建表。MySQL 数据保存在 Docker 卷 `mysql-data`，Redis 只保存队列与实时
传输状态。

3. 如需启动运行监控：

```powershell
docker compose --profile monitor up -d
```

主服务访问地址为 `http://服务器IP:8765`，监控地址为
`http://服务器IP:8766`。数据会分别保存在 Compose 项目作用域内的
`mysql-data` 和 `redis-data` 卷中，更新镜像不会丢失会话数据。测试和生产必须
使用不同的 Compose 项目名、数据库、Redis、密钥和网络；监控端口只开放给内网或 VPN。

图片理解工具的能力范围、安全目录与 VLM 配置见
[`docs/image-understanding.md`](docs/image-understanding.md)。

查看运行状态和日志：

```powershell
docker compose ps
docker compose logs -f nova-web
```

## 项目定位

Nova 当前主要用于内网演示、教学和自然语言处理学习实践。欢迎在使用过程中提出建议，一起把它变成更好用的学习伙伴。
