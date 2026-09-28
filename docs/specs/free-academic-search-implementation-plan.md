# 免费学术检索代码实施方案

## 1. 文档状态

- 状态：待实施
- 目标版本：第一阶段 MVP
- 成本约束：不购买商业学术检索 API，不部署 Google Scholar 代理或爬虫，不增加常规查询的独立 Worker 模型调用
- 适用范围：算法、模型理论、学术概念、论文出处、作者、时间、论文对比等专业问题

## 2. 目标与非目标

### 2.1 目标

1. Coordinator 自动识别学术问题并直接调用 `academic_search`。
2. 使用免费的国际学术数据源检索论文。
3. 返回标题、作者、摘要、提交/出版时间、论文状态和可验证链接。
4. 所有论文 URL 必须来自工具数据或确定性的官方 ID 映射，模型不得自行拼接论文编号。
5. 单个数据源失败时返回部分结果和明确的信源状态。
6. 低访问量场景的外部 API 直接费用为零。

### 2.2 第一阶段不做

1. 不抓取 Google Scholar 搜索结果页。
2. 不使用代理池、验证码绕过或仿真浏览器请求头。
3. 不下载或解析论文 PDF。
4. 不同步 Semantic Scholar 或 ACL Anthology 全量数据库。
5. 不建设向量数据库。
6. 不把所有学术问题默认交给 `academic_researcher` Worker。
7. 不保证外部服务 100% 可用，也不使用“零幻觉”作为承诺。

## 3. 总体设计

```text
用户问题
   |
   v
Coordinator 学术意图判断
   |
   v
academic_search（纯代码、无额外 LLM）
   |
   +-- arXiv：AI/NLP 预印本、作者摘要、版本时间
   +-- Semantic Scholar：国际论文发现、相关性和外部 ID
   +-- Crossref：DOI、正式出版信息的按需补全
   +-- ACL ID 映射：生成 ACL Anthology 官方链接
   |
   v
规范化 -> 去重 -> 排序 -> 前 5 篇 -> 结构化 JSON
   |
   v
Coordinator 根据检索结果组织中文答案
```

常规学术查询由 Coordinator 直接调用工具，以避免额外 Worker 模型费用。只有用户明确要求综述、大量论文比较或多轮证据分析时，后续版本才启用 `academic_researcher`。

## 4. 免费信源及职责

| 信源 | 第一阶段作用 | 鉴权 | 调用规则 |
|---|---|---|---|
| arXiv | 预印本检索、标题、作者、Abstract、首次提交和更新时间 | 无 | 全局单连接；两次请求至少间隔 3 秒 |
| Semantic Scholar | 综合论文检索、相关性排序、DOI/arXiv/ACL ID、Venue | 免费 API Key 可选，建议申请 | Key 模式按 1 RPS 起步；只请求必要字段 |
| Crossref | 正式 DOI、出版社、会议/期刊和正式出版日期补全 | 无；使用 `mailto` polite pool | 仅补全前 5 篇中信息缺失的记录 |
| ACL Anthology | NLP 论文官方落地页 | 无 | 第一阶段根据已核验 ACL ID 生成规范 URL，不做全库同步 |
| Google Scholar | 用户手动核验入口 | 无 | 只生成 URL 编码后的标题查询链接，不读取结果页 |

Semantic Scholar 的“免费”指其许可范围内无需按请求付费，不代表可无条件用于公开或商业产品。默认许可仅覆盖内部、非商业研究/教育用途；公开展示必须遵守当前 API License 的回链与品牌归因要求，商业使用必须在上线前取得适用的扩展许可。

Google Scholar 链接格式：

```text
https://scholar.google.com/scholar?q=<URL 编码后的论文标题>
```

该链接只表示“去 Google Scholar 查询”，不能作为论文元数据来源。

## 5. 文件改动清单

### 5.1 新增文件

```text
server/tools/academic/__init__.py
server/tools/academic/contracts.py
server/tools/academic/provider.py
server/tools/academic/arxiv.py
server/tools/academic/semantic_scholar.py
server/tools/academic/crossref.py
server/tools/academic/normalize.py
server/tools/academic/service.py
server/tools/api/academic_search_tool.py
scripts/smoke_academic_search.py
tests/fixtures/academic/arxiv_transformer.xml
tests/fixtures/academic/semantic_scholar_lora.json
tests/fixtures/academic/crossref_lora.json
tests/test_academic_contracts.py
tests/test_academic_providers.py
tests/test_academic_service.py
tests/test_academic_search_tool.py
core/prompt_runtime/templates/coordinator.v1.4.md
```

### 5.2 修改文件

