# 定价引导与统一视觉路由修改方案

状态：已实施
适用范围：模型运行时、配额计价、容器部署、图片理解
目标版本：`feature/extend-kimi-glm` 后续修订

## 1. 背景

当前 Kimi、GLM 与 Qwen-VL 已接入模型运行时，但部署和能力边界仍有三项需要收口：

1. `nova-migrate` 只执行 Alembic DDL 迁移，不会自动安装内置定价规则；
2. `scripts/seed_extended_model_pricing.py` 遇到未闭合的旧版本规则时，会因时间区间重叠而失败；
3. `kimi-k2.6` 原生支持视觉输入，但本项目决定不开放聊天模型的原生视觉入口，所有图片统一交给配置好的专用识图模型处理。

本文定义上述问题的目标架构、修改范围和验收标准。

## 2. 已确认的架构决策

### 2.1 定价由部署引导任务自动安装

数据库结构升级和内置定价安装由唯一的部署引导任务顺序完成：

```text
MySQL ready
    -> Alembic upgrade head
    -> install built-in pricing catalog
    -> validate pricing coverage
    -> nova-web / nova-worker start
```

不得让 `nova-web`、`nova-worker` 等常驻服务在各自启动时写入定价规则。多副本同时启动会造成重复写入、锁竞争和不一致的启动结果。

### 2.2 定价版本不可覆盖，只允许原子切换

已经存在的定价版本及费率字段不得被原地修改。升级只能在一个数据库事务内完成以下动作：

1. 锁定同一 `pricing_key` 的现有规则；
2. 校验目标版本；
3. 必要时闭合受系统管理的旧规则；
4. 插入新版本；
5. 校验该时刻只有一条有效规则；
6. 提交事务。

### 2.3 图片只走统一视觉工具

无论当前聊天模型是 DeepSeek、Qwen、Kimi、GLM，还是后续新增的原生多模态模型，图片都只允许经过以下链路：

```text
用户上传图片
    -> 受控 uploads 目录
    -> image_analyze
    -> OCR / 安全检查 / 结构化请求
    -> model_routes.vision-worker
    -> vision-qwen-plus
    -> qwen3-vl-plus
    -> 文本化识别结果返回当前聊天模型
```

聊天模型不得直接接收图片 Base64、远程图片 URL 或文件二进制。切换聊天模型档案不得改变视觉模型。

### 2.4 不接入 Kimi 原生视觉

`kimi-k2.6` 的确具有原生图片和视频理解能力，但本项目不创建 `vision-kimi` preset，不把 Kimi 加入 `vision-worker` fallback，也不为 Kimi 增加项目侧视觉计价字段。

模型能力元数据与项目路由策略必须分开理解：

- 能力元数据可以如实记录供应商支持的能力；
- 是否实际使用某项能力由项目路由和输入网关决定；
- `capabilities.vision=true` 不能自动获得图片输入，也不能改变 `vision-worker`；
- 如果团队把 `capabilities` 定义为“项目已启用能力”，则 Kimi 的 `vision` 应继续保持未启用，并必须在配置注释和运行时文档中明确该语义。

本次修改推荐采用后一种最小方案：Kimi 的项目能力不声明 `vision`，同时用配置校验和测试固定唯一视觉入口。

## 3. 定价自动初始化设计

### 3.1 建立单一内置定价目录

将当前脚本中的规则定义提取为可复用模块，例如：

```text
server/quota/builtin_pricing.py
```

该模块只保存经过证据核验的内置规则，不读取环境变量，也不直接建立数据库连接。命令行脚本、部署引导任务和测试均从这里导入同一份定义，避免复制费率。

内置目录至少需要覆盖：

- 生产配置实际可达的所有模型 `pricing_key`；
- `qwen/qwen3-vl-plus` 的视觉 Token 与图片预留字段；
- 已启用的收费工具，例如 `feature/image-understanding` 和 `feature/link-read`；
- 已启用且会产生额外费用的原生搜索字段。

没有可靠价格证据的规则不得自动填零。应将其列入缺失清单，并在配额开启时阻止部署通过。

### 3.2 新增统一数据库引导命令

新增一个适合本地和容器共同使用的命令，例如：

```powershell
python main.py bootstrap-db
```

命令执行顺序固定为：

1. 执行 `alembic upgrade head`；
2. 调用内置定价安装服务；
3. 校验当前生产路由的定价覆盖；
4. 输出结构化摘要并以进程退出码表示成功或失败。

