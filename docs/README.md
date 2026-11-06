# 文档导航

优先查阅下列运行与维护指南。`specs/` 中的实施方案和带日期的运维记录反映各自基线，不能直接当作当前配置或最新验收结果。

## 当前指南

| 主题 | 文档 |
| --- | --- |
| 安装、贡献与发布 | [项目首页](../README.zh.md)、[贡献指南](../CONTRIBUTING.zh.md)、[CI/CD](ci-cd.md) |
| 数据库与升级 | [迁移约定及切换要求](migrations.md) |
| Gateway 与 API | [后端 Gateway](backend-gateway.md)、[Web API](web-api.md)、[Redis Worker 部署](redis-worker-deployment.md) |
| 模型、上下文与记忆 | [模型运行时](model-runtime.md)、[会话与上下文](session-context.md)、[记忆运行时](memory-runtime.md) |
| 工具与图片 | [工具运行时](tool-runtime.md)、[可靠性](tool-reliability.md)、[图片理解](image-understanding.md)、[学术检索运维](academic-search-operations.md) |
| 教学与用户权限 | [教师模式](teacher-mode.md)、[用户管理与 RBAC](user-management-rbac-architecture.md)、[验证码](captcha-implementation.md) |
| 监控与日志 | [开发者平台](developer-platform.md)、[可观测性](observability.md)、[日志](logging.md) |
| 额度运营 | [Phase 4 运营与恢复](specs/quota-phase4-operations.md) |
| 沙箱文件与扩展 | [个人文件桥接](operations/sandbox-files-bridge.md)、[可选扩展边界](sandbox-phase5.md) |
| 评测 | [评测系统](evaluation-system.md)、[评测运行入口](../evaluation/README.md) |

## 设计与历史证据

- [架构决策](adr/)记录蓝图快照、服务端分配及主题生命周期的设计约束。
- [实施规格](specs/)保留接口契约、设计依据及尚未完成的验收项；是否已经实施需要结合当前代码判断。
- [运维记录](operations/)与[沙箱容量记录](test-sandbox-capacity-20261025.md)保留指定版本的部署、故障和容量证据，不代表后续版本也已验收。
- [邮箱注册规则](operations/email-registration-20261018.md)明确取代 T06 规格中的手机号自注册方案；历史账号、手机号字段及短信历史仍需兼容。
- [T07 集成交付记录](specs/t07-develop-pr-verification.md)、[复审记录](specs/t07-review-fix-verification.md)及其基线对照属于不同历史提交，不能互相替代测试结果，也不能据此认定扩容已通过验收。
- [Model Runtime P0 规格](model_runtime_p0_specs.md)仍被模型运行时指南引用，保留供应商错误码依据和协议约束。

维护时优先更新当前指南。已完成且内容已被指南与自动化测试覆盖的一次性计划、手动验证脚本可以删除；迁移、兼容代码、未完成验收及故障证据需先核实用途。
