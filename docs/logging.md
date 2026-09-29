# 日志查看与保留

项目有两条互补的观测链路：

- 应用日志：结构化 JSONL，记录异常、依赖错误、启动和 Worker 运行信息；
- Observability Monitor：查询 MySQL 中的 Trace、Span、Token 和 Telemetry Event，地址默认是 `8766`。

## Docker 部署

Compose 为 `nova-web`、`nova-worker`、`nova-monitor`、`nova-sandbox-manager` 和迁移任务统一配置：

- 日志写入容器标准输出，因此 `docker compose logs` 可直接查看；
- 同时写入持久化卷 `nova-logs`，容器重建不会丢失文件日志；
- 按服务隔离到 `/app/logs/<service>/YYYY-MM-DD/<启动时间>-p<PID>/`；
- 默认保留 14 天，可在 Compose 环境变量中调整 `NLP_AGENT_LOG_RETENTION_DAYS`。

常用命令：

```bash
docker compose ps
docker compose logs -f --tail=200 nova-web
docker compose logs --since=1h --no-color nova-worker
docker compose --profile monitor logs -f --tail=200 nova-monitor
```

## 统一查询命令

如果需要跨重启、按级别或 Trace 查询，在应用容器内运行：

```bash
docker compose exec nova-web .venv/bin/python scripts/logs.py --log-dir /app/logs --service nova-web --tail 100
docker compose exec nova-web .venv/bin/python scripts/logs.py --log-dir /app/logs --level error --tail 100
docker compose exec nova-web .venv/bin/python scripts/logs.py --log-dir /app/logs --trace-id TRACE_ID --json
docker compose exec nova-web .venv/bin/python scripts/logs.py --log-dir /app/logs --since 2026-09-11T00:00:00Z
```

`all.log` 是查询的权威输入；`warning.log` 和 `error.log` 是便于人工快速定位的级别子集，不要把它们与 `all.log` 同时合并读取，否则会重复统计。

## 现有历史日志

本地历史文件已经在仓库根目录的 `logs/` 下，旧布局通常是：

```text
logs/YYYY-MM-DD/HH-MM-SS/{all,warning,error}.log
```

新的查询命令兼容这个布局，也兼容部署后的按服务布局：

```powershell
.venv\Scripts\python.exe scripts\logs.py --log-dir logs --level error --tail 100
```

服务器上如果没有文件日志，先看 `docker compose logs`；如果连标准输出也没有，检查部署是否使用了包含本次日志配置的 Compose 文件，以及容器是否正在运行。`docker compose down -v` 会删除包括 `nova-logs` 在内的命名卷，不应在需要保留日志时使用。