```text
pyproject.toml
requirements.txt
.env-example
core/tool_config.py
server/tools/tool_manager.py
configs/agent_config.yaml
tests/test_builtin_tool_registration.py
tests/test_worker_profiles.py
webui/src/modules/student/components/MarkdownContent.tsx
webui/src/modules/student/components/MarkdownContent.test.tsx
```

第一阶段不修改 `worker.v1.3.md`，因为专项检索纪律属于 Coordinator 路由和工具数据契约，不应影响无关 Worker。

## 6. 依赖调整

在 `pyproject.toml` 和 `requirements.txt` 增加：

```text
defusedxml>=0.7.1
```

继续使用项目已有的 `httpx`、`pydantic` 和现有 TTL 缓存实现，不引入搜索 SDK。

`defusedxml` 用于解析 arXiv Atom XML，避免把外部 XML 当作完全可信输入。

## 7. 配置模型

在 `core/tool_config.py` 增加严格配置模型：

```python
class AcademicArxivConfig(StrictConfigModel):
    enabled: bool = True
    base_url: str = "https://export.arxiv.org/api/query"
    min_interval_s: float = Field(default=3.0, ge=3.0)
    timeout_s: float = Field(default=15.0, gt=0, le=60)


class AcademicSemanticScholarConfig(StrictConfigModel):
    enabled: bool = True
    base_url: str = "https://api.semanticscholar.org/graph/v1"
    api_key_env: str = "SEMANTIC_SCHOLAR_API_KEY"
    timeout_s: float = Field(default=10.0, gt=0, le=60)


class AcademicCrossrefConfig(StrictConfigModel):
    enabled: bool = True
    base_url: str = "https://api.crossref.org"
    mailto_env: str = "ACADEMIC_CROSSREF_MAILTO"
    timeout_s: float = Field(default=10.0, gt=0, le=60)


class AcademicToolsConfig(StrictConfigModel):
    enabled: bool = True
    max_results: int = Field(default=5, ge=1, le=10)
    search_cache_ttl_s: int = Field(default=86400, ge=0, le=604800)
    metadata_cache_ttl_s: int = Field(default=2592000, ge=0)
    arxiv: AcademicArxivConfig = Field(default_factory=AcademicArxivConfig)
    semantic_scholar: AcademicSemanticScholarConfig = Field(
        default_factory=AcademicSemanticScholarConfig
    )
    crossref: AcademicCrossrefConfig = Field(default_factory=AcademicCrossrefConfig)
```

在 `ToolRuntimeConfig` 中增加：

```python
academic: AcademicToolsConfig = Field(default_factory=AcademicToolsConfig)
```

生产代码不得允许用户覆盖 `base_url`，这些地址只能由运维配置控制，避免把检索工具变成任意 URL 请求器。

## 8. YAML 与环境变量

在 `configs/agent_config.yaml` 的 `tools` 下增加：

```yaml
academic:
  enabled: true
  max_results: 5
  search_cache_ttl_s: 86400
  metadata_cache_ttl_s: 2592000
  arxiv:
    enabled: true
    base_url: "https://export.arxiv.org/api/query"
    min_interval_s: 3
    timeout_s: 15
  semantic_scholar:
    enabled: true
    base_url: "https://api.semanticscholar.org/graph/v1"
    api_key_env: "SEMANTIC_SCHOLAR_API_KEY"
    timeout_s: 10
  crossref:
    enabled: true
    base_url: "https://api.crossref.org"
    mailto_env: "ACADEMIC_CROSSREF_MAILTO"
    timeout_s: 10
```

在 `.env-example` 增加：

```dotenv
# 可选；不配置时使用 Semantic Scholar 未认证公共限额。
SEMANTIC_SCHOLAR_API_KEY=

# 推荐填写实际维护邮箱，用于 Crossref polite pool，不是密钥。
ACADEMIC_CROSSREF_MAILTO=
```

## 9. 数据契约

在 `server/tools/academic/contracts.py` 中定义：

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


AcademicSource = Literal["arxiv", "semantic_scholar", "crossref", "acl"]


class AcademicSearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    query_en: str | None = Field(default=None, max_length=500)
    sources: list[AcademicSource] = Field(default_factory=list)
    max_results: int = Field(default=5, ge=1, le=10)
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    sort: Literal["relevance", "recent"] = "relevance"


class AcademicAuthor(BaseModel):
    name: str
    author_id: str | None = None


class AcademicIdentifiers(BaseModel):
    doi: str | None = None
    arxiv_id: str | None = None
    acl_id: str | None = None
    semantic_scholar_id: str | None = None
    corpus_id: str | None = None


class AcademicSourceRecord(BaseModel):
    source: AcademicSource
    source_id: str
    source_url: HttpUrl | None = None
    rank: int | None = Field(default=None, ge=1)
    retrieved_at: datetime


