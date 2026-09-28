# 学术检索生产运行手册

## 运行模式

Coordinator 当前使用 1.5 提示词：无可核验结果时明确说明无法核验，
禁止凭记忆补写论文出处；部分失败仍允许引用成功来源。
这属于模型行为约束，不是代码级输出拦截。
查询及元数据缓存均按 Provider 配置和启用来源隔离，旧缓存等待 TTL 到期，
无需清空 Redis。修改配置后应重启后端，使 Provider 实例与配置一致。

`tools.academic.reliability` 控制 P2 可靠性能力。配置了
`NLP_AGENT_REDIS_URL` 时，所有 Web/Worker 实例共享：

- arXiv 请求起始时间槽（全局最少间隔 3 秒）；
- 查询结果缓存；
- 单篇论文元数据缓存；
- 按 UTC 日期保存的 Provider、缓存和零扣费使用计数。

Redis URL 未配置时，单进程开发模式使用进程内锁和内存 TTL 缓存。Redis URL
已经配置但服务超时或连接失败时，缓存继续降级到内存；arXiv Provider 则暂停，
因为退回进程内限流无法保证多实例合计仍满足 3 秒间隔。Redis 故障会进入冷却期，
避免每次查询都重复等待连接超时，其他 Provider 仍可返回部分结果。

## 配置

```dotenv
NLP_AGENT_REDIS_URL=redis://127.0.0.1:6379/0
SEMANTIC_SCHOLAR_API_KEY=
```

Semantic Scholar Key 可选。匿名接口出现 429 时，服务保留 arXiv 结果并返回
`partial=true`。

## 观测

开发者快照的 `tools.academic_metrics` 包含进程内实时指标和可用时的 Redis 当日
计数。也可以直接读取 Redis 指标：

```powershell
uv run python scripts/academic_metrics.py
uv run python scripts/academic_metrics.py --day 2026-09-06
```

其中 `zero_charge_searches` 是免费学术检索独立使用量，`billable_charges` 固定为
零，不进入 `search_calls` 或 `link_pages`。

## 验证

离线测试：

```powershell
uv run pytest tests/test_academic_contracts.py tests/test_academic_providers.py tests/test_academic_service.py tests/test_academic_reliability.py -q
```

连接本机 Redis 的显式集成测试：

```powershell
$env:NLP_AGENT_REDIS_INTEGRATION='1'
uv run pytest tests/test_academic_redis_integration.py -q
```

学术路由评测（会调用已配置模型并可能产生模型费用）：

```powershell
$env:NLP_AGENT_EVALUATION_USERNAME='evaluation-user'
$env:NLP_AGENT_EVALUATION_PASSWORD='<password>'
uv run python -m evaluation validate .jbeval/suites/academic-routing-v1/dataset.yaml
uv run python -m evaluation run academic-routing-v1 --live --limit 2
```

真实学术 API 冒烟位于独立的 `academic-smoke.yml` 周期工作流，不属于普通 CI，
因此外部 API 故障不会阻断 PR。

## 降级与回滚

1. 未配置 Redis URL 的单进程开发环境使用本地限流和内存缓存。
2. 已配置 Redis 后若 Redis 故障：共享缓存降级为内存缓存；arXiv 单源暂停，避免多实例绕过全局 3 秒限流；其他 Provider 继续返回，响应标记 `partial=true`。
3. 单个学术 Provider 熔断：其他信源继续返回，响应标记 `partial=true`。
4. 关闭共享能力：仅限确认单进程运行时，设置 `tools.academic.reliability.redis_enabled=false`。
5. 关闭整个功能：设置 `tools.academic.enabled=false`，并将 Coordinator Prompt
   切回 1.3。
