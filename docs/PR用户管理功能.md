# feat(admin): 补齐用户管理后台能力（班级 CRUD、成员管理、用户搜索、角色/菜单/审计）

## Summary

本次改动在**不替换任何原有基础组件**（认证系统 `SameOriginSessionAuth`、RBAC 词典、MySQL 数据库、SQLAlchemy ORM 均保留）的前提下，扩展了后台用户管理体系：

- 班级：修复创建时的外键 1452 缺陷，补齐**重命名 / 删除 / 成员列表**接口与前端 UI
- 用户：新增按关键字模糊搜索
- 后台控制台：新增**角色管理、菜单管理、授权审计、班级管理**四个页面及路由
- 认证：新增一条「数据库密码登录」入口 `POST /auth/login/db`

涉及文件 24 个已修改 + 若干新增模块/页面，后端/前端合计约 **+776 / −30** 行（不含新增文件）。

## Changes

### 认证（机制未替换，仅加登录入口）
- `server/web/app.py`：`POST /api/v1/auth/login/db` —— 用 DB 用户密码登录，签发与原 `login()` 一致的 HMAC 会话令牌
- `server/web/auth.py` (+41)、`server/auth/{__init__,controller,dependencies}.py`：DB 凭证校验与依赖桥接胶水代码

### 权限 / RBAC（双层结构保留）
- `core/rbac.py` (+6)：新增 `CLASSROOM_UPDATE` / `CLASSROOM_DELETE` / `CLASSROOM_MEMBER_READ`，授予 teacher / developer
- `server/rbac/service.py` (+66)：新增 `update_classroom` / `delete_classroom` / `classroom_members`；`classroom()` 改抛 `ResourceNotFoundError`（删后查询 500 → 404）
- 新增接口：`GET /classrooms/{id}/members`、`PATCH /classrooms/{id}`、`DELETE /classrooms/{id}`
- 运维动作：已将三个新权限种入 `nlp_permissions` + `nlp_role_permissions` + `nlp_role_permission_scopes`（scope=classroom）

### 用户管理
- `server/user/{controller,service}.py`：`GET /users` 支持 `keyword` 参数，按 `username` / `display_name` 模糊匹配

### 班级管理
- `server/rbac/service.py`：修复 `create_classroom` 在插父表后缺 `flush()`，导致成员子表先插触发外键 1452
- `server/web/contracts.py` (+4)：新增 `UpdateClassroomBody`
- `server/infrastructure/mysql/models.py` (+112，纯新增)：`ClassModel` / `ClassEnrollmentModel` / `ClassTeacherModel` / `ClassJoinRequestModel`
- 新增模块 `server/classroom_join/`（api / schemas / service）
- 前端 `ClassroomManagementPage.tsx`（新增）：列表 → 成员管理子视图、工作区下拉新建、成员增删/启停、重命名、删除（二次确认）

### 后台控制台（新增 4 页面 + 路由）
- `RoleManagementPage` / `MenuManagementPage` / `AuditLogPage` / `ClassroomManagementPage`
- `routes.tsx` (+39) 新路由；`AdminLayout.tsx` (+8) 导航
- 前端 `api.ts` (+86) / `types/index.ts` (+100)：配套接口与方法、类型（`RoleCatalogItem`、`PermissionCatalogItem`、`ClassroomItem`、`ClassroomMemberItem` 等）

### 数据库 / 网关
- 新增迁移 `migrations/versions/20260812_17_class_join_requests.py`（班级加入申请表）
- `gateway/mysql_repository.py` (+25)：新增 `list_questions`（教师分析用 workspace 级问答查询）
- 引擎 / 连接配置（`NLP_AGENT_DATABASE_URL`）未变更

### 角色工作区联动
- `DeveloperWorkspace` / `TeacherWorkspace` / `StudentWorkspace` / `student/SettingsDialog` 小幅接入新能力

## Bug Fixes

- **班级创建 500（外键 1452）**：`create_classroom` 把班级与成员在同一 `flush` 中提交，成员以字符串 `classroom.id` 引用父表，SQLAlchemy 无法推断依赖顺序，可能先插子表。修复：父表 `add` 后加 `await session.flush()`。
- **删除后查询 500**：`classroom()` 原抛裸 `KeyError`，改为 `ResourceNotFoundError`（映射 404）。

## Testing / Verification

- [x] 后端重启监听 8765，`import server.web.app` 正常
- [x] 班级端到端（developer01）：创建 201 → 改名 200 → 列成员 200 → 删除 204 → 删后查询 404
- [x] 用户搜索：`GET /users?keyword=teacher` 返回模糊过滤结果
- [x] 前端 `npm run typecheck` 退出码 0

## Risks / Notes

- **`Class` ↔ `Classroom` 命名分裂**：ORM 模型叫 `Class*`，接口/权限叫 `classroom*`，原项目既有问题，非本次引入，扩展时需注意。
- **删除为硬删除**：班级删除直接落库，成员由 `ON DELETE CASCADE` 级联清理；如需归档/软删除状态可再补（模型已有 `status` 字段但未用于删除流程）。
- **临时脚本未清理**：根目录 `check_db.py`、`create_test_users.py`、`diag_auth.py`、`verify_rbac_permissions.py`、`cookies.txt` 等属调试辅助文件，建议移入 `scripts/` 或清理。
- **遗留**：admin 别名未传播到强制层，`GET /api/v1/classes` 对 developer 仍 403（早期遗留，本次未动）。

## Checklist

- [x] 改动仅扩展，未替换原有认证/RBAC/DB/ORM
- [x] 新权限已种入 DB 三表
- [x] 前端 typecheck 通过
- [x] 关键接口已端到端验证
- [ ] 临时调试脚本清理（建议合并前处理）