class AcademicPaper(BaseModel):
    record_id: str
    title: str
    authors: list[AcademicAuthor]
    abstract: str | None = None
    first_submitted_at: datetime | None = None
    updated_at: datetime | None = None
    published_at: datetime | None = None
    publication_year: int | None = Field(default=None, ge=1000, le=2100)
    publication_status: Literal[
        "preprint", "peer_reviewed", "technical_report", "unknown"
    ] = "unknown"
    venue: str | None = None
    identifiers: AcademicIdentifiers
    landing_url: HttpUrl
    pdf_url: HttpUrl | None = None
    source_records: list[AcademicSourceRecord]


class AcademicSourceStatus(BaseModel):
    source: AcademicSource
    status: Literal["ok", "skipped", "timeout", "rate_limited", "error"]
    message: str = ""


class AcademicSearchResponse(BaseModel):
    query: str
    query_used: str
    papers: list[AcademicPaper]
    source_status: list[AcademicSourceStatus]
    partial: bool
    warnings: list[str] = Field(default_factory=list)
    retrieved_at: datetime
```

约束：

1. `abstract` 只保存数据源明确标记为摘要的内容。
2. 不在工具中生成 `key_contributions`；核心贡献由 Coordinator 根据 Abstract 总结，并标注为总结。
3. 不把 Google Scholar 作为 `AcademicSource`。
4. `published_at` 表示正式出版日期；arXiv 的 `published` 映射为 `first_submitted_at`。
5. 数据缺失时使用 `None`，不得填入猜测值。

## 10. Provider 接口

在 `provider.py` 定义统一协议：

```python
from typing import Protocol


class AcademicProvider(Protocol):
    name: str

    async def search(self, request: AcademicSearchInput) -> list[AcademicPaper]: ...
```

每个 Provider 负责：

1. 固定目标域名和路径。
2. URL 参数编码。
3. 超时、响应大小上限和状态码处理。
4. 将外部结构转换成 `AcademicPaper`。
5. 不负责跨源去重和最终排序。
6. 不静默吞掉 429、超时或格式错误。

## 11. arXiv Provider

请求字段：

```text
search_query
start=0
max_results<=10
sortBy=relevance 或 submittedDate
sortOrder=descending
```

实现要求：

1. 用 `defusedxml.ElementTree` 解析 Atom。
2. 最大响应体限制为 2 MB。
3. 解析 `id`、`title`、`summary`、`author/name`、`published`、`updated`、`journal_ref`、`doi` 和 `link`。
4. 从返回的真实 `id` 提取 arXiv ID，不根据标题推测编号。
5. 用户展示 URL 统一规范化为 `https://arxiv.org/abs/{arxiv_id}`。
6. 保留版本信息用于 `source_id`，去重时使用不带版本的 arXiv ID。
7. `journal_ref` 存在不代表已经完成同行评审；只有其他正式出版元数据得到核验后才设置 `peer_reviewed`。
8. 使用进程级异步锁和单调时钟保证最少 3 秒间隔。
9. 多实例部署时改用现有 Redis 实现分布式限流；Redis 不可用时应暂停 arXiv Provider，不能绕过限流继续请求。

## 12. Semantic Scholar Provider

第一阶段调用：

```text
GET /paper/search
```

只请求：

```text
paperId,title,abstract,authors,year,publicationDate,venue,externalIds,url,openAccessPdf
```

实现要求：

1. 环境变量存在时设置 `x-api-key`，否则使用未认证访问。
2. 不记录 API Key，不把 Key 放入 URL。
3. 请求量限制为每秒一次；收到 429 后读取 `Retry-After`，本次调用不进行无限重试。
4. 使用 `externalIds` 提取 DOI、ArXiv 和 ACL ID。
5. 如果存在 DOI，首选 `https://doi.org/{doi}` 作为正式版本链接。
6. 如果存在 ACL ID，生成 `https://aclanthology.org/{acl_id}/` 作为领域官方链接候选。
7. Semantic Scholar 自身页面可以作为索引记录，但不能优先于 DOI、arXiv 或 ACL 官方落地页。

## 13. Crossref Provider

第一阶段不把 Crossref Abstract 作为主要摘要来源，只用于出版元数据补全，以减少版权和数据清洗问题。

实现要求：

1. 使用 `query.bibliographic` 搜索未知 DOI。
2. 已知 DOI 时使用 `/works/{doi}` 精确查询。
3. 发送明确的 `User-Agent`；配置邮箱时附加 `mailto`。
4. 使用 `select` 只获取 DOI、title、author、published、container-title、publisher、type、URL。
5. 只为最终候选中正式出版信息缺失的论文请求 Crossref。
6. 将 `published-print`、`published-online` 和 `issued` 按明确优先级转换为 `published_at`，并保留来源。
7. Crossref 匹配结果必须经过标题相似度和作者交集检查，不能只取第一条。