建议的摘要字段：

```json
{
  "schema": "up_to_date",
  "pricing": {
    "created": [],
    "already_present": [],
    "superseded": [],
    "conflicts": [],
    "missing": []
  }
}
```

`compose.yaml` 中的 `nova-migrate` 改为调用该命令，其他服务继续依赖 `nova-migrate` 成功结束后再启动。

### 3.3 启动预检

部署引导完成后必须执行定价覆盖检查：

- `quota_enforcement_enabled=true`：任何可达模型或收费功能缺少有效规则时，引导任务失败；
- `quota_enforcement_enabled=false`：允许启动，但输出明确警告，相关用量只能记录为 `pending`；
- 同一 `pricing_key` 在当前时刻存在零条或多条有效规则，都视为配置错误；
- 视觉路由启用时，`qwen/qwen3-vl-plus` 必须同时具有 `visual_input_credits_micro_per_million_tokens` 和 `image_unit_credits_micro`。

## 4. 旧定价规则平滑升级设计

### 4.1 新增原子安装接口

在 `QuotaManagementService` 增加面向系统内置规则的接口，例如：

```python
install_builtin_pricing_version(definition, *, installed_by, now)
```

该接口不能由“先调用 `retire_pricing_rule`，再调用 `create_pricing_rule`”拼接实现，两个动作必须共享同一事务和同一行锁。

### 4.2 幂等和冲突规则

对每个 `pricing_key` 按以下顺序处理：

1. **目标版本存在且全部费率一致**：返回 `already_present`；
2. **目标版本存在但任一费率不同**：返回冲突并失败，禁止修改已有版本；
3. **不存在重叠规则**：直接创建目标版本；
4. **存在系统管理的开放旧规则**：原子闭合旧规则并创建新规则；
5. **存在人工创建或来源不明的重叠规则**：拒绝自动覆盖，返回规则 ID、版本和处理建议。

系统管理规则通过 `created_by` 的受控值识别，例如：

```text
system:pricing-bootstrap
p4-model-pricing
```

不允许仅凭版本名称判断规则是否可以自动修改。

### 4.3 生效时间

- 新数据库没有历史规则时，使用该价格的官方生效时间；
- 已运行环境从旧规则升级时，切换时间取“官方生效时间”和“本次首次安装时间”中的较晚值；
- 不得为了安装新版本而追溯改写已经结算的历史区间；
- 所有时间统一使用 UTC，并采用 `[effective_from, effective_until)` 半开区间。

### 4.4 失败和回滚

- 任一规则安装失败时，当前 `pricing_key` 的事务必须整体回滚；
- 部署引导返回非零退出码，常驻服务不得启动；
- 应用版本回滚时不自动删除已经产生用量引用的新价格版本；
- 定价回滚必须通过新增修正版或显式管理操作完成，不能修改历史费率。

## 5. 统一视觉路由约束

### 5.1 配置要求

保留以下唯一视觉预设和路由：

```yaml
model_presets:
  vision-qwen-plus:
    model: qwen3-vl-plus

model_routes:
  vision-worker:
    primary: vision-qwen-plus
    fallbacks: []
```

禁止以下配置：

- 在 `model_profiles` 中增加可覆盖视觉模型的字段；
- 根据当前聊天 profile 动态选择视觉供应商；
- 将 `vision-kimi`、`vision-glm` 等聊天供应商视觉 preset 加入 fallback；
- 因 Kimi 缺少密钥而影响 Qwen 视觉路由构建；
- 因 Qwen 缺少密钥而阻止其他已配置模型进行纯文本聊天。

### 5.2 API Key 边界

- 图片理解唯一依赖 `QWEN_API_KEY`；
- Kimi 文字聊天只依赖 `KIMI_API_KEY`；
- GLM 文字聊天只依赖 `GLM_API_KEY`；
- `KIMI_API_KEY` 为空不得影响图片工具；
- `QWEN_API_KEY` 为空时，图片工具返回明确的“视觉模型未配置”错误，但不能影响 GLM、Kimi 或其他模型的文字聊天。

### 5.3 计价边界

视觉模型调用使用 `qwen/qwen3-vl-plus` 规则结算。OCR-only 路径如产生独立内部成本，则继续使用 `feature/image-understanding`；不产生外部成本时不得重复收取视觉模型费用。

