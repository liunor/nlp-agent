# 文档导航

这里保留当前架构、配置、接口和运维指南。已完成的实施计划、阶段交接稿和逐轮测试流水账不再单独维护；需要追溯旧方案或实验结果时查 Git 历史。

| 主题 | 文档 |
| --- | --- |
| 安装、贡献与发布 | [项目首页](../README.zh.md)、[贡献指南](../CONTRIBUTING.zh.md)、[CI/CD 与部署恢复](ci-cd.md) |
| 数据库与升级 | [迁移约定及切换要求](migrations.md) |
| Gateway 与 API | [后端 Gateway](backend-gateway.md)、[Web API 与注册规则](web-api.md) |
| Worker 与容量 | [Redis Worker、Outbox、扩容和验收边界](redis-worker-deployment.md)、[负载测试](../tests/load/README.md) |
| 模型与额度 | [模型运行时、思考档位和计量接口](model-runtime.md)、[额度运营与恢复](quota-operations.md) |
| 上下文与记忆 | [会话与上下文](session-context.md)、[记忆运行时](memory-runtime.md) |
| 工具与图片 | [工具运行时](tool-runtime.md)、[可靠性](tool-reliability.md)、[图片理解](image-understanding.md)、[学术检索运维](academic-search-operations.md) |
| 教学与权限 | [领域术语](../CONTEXT.md)、[教师模式与班级申请](teacher-mode.md)、[用户管理与 RBAC](user-management-rbac-architecture.md)、[验证码](captcha-implementation.md) |
| 监控与日志 | [开发者平台](developer-platform.md)、[可观测性与指标口径](observability.md)、[日志](logging.md) |
| 沙箱文件与扩展 | [个人文件桥接](operations/sandbox-files-bridge.md)、[可选扩展边界](sandbox-phase5.md) |
| 评测 | [评测系统](evaluation-system.md)、[评测运行入口](../evaluation/README.md) |
| 架构决策 | [蓝图快照](adr/0001-blueprint-snapshots.md)、[服务端分配蓝图](adr/0002-server-assigned-blueprints.md)、[主题生命周期](adr/0003-topic-lifecycle.md) |

运行时 Prompt 位于 `core/prompt_runtime/templates/`，属于实际加载的程序资源；第三方许可、贡献说明和发布说明也应按用途维护，不能仅按 Markdown 后缀清理。