## 14. 规范化、去重与链接选择

### 14.1 标题规范化

仅用于匹配，不覆盖展示标题：

1. Unicode NFKC。
2. 转小写。
3. 合并连续空白。
4. 去掉首尾标点。
5. 保留原始标题用于展示。

### 14.2 去重优先级

```text
DOI 完全一致
  > arXiv ID 完全一致
  > ACL ID 完全一致
  > 规范化标题一致且年份相差不超过 1 年
```

合并规则：

1. 作者和标识符取并集。
2. 正式出版日期优先于年份推断。
3. Abstract 优先使用 arXiv 或 Semantic Scholar 的明确摘要字段。
4. 字段冲突时保留各自 `source_records` 并添加 warning，不静默选择。

### 14.3 用户链接优先级

```text
ACL Anthology 官方链接
  > DOI 链接
  > arXiv Abstract 链接
  > Semantic Scholar 索引链接
```

预印本问题可以优先显示 arXiv；正式发表问题优先显示 DOI 或 ACL。

## 15. 聚合服务

`AcademicSearchService.search()` 执行顺序：

1. 验证查询、年份和结果数量。
2. `query_en` 存在时优先用于国际信源；同时保留用户原始查询。
3. 命中查询缓存则直接返回。
4. 并行调用 Semantic Scholar；arXiv 调用受独立限流器控制。
5. 某个 Provider 失败时记录 `AcademicSourceStatus`，不使全部结果失败。
6. 合并并去重候选。
7. 按相关性、标题匹配度、可验证标识符完整度排序。
8. 截取前 5 篇。
9. 仅对缺少正式出版信息的前排结果调用 Crossref。
10. 再次合并、选择规范链接并验证 URL。
11. 生成 Google Scholar 查询 URL 时只使用经过 URL 编码的论文标题。
12. 返回 JSON，不在 Service 内生成 Markdown。

如果所有 Provider 都失败，工具应返回带 `source_status` 的空结果，而不是编造降级数据。

## 16. 缓存与限流

第一阶段复用 `server/tools/web/cache.py` 的 TTL 缓存：

- 规范化查询结果：24 小时。
- 单篇论文元数据：30 天。
- 429 或网络错误：不写正常结果缓存，可设置 30～60 秒失败退避。
- 缓存键包含 Provider、规范化查询、年份、排序和结果数量。
- API Key、邮箱和完整请求头不得进入缓存键或日志。

多 Web 进程部署时，内存缓存不能保证全局 arXiv 限流。上线多实例之前，必须把 arXiv 限流槽迁移到现有 Redis；缓存本身可以继续使用内存方案作为 MVP。

## 17. LangChain 工具封装

在 `server/tools/api/academic_search_tool.py` 中实现：

```python
@tool("academic_search", args_schema=AcademicSearchInput)
async def academic_search(
    query: str,
    query_en: str | None = None,
    sources: list[str] | None = None,
    max_results: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
    sort: str = "relevance",
) -> str:
    """检索国际学术论文元数据和官方出处，返回可核验的结构化结果。"""
```

行为要求：

1. 调用共享 `AcademicSearchService`。
2. 返回 `AcademicSearchResponse.model_dump_json()`。
3. 不捕获并伪装程序错误；可预期的 Provider 错误由 Service 转成状态字段。
4. 工具输出中的 Abstract 按不可信外部数据处理。
5. 第一阶段不调用 `begin_billable_tool_usage`，避免把免费 API 自动计入现有付费搜索价格。
6. 防滥用依赖 Agent 每轮工具调用上限、Provider 限流和结果数量上限。

如果产品必须统计学术检索次数，应新增零积分价格项或单独的免费计数指标，不能直接复用可能产生扣费的 `search_calls`。

## 18. 工具注册与授权

修改 `server/tools/tool_manager.py`：

1. 导入 `academic_search`。
2. 加入 `ALL_AVAILABLE_TOOLS`，从而进入可压缩工具注册。
3. 增加 `ToolDescriptor`：

```python
ToolDescriptor(
    name=academic_search.name,
    description=academic_search.description,
    source=ToolSource.BUILTIN,
    provider="academic-open-apis",
    scopes=frozenset({ToolScope.COORDINATOR, ToolScope.WORKER}),
    capabilities=frozenset({"academic.search"}),
    risk=ToolRisk.MEDIUM,
    read_only=True,
    concurrency_safe=True,
    timeout_s=35,
    max_concurrency=4,
    retry=ToolRetryPolicy(max_attempts=1),
    factory=lambda: academic_search.model_copy(deep=True),
)
```

整个工具只重试一次，因为各 Provider 已独立处理超时和限流；重复执行聚合工具可能增加免费接口的限流压力。

修改 `configs/agent_config.yaml`：

