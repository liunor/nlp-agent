# CI/CD 与发布流程

## 分支约定

- `feature/*`：从 `develop` 创建，完成后以 Pull Request 合并回 `develop`。
- `develop`：集成分支；CI 成功后构建、推送测试镜像并部署测试环境。
- `main`：受保护的可发布分支；只接受来自 `develop` 的 Pull Request。

## 工作流

| 工作流 | 触发条件 | 作用 |
| --- | --- | --- |
| `CI` | feature、develop、main 的 push / PR | Python 与前端检查、完整测试、Docker 构建验证 |
| `Publish Test Image` | push 到 `develop` 或 `main` | `develop` 发布测试镜像；`main` 发布带 commit SHA 的 release candidate，并在测试环境进行健康检查 |
| `Release Production` | push `vX.Y.Z` Tag | 校验 Tag 属于 main，把已在测试环境验证的 candidate digest 晋级为正式 tag；生产不重新构建镜像 |
| `Sync Gitee Mirror` | 生产发布完成或手动执行 | 同步 GitHub main 与所有 Tag 到 Gitee |

## GitHub 配置清单

1. 将 `main`、`develop` 设为受保护分支；要求 `CI` 成功后才能合并。
2. 创建 GitHub Environments：`test`、`production`；对 `production` 配置人工审批。
3. 在测试服务器安装带有 `self-hosted`、`linux`、`test` 标签的 GitHub Actions Runner。
4. 在生产服务器安装带有 `self-hosted`、`linux`、`production` 标签的 Runner。
5. 在两个 Environment 的 Variables 中设置 `DEPLOY_DIR`，该目录只保存服务器本地 `.env`。
6. 将 GHCR 包设为可被服务器拉取；若包保持私有，为服务器 Docker 登录配置只读 package token。
7. 在仓库 Secrets 中配置 `GITEE_SSH_PRIVATE_KEY` 与 `GITEE_KNOWN_HOSTS`。

## 服务器部署目录

测试环境使用 `/opt/nova-test`，生产环境使用 `/opt/nova-prod`。两个目录中只维护服务器本地 `.env`。部署工作流从仓库 checkout 的 `compose.yaml` 启动服务，并通过 `NOVA_ENV_FILE` 显式读取该服务器 `.env`，因此不会覆盖密钥文件。

严格部署时测试和生产必须使用不同主机或 VM，最好位于不同 VPC/网络安全域；两边分别使用独立 MySQL、Redis、Docker 凭据、模型密钥、会话密钥和备份策略。若只是本地临时联调，可以用不同 Compose 项目名和端口模拟隔离，但这不满足生产隔离要求。工作流分别使用 Compose 项目名 `nova-test`、`nova-prod`，数据卷彼此隔离；测试 Web/Monitor 默认使用 `18765/18766`，生产使用 `8765/8766`。

首次准备服务器配置：

```bash
# Test server
cp deploy/env/test.env.example /opt/nova-test/.env
chmod 600 /opt/nova-test/.env
nano /opt/nova-test/.env

# Production server
cp deploy/env/production.env.example /opt/nova-prod/.env
chmod 600 /opt/nova-prod/.env
nano /opt/nova-prod/.env
```

将模板中的地址占位符和模型密钥替换为对应环境的真实值。用户密码和
角色不写入部署文件，首次部署后通过 `python main.py bootstrap-developer`
交互式创建开发者账号。

服务器 `.env` 必须包含：

```dotenv
NOVA_IMAGE_REF=ghcr.io/liunor/nlp-agent@sha256:...
NOVA_PULL_POLICY=always
DEEPSEEK_API_KEY=...
NLP_AGENT_WEB_ALLOWED_HOSTS=你的内网IP或域名
NLP_AGENT_WEB_ALLOWED_ORIGINS=http://你的内网IP或域名:18765
NLP_AGENT_DATABASE_URL=mysql+aiomysql://测试专用用户:密码@mysql:3306/测试专用数据库
```

## 正式发布

1. `develop` 的变更通过 CI 后自动部署测试环境。
2. 将已通过人工测试的 `develop` 以 Pull Request 合并至 `main`；`main` push 会生成 release candidate 并部署到测试环境复核。
3. 确认 candidate 的测试结果后，从 `main` 创建并推送带注释的版本 Tag，例如 `v1.0.1`。
4. `Release Production` 只把 candidate 的 digest 晋级为 `ghcr.io/liunor/nlp-agent:v1.0.1`，等待 production 审批后部署。
5. 部署成功后自动同步 Gitee。