因为 Kimi 不进入视觉路由，本次不需要：

- `visual_input_credits_micro_per_million_tokens` for `kimi/kimi-k2.6`；
- `image_unit_credits_micro` for `kimi/kimi-k2.6`；
- Kimi 图片 Token 估算接口；
- Kimi 图片真实账号冒烟测试。

## 6. 文件级修改清单

| 文件 | 修改要求 |
| --- | --- |
| `server/quota/builtin_pricing.py` | 新增经过核验的单一定价目录 |
| `server/quota/management.py` | 新增事务内的内置定价版本安装/切换接口 |
| `scripts/seed_extended_model_pricing.py` | 改为复用统一目录和安装接口，保留预览与手动执行能力 |
| `main.py` 或独立 bootstrap 模块 | 新增 `bootstrap-db` 命令，串联迁移、定价安装和覆盖校验 |
| `compose.yaml` | `nova-migrate` 改为执行统一数据库引导命令 |
| `configs/agent_config.yaml` | 保持唯一 `vision-worker -> vision-qwen-plus`，增加架构注释 |
| `core/model_runtime/factory.py` | 确保缺少 Qwen Key 只影响视觉路由的懒加载，不影响文字模型 |
| `docs/image-understanding.md` | 写明所有聊天模型统一通过 Qwen-VL 工具识图 |
| `docs/model-runtime.md` | 写明模型原生能力与项目启用能力/路由策略的区别 |
| `docs/migrations.md` | 增加本地与容器的数据库引导命令 |

## 7. 测试要求

### 7.1 定价安装

必须覆盖：

1. 空数据库首次安装全部规则；
2. 同一版本重复安装完全幂等；
3. 同版本费率不同必须失败；
4. 系统管理的旧开放规则能够被原子闭合；
5. 人工规则发生重叠时不得自动修改；
6. 多个 `pricing_key` 中任一失败时给出可定位结果；
7. 配额开启时缺失定价导致部署预检失败；
8. 配额关闭时缺失定价只产生警告和 `pending` 用量；
9. 新旧规则交界时间不存在空窗或双重有效区间；
10. 并发执行引导任务时不产生重复版本。

### 7.2 统一视觉路由

必须覆盖：

1. DeepSeek、Qwen、Kimi、GLM profile 解析到同一个 `vision-worker`；
2. 切换聊天 profile 不改变视觉候选模型；
3. 图片二进制不会进入 Coordinator 或普通 Worker 请求；
4. `KIMI_API_KEY=""` 时 Qwen 图片识别仍可构建和调用；
5. 仅配置 GLM Key 时文字聊天可用，图片功能返回视觉模型未配置；
6. 仅配置 Qwen Key 时图片功能可用；
7. `vision-worker` 的主模型必须声明 `vision` 和 `structured_output`；
8. 任意 profile 都不能覆盖或追加视觉 fallback；
9. Qwen-VL 用量只按 Qwen 视觉定价结算，不落入 Kimi 或 GLM 规则；
10. 猫图、人物插画、文字截图至少各完成一次端到端识别冒烟测试。

## 8. 验收标准

完成修改后应同时满足：

- `docker compose up` 在全新数据库上无需手动执行定价脚本；
- 已存在旧定价规则的数据库能够安全升级，或对人工冲突给出明确阻塞信息；
- 重复部署不会生成重复规则；
- 开启配额后，所有生产可达模型和收费功能均有唯一有效定价；
- Kimi Key 为空不影响 GLM 聊天和 Qwen 图片识别；
- 任何聊天模型上传图片时，实际视觉供应商始终为 `qwen3-vl-plus`；
- 后续新增多模态聊天模型时，无需新增视觉 preset，仍自动遵守统一视觉路由；
- 文档、配置注释和自动化测试对上述边界保持一致。

## 9. 证据来源

- Kimi K2.6 官方能力与多模态调用说明：<https://platform.kimi.com/docs/guide/kimi-k2-6-quickstart>
- Kimi 视觉输入格式和限制：<https://platform.kimi.com/docs/guide/use-kimi-vision-model>
- Kimi Chat API 与结构化输出：<https://platform.kimi.com/docs/api/chat>
- 项目视觉架构：`docs/image-understanding.md`
- 项目模型运行时：`docs/model-runtime.md`
- 当前定价脚本：`scripts/seed_extended_model_pricing.py`