```yaml
tools:
  policies:
    coordinator:
      allowed_tools:
        - academic_search
        # 保留现有条目
      allowed_capabilities:
        - academic.search
        # 保留现有条目
```

不要将 `academic_search` 加入全局 Worker 权限。后续创建 `academic_researcher` 时再通过 Profile 单独授权。

## 19. Coordinator Prompt 1.4

复制 `coordinator.v1.3.md` 为 `coordinator.v1.4.md`，保留原有内容，在“联网路由”中增加以下学术路由：

```text
学术检索路由：

1. 用户询问具体算法、模型架构、理论机制、论文出处、作者、年份、
   学术方法对比、实验结论或当前研究进展时，必须先调用 academic_search。
2. 学术问题即使属于稳定知识，也不能只依赖模型记忆给出论文题目、作者、
   年份、DOI 或 arXiv 编号。
3. 中文问题应向 academic_search 同时提供简洁的英文关键词、标准缩写和原始查询；
   不确定译名时不得自行创造专有名词。
4. 一般问题默认返回最相关的 3～5 篇，不罗列大量低相关结果。
5. 论文标题、作者、时间、Abstract 和 URL 只能使用工具返回字段。
6. 核心贡献可以根据 Abstract 进行概括，但必须表述为总结，不能伪装成原文。
7. 区分预印本首次提交时间与会议、期刊的正式出版时间。
8. 每个可点击论文链接都必须来自当前 academic_search 结果。
9. 信源部分失败时说明缺少的信源；不得声称检索完整。
10. Google Scholar 链接仅作为二次核验入口，不得声称结果来自 Scholar。
11. 只有官方技术报告不在学术数据源中时，才补充启动 web_researcher；
    结果必须来自发布机构自身域名。
```

把 `configs/agent_config.yaml` 中的 Coordinator Prompt 版本从 `1.3` 改为 `1.4`。不要直接修改旧版本模板，以保留可回滚性。

## 20. 前端外链

第一阶段在 `MarkdownContent.tsx` 中新增严格的学术链接判断：

```typescript
const TRUSTED_ACADEMIC_HOSTS = new Set([
  "arxiv.org",
  "doi.org",
  "aclanthology.org",
  "www.semanticscholar.org",
  "scholar.google.com",
]);

function isTrustedAcademicLink(href: string | undefined): href is string {
  if (!href) return false;
  try {
    const url = new URL(href);
    return url.protocol === "https:"
      && !url.username
      && !url.password
      && !url.port
      && TRUSTED_ACADEMIC_HOSTS.has(url.hostname.toLowerCase());
  } catch {
    return false;
  }
}
```

链接渲染逻辑调整为：

```typescript
if (isSameOriginMarkdownLink(href)) {
  return <a {...props} href={href}>{value}</a>;
}
if (isTrustedAcademicLink(href)) {
  return <a {...props} href={href} target="_blank" rel="noopener noreferrer">{value}</a>;
}
return <span className="external-link-removed">{value}</span>;
```

第一阶段不要把 GitHub、Hugging Face 或 Papers with Code 放入论文官方域名集合。它们属于补充资源，后续应使用单独策略。

长期方案是由服务端返回结构化 Citation，再由专门组件渲染；该改造涉及消息协议，不纳入免费 MVP。

## 21. 测试方案

### 21.1 单元测试

`tests/test_academic_contracts.py`：

- 拒绝空查询、过长查询和 `max_results > 10`。
- 验证年份上下界和来源枚举。
- 验证缺失字段使用 `None`。

`tests/test_academic_providers.py`：

- 使用 `httpx.MockTransport` 和固定 fixture，不访问公网。
- arXiv Atom 的标题、全部作者、Abstract、首次提交、更新时间、DOI 和版本解析。
- Semantic Scholar `externalIds` 映射。
- Crossref 精确 DOI 与模糊标题匹配。
- 429、超时、非 JSON/XML、超大响应和缺字段。
- URL 只能来自固定 Provider 域名。

`tests/test_academic_service.py`：

- arXiv 与 Semantic Scholar 同一论文去重。
- DOI、arXiv ID、ACL ID 优先级。
- 同标题不同论文不能误合并。
- 一个 Provider 失败时 `partial=true` 且仍返回其他结果。
- 全部失败时返回空列表和完整状态。
- 缓存命中不重复调用 Provider。
- Crossref 只补全最终候选。
- Abstract 中包含提示注入文字时，只作为数据返回，不影响执行逻辑。

`tests/test_academic_search_tool.py`：

- 工具 Schema 正确。
- 返回合法 JSON。
- Descriptor 同时允许 Coordinator 和 Worker。
- 未授权 Worker 不能获得工具。
- 工具不触发付费搜索扣费。

### 21.2 Prompt 路由测试

