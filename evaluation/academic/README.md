# 学术检索质量评测

正式套件 ID 为 `academic-routing-v1`，数据位于
`.jbeval/suites/academic-routing-v1/dataset.yaml`。

它复用统一评测运行器，并额外统计：

- `academic_search_recall`：应检索问题实际调用 `academic_search` 的比例；
- `academic_false_positive_rate`：无需工具的问题错误调用学术检索的比例；
- `citation_integrity_rate`：最终学术链接全部存在于本轮工具结果的比例。

先启动 Web、Monitor，再运行小样本：

```powershell
$env:NLP_AGENT_EVALUATION_USERNAME='evaluation-user'
$env:NLP_AGENT_EVALUATION_PASSWORD='<password>'
uv run python -m evaluation validate .jbeval/suites/academic-routing-v1/dataset.yaml
uv run python -m evaluation run academic-routing-v1 --live --limit 2
```

`--live` 会产生模型调用费用；数据集校验和单元测试不调用外部模型。

关闭/故障场景需在独立测试环境验证，不能在正常套件中只设置
`forbidden_tools: [academic_search]`：功能关闭后工具仍可调用并返回 skipped。
重启加载 `tools.academic.enabled=false` 的测试后端，再提问
“Transformer 最初由哪篇论文提出？请给出官方论文链接”。
验收条件：工具无外部 Provider 请求；答复说明无法核验学术出处，
不凭记忆补写论文信息或链接。部分失败则允许引用成功来源返回的链接。
现有引用完整性评测不是运行时拦截器，Prompt 回归测试也不能代替此真实模型验收。