更新 `tests/test_worker_profiles.py` 或新增 `tests/test_academic_routing.py`：

- “Transformer 是哪篇论文提出的”必须调用 `academic_search`。
- “解释 LoRA 原理并给出原论文”必须调用。
- “比较 PPO 和 DPO 的理论差异”必须调用。
- “Python 列表如何排序”不调用。
- 指定普通 URL 的总结仍走 `web_reader`。
- 最新新闻仍走 `web_researcher`。
- 学术问题不会默认启动额外 Worker。

### 21.3 前端测试

在 `MarkdownContent.test.tsx` 增加：

- `https://arxiv.org/abs/1706.03762` 可点击。
- `https://doi.org/...` 可点击。
- `https://aclanthology.org/...` 可点击。
- Scholar 查询链接可点击且新窗口打开。
- `http://arxiv.org/...` 不可点击。
- `https://arxiv.org.evil.example/...` 不可点击。
- `https://evil.example/?next=arxiv.org` 不可点击。
- 含用户名、密码、非默认端口的 URL 不可点击。
- 原有同源链接行为不变。

### 21.4 真实接口冒烟测试

`scripts/smoke_academic_search.py` 只供开发者手动执行，不进入普通 CI：

```powershell
uv run python scripts/smoke_academic_search.py --query "Attention Is All You Need"
uv run python scripts/smoke_academic_search.py --query "LoRA Low-Rank Adaptation"
```

输出只检查：

- 至少一个免费信源成功。
- 返回结构可解析。
- 已知论文链接正确。
- 不输出任何密钥。

## 22. 推荐实施顺序

### 步骤一：契约与配置

1. 增加 `defusedxml`。
2. 增加 Academic 配置模型和 YAML。
3. 编写 contracts 测试。

完成标准：配置可加载，非法配置在启动时失败。

### 步骤二：Provider

1. 实现 arXiv。
2. 实现 Semantic Scholar。
3. 实现 Crossref。
4. 使用固定 fixture 完成解析和错误测试。

完成标准：所有测试不依赖公网且结果确定。

### 步骤三：聚合服务

1. 实现并行查询、限流和缓存。
2. 实现规范化、去重、排序和 Crossref 补全。
3. 实现部分失败状态。

完成标准：单源失败不影响其他结果；全失败不产生伪造结果。

### 步骤四：工具注册

1. 创建 LangChain 包装器。
2. 注册 ToolDescriptor。
3. 授权 Coordinator。
4. 验证工具不会走现有付费搜索扣费路径。

完成标准：Coordinator 工具集中存在 `academic_search`，无权限 Worker 不可使用。

### 步骤五：路由 Prompt

1. 新建 Coordinator 1.4。
2. 配置切换到 1.4。
3. 增加学术意图路由测试。

完成标准：专业学术问题必须检索，普通编程问题不误触发。

### 步骤六：前端链接

1. 增加严格学术域名判断。
2. 增加安全属性。
3. 补全绕过测试。

完成标准：官方论文链接可点击，伪装域名仍不可点击。

### 步骤七：整体验证

建议执行：

```powershell
uv run pytest tests/test_academic_contracts.py tests/test_academic_providers.py tests/test_academic_service.py tests/test_academic_search_tool.py tests/test_builtin_tool_registration.py tests/test_worker_profiles.py -q

Set-Location webui
npm test -- --run src/modules/student/components/MarkdownContent.test.tsx
npm run typecheck
npm run build
```

最后手动运行真实 API 冒烟测试。

## 23. 验收标准

第一阶段必须同时满足：

1. 询问 Transformer、LoRA、RoPE、DPO 等算法理论时会主动调用 `academic_search`。
2. 返回标题、作者、Abstract、预印本时间、正式出版时间和论文状态；缺失信息明确为空。
3. `Attention Is All You Need` 可返回真实的 `https://arxiv.org/abs/1706.03762`。
4. `LoRA: Low-Rank Adaptation of Large Language Models` 可返回真实的 `https://arxiv.org/abs/2106.09685`。
5. 不存在的论文或编号不会被模型自动补造。
6. 每个可点击论文链接都能在本轮工具结果中找到，或由工具依据已核验官方 ID 确定性生成。
7. Google Scholar 仅显示“在 Google Scholar 中核验”，不标记为数据来源。
8. 任一免费数据源失败时仍可返回其他来源，并显示 `partial=true`。
9. 普通学术查询不启动额外 Worker。
10. 不产生商业 API 费用，也不扣除现有付费网页搜索额度。

## 24. 上线与回滚

上线前设置功能开关：

```yaml
tools:
  academic:
    enabled: true
```

回滚步骤：

1. 设置 `tools.academic.enabled=false`。
2. 把 Coordinator Prompt 版本从 `1.4` 切回 `1.3`。
3. 保留工具代码和缓存，不删除历史数据。
4. 前端学术链接放行可以独立保留，也可以随版本回滚。

关闭功能时，Coordinator 应明确说明当前无法联网核验学术出处，不得退回到凭记忆生成论文链接。

## 25. 后续可选增强

只有在真实使用数据证明有必要时再实施：

1. `academic_researcher`：用于综述和多论文深度比较。
2. ACL Anthology 官方元数据定时同步。
3. OpenReview 数据源。
4. 官方技术报告专用检索规则。
5. PDF 正文解析及引用页码。
6. 服务端结构化 Citation 协议和前端引用卡片。
7. Redis 分布式缓存。
8. 免费额度无法满足流量后，再评估商业 SLA；不要提前购买。

## 26. 功能体量与分期交付

### 26.1 总体体量

该功能属于中等体量的跨层改造，不是只修改 Prompt 的小需求。

预计涉及：

- 新增或修改 20～30 个文件，包含测试和 fixture。
- 生产代码约 900～1,500 行。
- 测试代码和固定响应数据约 800～1,300 行。
- Prompt、配置和前端约 150～300 行。
- 达到核心需求的 P0 + P1，单名熟悉本项目的开发者约需 5～8 个工作日。
- 达到较稳定的生产状态，再增加约 3～5 个工作日完成 P2。

以上是工程估算，不是固定承诺。主要不确定性来自外部 API 响应差异、当前部署是否为多实例、配额系统是否要求记录免费检索，以及端到端模型路由评测的稳定性。

现有功能可以降低部分工作量：

- 复用 `httpx`、TTLCache、日志方式和网络错误处理模式。
- 复用 Tool Runtime 的注册、权限和审计机制。
- 复用 Prompt 版本切换机制。
- 复用 `MarkdownContent` 及其测试框架。

不能直接复用的部分：

- `web_researcher` 是 Qwen one-shot 原生搜索，不能提供确定性的论文数据契约。
- `web_fetch` 需要已知 URL，并且不支持 arXiv Atom XML 或 PDF。
- 当前系统没有跨学术信源的 ID 合并、日期语义和链接优先级。

### 26.2 P0：arXiv 单源端到端闭环

目标：以最小改动证明“学术意图 -> 免费学术 API -> 真实论文链接 -> 前端可点击”的完整链路。

预计工期：2～3 个工作日。

预计改动：10～14 个文件，约 700～1,100 行代码和测试。

实施内容：

1. 增加最小 `AcademicSearchInput`、`AcademicPaper`、`AcademicSearchResponse` 契约。
2. 增加 `AcademicToolsConfig`，P0 只启用 arXiv。
3. 增加 `defusedxml` 依赖。
4. 实现 arXiv Atom API 客户端。
5. 实现 3 秒间隔、单连接、超时、2 MB 响应限制和短期缓存。
6. 实现标题、作者、Abstract、首次提交、更新时间和 arXiv ID 解析。
7. 实现 `academic_search` LangChain 工具。
8. 在 Tool Runtime 中注册，并仅授权 Coordinator 直接调用。
9. 新建 `coordinator.v1.4.md`，增加最基本的学术路由。
10. 前端只放行严格校验后的 `https://arxiv.org` 链接。
11. 增加固定 XML fixture、Provider、工具注册、Prompt 和前端测试。
12. 增加不进入 CI 的真实 arXiv 冒烟脚本。

P0 不做：

- Semantic Scholar、Crossref 和 ACL Anthology。
- 跨源去重和正式出版信息补全。
- Google Scholar 核验按钮。
- 多实例 Redis 限流。
- PDF 阅读。

P0 验收：

1. “Transformer 是哪篇论文提出的？”会调用 `academic_search`。
2. 返回 `Attention Is All You Need` 的真实 arXiv 记录。
3. 标题、作者、摘要和时间全部来自 arXiv 响应。
4. arXiv 链接可点击，伪装域名不可点击。
5. arXiv 失败时明确返回空结果和错误状态，不回退到模型编造链接。
6. 普通新闻和指定 URL 的现有路由不受影响。

P0 的业务状态：可演示、可小流量试用，但尚未完整满足“国际综合学术检索和正式出版核验”的全部需求。

### 26.3 P1：免费多源学术检索 MVP

目标：在 P0 基础上满足用户提出的核心交付需求，形成可正式试用的免费版本。

预计增量工期：3～5 个工作日。

预计增量改动：8～12 个文件，约 900～1,500 行代码和测试。

实施内容：

1. 接入 Semantic Scholar 免费 API，免费 Key 可选。
2. 接入 Crossref public/polite API，仅补全最终候选。
3. 解析 DOI、arXiv ID、ACL ID、Venue 和正式出版日期。
4. 增加中文查询对应的英文关键词和标准缩写输入。
5. 实现 DOI -> arXiv ID -> ACL ID -> 标题与年份的去重顺序。
6. 实现预印本、正式发表、技术报告和未知状态区分。
7. 实现多源并行、部分失败状态、warnings 和来源追踪。
8. 实现官方链接选择优先级和 ACL Anthology 规范链接。
9. 为每篇论文生成 Google Scholar 标题查询链接，只用于手动核验。
10. 前端放行 DOI、ACL Anthology、Semantic Scholar 和 Scholar 的严格 HTTPS 主机集合。
11. 增加跨源去重、字段冲突、429、超时、缺少摘要和恶意 Abstract 测试。
12. 增加核心问题路由评测：Transformer、LoRA、RoPE、PPO、DPO、MoE。
13. 明确免费学术搜索不复用现有付费 `search_calls` 或 `link_pages` 计量。

P1 验收：

1. 常见 AI/NLP 论文能够同时从两个以上免费国际学术源发现或核验。
2. 输出标题、作者、摘要、预印本时间、正式出版时间、状态和官方链接。
3. 同一论文的 arXiv 与正式出版版本不会重复展示。
4. 每个可点击论文链接来自工具响应或经过官方 ID 的确定性映射。
5. Google Scholar 只显示为核验入口，不标记为数据来源。
6. 单个数据源失败时仍能返回其他来源，并显示 `partial=true`。
7. 常规查询不启动额外学术 Worker，不产生商业 API 费用。

P1 的业务状态：满足本次需求，可开始受控上线。对于低到中等访问量，这是推荐的第一版交付终点。

### 26.4 P2：生产可靠性与质量评测

目标：解决多实例、长期运行、可观测性和回答质量波动问题。

预计增量工期：3～5 个工作日。

预计增量改动：6～10 个文件，约 600～1,100 行代码和测试。

实施内容：

1. 使用现有 Redis 实现跨进程、跨实例的 arXiv 3 秒分布式限流。
2. 将搜索和论文元数据缓存升级为 Redis 共享缓存；保留内存降级方案。
3. 增加 Provider 级熔断、退避、延迟、命中率和失败率指标。
4. 为免费学术检索增加独立的零扣费使用计数，避免与付费搜索混淆。
5. 增加数据源状态、部分结果和缓存命中的结构化日志。
6. 建立学术路由评测集，统计应检索召回率和非学术误触发率。
7. 建立引用完整性评测：最终可点击链接必须能在工具结果中找到。
8. 增加多版本、同名论文、作者名称差异、日期冲突和撤回信息测试。
9. 增加正式发布前的真实接口定时冒烟任务，但不让外部网络决定普通 CI 成败。
10. 对技术报告增加组合路由：学术源未命中时才使用现有 `web_researcher`，找到官网后再使用 `web_reader`。
11. 增加功能开关、单源开关和快速回滚演练。

P2 验收：

1. 多实例部署不会违反 arXiv 全局请求间隔。
2. 单源连续故障不会拖垮整个 Coordinator 请求。
3. 可观察每个 Provider 的成功率、延迟、429 和缓存命中率。
4. 引用完整性自动评测通过率为 100%。
5. 学术问题路由达到项目设定的召回率，普通开发问题不会大量误触发。
6. 功能可以通过配置独立关闭并安全回到旧 Prompt。

P2 的业务状态：适合正式生产运行和持续维护。

### 26.5 P3：高级研究能力，可选

目标：从“论文发现和摘要整理”扩展到“全文阅读和深度研究”。

预计增量工期：5～10 个工作日；若要求复杂 PDF、表格和公式解析，工期可能继续增加。

可选内容：

1. 增加 PDF 下载、文本解析、页码定位和引用。
2. 增加 OpenReview，区分投稿、评审和录用状态。
3. 增加 ACL Anthology 官方数据定时同步。
4. 增加 `academic_researcher`，只处理综述和多论文深度比较。
5. 增加引用网络、相似论文和后续研究发现。
6. 增加服务端结构化 Citation 协议和前端引用卡片。
7. 增加论文正文级证据片段，而不只依赖 Abstract。
8. 根据实际免费限额和流量数据决定是否采购商业 SLA。

P3 不属于本次核心需求的必要范围。没有真实使用数据前，不建议提前实施。

### 26.6 推荐交付选择

推荐采用：

```text
先完成 P0 纵向闭环
    -> 验证架构和路由
    -> 紧接着完成 P1
    -> 小流量运行后依据日志决定是否进入 P2
    -> P3 按实际需求单独立项
```

不建议只交付 P0 后长期停留，因为单独依赖 arXiv 无法稳定区分预印本和正式出版信息。对于本次需求，P1 才是功能完成点，P2 是生产稳定点。
