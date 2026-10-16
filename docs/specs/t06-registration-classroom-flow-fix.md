# T06 注册、入班与权限变更流程修复实施规格

> 状态：可执行
>
> 适用仓库：`Pro_NLP`
>
> 目标读者：负责实现、测试和交付的开发 Agent
>
> 最后核对日期：2026-10-16

## 1. 执行指令

本文件不是讨论稿。执行 Agent 应按本文确定的产品流程、测试顺序和验收标准实施 T06；不要在执行过程中重新发明注册或入班流程。

开始前必须：

1. 阅读仓库根目录 `CONTEXT.md`、`docs/user-management-rbac-architecture.md` 和本文件。
2. 执行 `git status --short`，保留所有既有改动。当前工作树可能不干净，不得覆盖、还原或重排无关改动。
3. 先写失败测试，再改实现。至少覆盖第 8 节列出的测试。
4. 数据库结构变化只能通过新的 Alembic migration 完成；不得修改旧 migration，不得在应用启动时建表或改表。
5. 后端权限判断是唯一安全边界；前端角色判断只用于界面显示。
6. 不得通过削弱会话失效、RBAC、CSRF、Origin 检查来消除 401。

## 2. 已确定的产品决策

以下决策不再留给执行 Agent选择：

1. **手机号自注册成功的新账号初始角色为 `guest`，而不是 `student`。**
2. `guest` 角色继续保留，并用于公开注册、受控访客账号、演示账号和管理员明确创建的临时账号；公开注册账号只有在入班审批通过后才转为 `student`。
3. 注册成功后不自动登录。页面显示“注册成功，请登录”，用户用手机号和密码完成首次登录。
4. 注册后的 guest 只能提交和查询自己的入班申请，不能访问正式课堂学习内容。
5. `develop` 现有邮箱验证码注册入口保持兼容；邮箱和手机号自注册均返回 guest、不建立会话，并遵循相同的入班权限边界。手机号注册是本规格的主要新用户流程。
5. guest 通过一个不区分大小写的短班级码申请入班，不填写数据库 UUID，也不公开浏览全部班级。
6. 入班采用“guest 申请 → 教师/有权限的管理员审核 → guest 转 student 并加入班级”的流程。
7. 入班审批是**角色和权限增加**：审批成功后在同一事务中完成 guest→student、班级成员关系和授权版本更新，并保留该账号当前有效登录会话；已连接 WebSocket 可以被关闭并自动重连，以重新加载权限范围。
8. 角色替换、账号禁用、密码修改、成员移除等可能降权的操作仍必须使旧会话失效。
9. 因角色变化等安全原因失效时，后端返回机器可读错误码 `authorization_changed`，前端清理登录态并显示“账号权限已更新，请重新登录”，不得只显示裸 `HTTP 401`。

## 3. 当前缺陷基线

执行 Agent 应先用测试固定以下现状，再开始修复：

1. `POST /api/v1/auth/register` 调用 `UserService.create_user()`，当前默认分配 `guest`，但注册流程和验收口径曾被错误改成 `student`。
2. `guest` 不包含正式课堂学习权限；入班申请需要独立的 `classroom:join_request:submit` 自有资源权限，否则自注册用户无法提交或查询申请。
3. 后端已有按 `classroom_id` 提交、审批、拒绝和查询申请的接口，但没有面向学生的班级码入口。
4. 前端只有管理员侧申请审批页，没有学生侧申请和状态查询界面。
5. `replace_classroom_member()` 会递增 `authorization_version`，导致审批后的学生旧会话立即 401。
6. 所有认证失败当前统一返回 `authentication_required`，前端普通请求没有全局处理运行期间出现的 401。
7. `server/user/service.py` 中还存在另一条会额外分配 `student` 的注册实现；必须消除两套注册规则不一致的问题。

## 4. 目标用户流程

### 4.1 注册与首次登录

1. 用户填写手机号、短信验证码、密码和显示名称。
2. 后端在一个事务中创建用户、个人工作区、工作区 owner 成员关系，并且只分配 `guest` 初始角色。
3. 注册接口返回 `201` 和基础账号信息，不返回 Cookie 或会话令牌。
4. 前端切换到登录表单，并显示持久到下一次提交前的成功提示“注册成功，请使用手机号登录”。
5. 用户登录后 `/api/v1/auth/session` 返回 `guest` 角色、入班申请权限，但不返回正式课堂学习权限。

失败行为：

- 验证码错误或过期：`400`，不创建任何用户、角色或工作区记录。
- 手机号重复：`409`，不产生部分记录。
- guest 内置角色不可用：注册整体回滚，返回稳定的服务错误，不创建无角色用户。

### 4.2 guest 申请入班

1. guest 登录后打开“设置 → 我的班级”。
2. 输入教师提供的短班级码，可选填学号。
3. 后端将班级码规范化为大写并解析到一个 active 班级。
4. 创建 `pending` 申请并返回班级名称、申请时间和状态。
5. 页面展示“待审核”；重复提交同一班级的 pending 申请返回 `409` 和可读提示。
6. guest 只能查询自己的申请，不能枚举其他学生或全部班级；guest 不能读取正式课堂学习内容。

### 4.3 教师创建与审核

1. 普通教师从 `/teacher/classrooms` 进入班级管理；管理员/开发者仍可从 `/admin/classrooms` 进入同一组件。前端不得只把该能力挂在管理员路由下。
2. 教师可以选择有 `classroom:classroom:create` 权限的工作区，填写班级名称创建班级；后端生成唯一的 8 位班级码并在响应中返回。创建者自动成为该班级教师成员。
3. 教师在班级管理页查看班级码、复制班级码和 pending 申请。
4. 通过申请时，在同一数据库事务中：
   - 锁定申请；
   - 将申请置为 `approved`；
   - 将申请人唯一的 `guest` 角色替换为 `student`；
   - 新增或激活 `nlp_classroom_members`；
   - 递增用户 `authorization_version`；
   - 同步该用户所有仍有效、未撤销会话的 `authorization_version` 到新版本；
   - 撤销旧 sandbox lease；
   - 写授权审计记录和 `authorization.changed` outbox。
5. 申请人后续 HTTP 请求保持登录并读取到最新班级范围和 student 权限；WebSocket 重连收到 `connection.ready` 后必须再次请求 `/auth/session`，让前端 AuthContext 和学生工作区立即得到新角色/权限，不要求手工刷新页面。
6. 拒绝申请不改变角色、班级成员关系或会话版本；申请人仍保持 `guest`。
7. 同一班级/用户只允许一条当前 `pending` 申请。申请被拒绝后可以再次申请并再次审批；已经是 active 班级成员的用户再次申请返回 `409 class_join_member_exists`。

### 4.4 降权或角色变化

1. 管理员替换角色、禁用账号、修改密码或移除班级成员时，继续递增 `authorization_version`，但**不得**同步旧 Session 版本。
2. 旧 Session 下一次请求返回：

```json
{
  "status": 401,
  "code": "authorization_changed",
  "title": "Authorization changed",
  "detail": "账号权限已更新，请重新登录"
}
```

3. 前端收到该错误后只处理一次：清空内存登录态和 CSRF token，关闭实时连接，打开登录界面并显示上述提示。
4. 登录接口本身发生 401 时仍显示“手机号或密码错误”，不得错误标记为权限变化。

## 5. 数据模型与迁移

### 5.1 班级码

为 `nlp_classrooms` 新增：

- `join_code VARCHAR(12) NOT NULL`
- 唯一索引 `uq_nlp_classrooms_join_code`

规则：

- 新建班级时生成 8 位大写 Base32 风格码，只使用 `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`，排除易混淆字符 `0/O/1/I`。
- 使用 `secrets` 生成，不得使用可预测的递增数字或截断 UUID。
- 发生唯一键冲突时最多重试 5 次，仍失败则返回服务错误。
- API 接收时执行 `strip().upper()`。
- migration 先添加 nullable 列，为既有班级逐行生成且在 migration 内检查不重复，再改为 non-null 并添加唯一索引。
- 不在本 T06 中实现班级码轮换；这是后续独立需求。

建议 migration 文件名：

```text
migrations/versions/<revision>_t06_classroom_join_code.py
```

### 5.2 初始角色与审批升级

不要在注册完成后追加一个 `student`，形成 `guest + student` 双角色。公开注册必须只分配 `guest`；审批时再原子地替换为 `student`。应将用户创建服务改成显式接收初始角色：

```python
async def create_user(
    self,
    data: UserCreate,
    *,
    actor_user_id: str | None = None,
    initial_role_code: str = DEFAULT_USER_ROLE,
) -> UserModel:
    ...
```

- 管理员现有创建流程如未传值，保持当前默认行为。
- 公开 `/auth/register` 必须显式传 `initial_role_code="guest"`（或使用 `DEFAULT_USER_ROLE`，但测试必须固定结果）。
- 审批服务必须在同一事务中完成 guest→student、成员关系、版本更新和审计写入；不得先提交申请再异步补角色。
- `server/user/service.py` 中另一条注册实现必须改为复用同一条注册编排，或删除重复的角色追加逻辑；最终只能有一个决定自注册初始角色的入口。

### 5.3 guest 入班申请权限

新增内置权限 `classroom:join_request:submit`，范围固定为 `own`，授予 `guest`、`student`、`teacher` 和 `developer`。该权限只允许当前用户创建申请和读取自己的申请，不授予正式课堂内容、课堂进度或课堂成员管理权限。

## 6. 后端实施清单

### 6.1 注册

主要文件：

- `server/web/app.py`
- `server/user/service.py`
- `server/user/schemas.py`
- `tests/api_http/auth/test_registration_core.py`
- `tests/test_user_management_v3.py`

任务：

1. 为 `create_user()` 增加显式 `initial_role_code`。
2. `/api/v1/auth/register` 传入 `guest`。
3. 清理重复注册服务中的额外 student 分配，确保公开注册只有一条 guest 角色关联。
4. 注册响应加入 `roles: ["guest"]`，便于 HTTP 测试验证；仍不创建登录会话。
5. 注册写入必须保留数据库手机号/用户名唯一约束；并发冲突捕获为 409，不得将其他 IntegrityError 误判为重复注册。
5. 保证用户、工作区、owner membership 和角色分配同事务提交或整体回滚。

### 6.2 班级码与学生申请 API

主要文件：

- `server/infrastructure/mysql/models.py`
- 新 Alembic migration
- `server/classroom_join/schemas.py`
- `server/classroom_join/service.py`
- `server/classroom_join/api.py`
- `server/rbac/service.py`
- `tests/api_http/classrooms/test_classroom_join_core.py`
- `tests/api_http/security/test_rbac_idor_core.py`

新增公开给学生使用的接口：

```http
POST /api/v1/classrooms/join-requests
Content-Type: application/json

{
  "join_code": "ABCD2345",
  "student_number": "20260001"
}
```

响应继续使用现有 `JoinRequestResponse`，成功为 `201`。

保留现有按 `/{classroom_id}/join-requests` 的内部兼容接口，但学生前端不得依赖 UUID 接口。两个入口必须调用同一个 service，避免两套申请规则。

教师获取班级码：

```http
GET /api/v1/classrooms/{classroom_id}/join-code
```

要求 `classroom:member:manage` 的资源级权限。响应：

```json
{
  "classroom_id": "...",
  "classroom_name": "自然语言处理一班",
  "join_code": "ABCD2345"
}
```

安全要求：

- 无效班级码统一返回 `404 class_join_code_not_found`，不泄露禁用班级信息。
- guest 可用 `classroom:join_request:submit` 提交和查询自己的申请；guest 仍不能访问正式课堂学习权限。teacher/developer 按实际 permission 判断，不按角色字符串硬编码。
- 所有写请求继续经过 `WriteClaims`、Origin 和 CSRF 检查。
- 并发重复申请由只针对当前 `pending` 的数据库唯一键兜底，并统一映射成 `409 class_join_request_pending`，不得把 `IntegrityError` 暴露为 500。已处理历史记录不得阻止重新申请；其他数据库错误不得误判为重复申请。

### 6.3 审批后保留会话

主要文件：

- `server/rbac/service.py`
- `server/infrastructure/mysql/models.py`
- `server/web/database_auth.py`
- `tests/api_http/classrooms/test_classroom_join_core.py`
- `tests/api_http/auth/test_session_lifecycle_core.py`

实现一个名称表达清楚的私有服务方法，例如：

```python
async def _advance_authorization_version(
    session: AsyncSession,
    *,
    user: UserModel,
    preserve_active_sessions: bool,
) -> int:
    ...
```

约束：

- 只有“批准加入 active 班级”调用 `preserve_active_sessions=True`。
- 角色替换、成员禁用/移除、账号状态变化、密码变化必须传 `False` 或使用默认安全路径。
- 同步 Session 时必须限定：相同 `user_id`、`revoked_at IS NULL`、`expires_at > UTC_TIMESTAMP(6)`、Session 版本等于更新前版本。
- 不得恢复已撤销或已过期 Session。
- 用户版本、Session 版本、成员关系、申请状态和审计记录必须在同一事务中提交。
- 保留 sandbox lease 撤销和授权变更 outbox；不得为了不断线删除这些安全行为。

修改现有 HTTP 测试：审批后旧学生客户端访问 `/api/v1/users/me` 应从 `401` 改为 `200`，并能读取到新班级范围。

### 6.4 可区分的认证错误

主要文件：

- `server/web/auth.py`
- `server/web/database_auth.py`
- `server/web/app.py`
- `tests/api_http/auth/test_session_lifecycle_core.py`

新增 `AuthorizationChangedError(AuthenticationError)`，仅在 Session 与 User 的 `authorization_version` 不一致时抛出。异常处理器返回 `401 authorization_changed`。

其他情况继续使用不同的稳定码：

- 缺少或无效 Cookie：`authentication_required`
- 自然过期或空闲超时：`session_expired`
- 权限版本不一致：`authorization_changed`

不要把异常原始字符串直接返回给客户端；使用固定的中文 `detail` 和英文机器码。

## 7. 前端实施清单

### 7.1 API 与类型

主要文件：

- `webui/src/platform/http/api.ts`
- `webui/src/shared/types/index.ts`

新增：

```ts
submitClassroomJoinRequest(input: { join_code: string; student_number?: string }): Promise<JoinRequest>
listMyJoinRequests(): Promise<JoinRequestListResponse>
getClassroomJoinCode(classroomId: string): Promise<ClassroomJoinCodeResponse>
```

`request()` 收到 401 时仍抛出 `ApiError`，同时对非登录请求发布一次全局认证事件，事件 detail 至少包含 `code` 和 `message`。必须防止多个并发 401 重复弹出多个登录框。

### 7.2 学生“我的班级”界面

主要文件：

- `webui/src/modules/student/components/SettingsDialog.tsx`
- 新建 `webui/src/modules/student/components/ClassroomSettingsSection.tsx`
- 相应 Vitest 文件

要求：

1. 在 `SettingsSection` 中增加 `classroom`，菜单文字为“我的班级”。
2. 仅登录用户显示；具备 `classroom:join_request:submit` 时允许 guest/student 提交和查询自己的申请。
3. 表单字段：班级码、可选学号、提交按钮。
4. 展示当前用户全部申请，状态中文映射为：待审核、已加入、未通过。
5. 成功提交后立即刷新列表并显示班级名称。
6. `409 class_join_request_pending` 显示“已申请该班级，请等待审核”。
7. 无效班级码显示“班级码不存在或班级不可加入”。
8. 不将 `classroom_id` 或原始 HTTP 错误作为主要用户文案。

### 7.3 教师班级管理界面

主要文件：

- `webui/src/modules/admin/ClassroomManagementPage.tsx`
- 相应 Vitest 文件

要求：

1. 普通 teacher 必须能从 `/teacher/classrooms` 导航进入；管理员/开发者可继续使用 `/admin/classrooms`。
2. 提供班级名称、工作区和“创建班级”表单；创建成功显示班级码并自动选中新班级。
3. 选择班级后显示班级码和“复制班级码”按钮，复制测试必须断言写入准确班级码。
4. 继续支持待审核申请的通过/拒绝。
5. 审批成功后从 pending 列表移除，并显示成功提示。
6. 快速切换班级时，旧请求不得覆盖当前班级的班级码或申请列表。
7. 不在本任务增加班级码轮换按钮。

### 7.4 401 用户体验

主要文件：

- `webui/src/platform/http/api.ts`
- `webui/src/platform/auth/AuthContext.tsx`
- `webui/src/modules/student/components/LoginDialog.tsx`
- `webui/src/modules/auth/LoginPage.tsx`
- 相应测试文件

要求：

- `authorization_changed`：清空 auth state 和 CSRF token，显示“账号权限已更新，请重新登录”；AuthGate 跳转 `/login` 时不得丢弃该提示。
- `session_expired`：显示“登录已过期，请重新登录”。
- `authentication_required`：静默进入未登录状态；如果来源是用户刚执行的受保护操作，可显示“请先登录”。
- 事件处理必须幂等；同一 code 在短时间内去重，`authorization_changed` 优先于 `session_expired`，`session_expired` 优先于 `authentication_required`，不同 code 必须允许覆盖或升级提示；10 个并发 401 只能产生一次状态清理和一个登录提示。
- AuthContext 保存 `authNotice`；AuthGate 跳转 `/login` 时通过上下文/路由状态保留提示，LoginPage 必须显示提示。
- 登录成功或用户主动切换登录/注册页面时清除旧提示并重新 bootstrap 学生工作区。
- 学生实时连接每次收到 `connection.ready`（包括权限变更后的自动重连）都必须刷新 `/auth/session`，同步 AuthContext 的角色和权限。

### 7.5 注册成功提示

两个现有注册入口都必须同步修改：

- `webui/src/modules/student/components/LoginDialog.tsx`
- `webui/src/modules/auth/LoginPage.tsx`

注册成功后切换到登录页并显示“注册成功，请使用手机号登录”。不得只切换标签而无反馈。
该提示保留到下一次登录表单提交开始时；无论登录成功还是失败，下一次提交都应先清理旧注册提示。

## 8. 测试优先顺序

### 8.1 第一组：先写成红色

在实现前增加或修改以下测试，并确认它们因当前缺陷失败：

1. 自注册账号只有 `guest`，没有 `student`。
2. 注册后的新账号登录后拥有 `classroom:join_request:submit`，没有正式课堂学习权限。
3. 新注册 guest 可用班级码提交 pending 申请并查询自己的申请。
4. guest 不能直接访问正式课堂学习内容。
5. 无效或禁用班级码返回稳定 404 code。
6. 并发重复申请只有一条成功，另一条返回稳定 409。
7. 审批后成员关系和申请状态同时提交。
8. 审批前的 guest Session 在审批后仍返回 200，并看到新班级范围。
9. 管理员执行降权型角色替换、禁用账号或修改密码后，旧 Session 返回 401，code 为 `authorization_changed`；审批导致的 guest→student 升权是明确例外，旧 Session 保持可用。
10. 拒绝申请不改变 Session 版本。
11. 事务中任何一步失败时，申请、成员关系、版本和审计全部回滚。

### 8.2 后端建议命令

Docker Desktop 已启动时运行真实 HTTP 套件：

```powershell
$env:RUN_API_HTTP='1'
uv run pytest tests\api_http\auth\test_registration_core.py tests\api_http\auth\test_session_lifecycle_core.py tests\api_http\classrooms\test_classroom_join_core.py tests\api_http\security\test_rbac_idor_core.py -q
```

运行相关单元测试：

```powershell
uv run pytest tests\test_user_management_v3.py tests\test_rbac_authorization.py tests\test_rbac_route_guards.py -q
```

运行后端完整回归：

```powershell
uv run pytest -q
```

如果 Docker 不可用，不得把真实 HTTP 测试标记为通过；应明确报告阻塞原因。

### 8.3 前端测试

至少增加：

- 注册成功提示测试，两套注册 UI 都要覆盖。
- 学生提交申请、重复申请、状态列表测试。
- 教师查看和复制班级码测试。
- 普通 teacher 路由进入班级管理、创建班级并获得班级码测试。
- 教师批准/拒绝刷新测试。
- 快速切换班级不串数据测试。
- 已加入用户重复申请、拒绝后再次申请测试。
- `authorization_changed`、`session_expired` 全局处理测试。
- 多个并发 401 只触发一次提示的测试。

命令：

```powershell
Set-Location webui
npm test -- --run
npm run typecheck
npm run build
```

## 9. 实施顺序与建议提交

按以下顺序提交，保证每个提交可独立审查：

1. `test(t06): lock registration and classroom flow failures`
2. `feat(auth): keep public registrations in guest role`
3. `feat(classroom): add secure classroom join codes`
4. `fix(auth): preserve sessions for additive classroom approval`
5. `feat(web): add student classroom application flow`
6. `fix(web): explain authorization-driven session invalidation`
7. `test(t06): cover end-to-end registration and enrollment`

不要为了凑提交数量拆散同一数据库事务的代码和测试。如果仓库交付规范要求更少提交，可以合并相邻项，但不得改变依赖顺序。

## 10. 验收矩阵

| 场景 | 预期结果 |
|---|---|
| 新手机号注册 | 201；用户、个人工作区、owner membership、单一 guest 角色同时存在 |
| 重复手机号注册 | 409；数据库无新增残留 |
| 新用户首次登录 | 200；角色为 guest，具备入班申请权限但没有正式课堂学习权限 |
| guest 使用有效班级码申请 | 201 pending；只看到自己的申请 |
| guest 访问正式课堂内容 | 403 |
| 无效/禁用班级码 | 404 `class_join_code_not_found` |
| 同一学生重复 pending | 409 `class_join_request_pending` |
| 教师批准 | 200 approved；guest→student；成员 active；审计存在 |
| 批准后的旧 HTTP Session | 继续 200，不要求重新登录，并读取到 student 权限 |
| 批准后的自动重连 | WebSocket 重连后刷新 `/auth/session`；前端立即显示 student 功能 |
| 批准后的旧 WebSocket | 可断开并恢复；持久事件不丢失 |
| 教师拒绝 | 200 rejected；无成员关系；学生 Session 不变 |
| 拒绝后再次申请 | 新建 pending 成功；可再次拒绝或批准 |
| 已加入用户再次申请 | 409 `class_join_member_exists`；不产生 pending |
| 普通教师进入管理页 | `/teacher/classrooms` 可达；可创建班级并显示唯一班级码 |
| 管理员替换角色 | 旧 Session 401 `authorization_changed` |
| 前端收到权限变化 401 | 只弹一次明确提示并进入登录状态 |
| 任意审批事务步骤失败 | 所有审批相关写入整体回滚 |

## 11. 发布与回退

### 11.1 发布前

1. 备份 MySQL。
2. 在隔离环境运行 migration 并核对所有既有班级均有唯一班级码。
3. 使用一个全新手机号完整走通：注册、登录、申请、审批、审批后继续使用。
4. 使用一个既有用户验证角色替换后收到明确重新登录提示。
5. 确认日志和响应中不包含验证码、密码、Cookie、CSRF token。

### 11.2 监控

发布后至少观察：

- `/api/v1/auth/register` 的 2xx/4xx/5xx 数量；
- 401 按 `code` 分类的数量；
- 入班申请 201/409/404 数量；
- 审批成功与事务回滚数量；
- WebSocket 因 `authorization.changed` 关闭和恢复的数量。

不得记录原始手机号、班级码、Session token 或验证码；日志使用 request ID、user ID 和 classroom ID。

### 11.3 回退原则

- 应用回退时不要立即删除 `join_code` 列；旧版本会忽略该列。
- 如果新学生界面需要紧急关闭，可通过前端发布回退；后端兼容接口继续保留。
- 不得通过绕过审批直接给自注册账号 student 作为临时止血；guest→student 必须和成员关系、授权版本在同一事务中完成。
- 不得通过关闭 `authorization_version` 检查止血。

## 12. 完成定义

只有同时满足以下条件，T06 才可标记完成：

- [ ] 自注册角色规则只有一套，并且结果是单一 guest。
- [ ] guest 可以用短班级码完成申请并查看状态，但不能读取正式课堂内容。
- [ ] 教师可以查看班级码并审批/拒绝。
- [ ] 普通 teacher 可从教师工作台进入班级管理并创建班级。
- [ ] 当前 pending 申请唯一，拒绝后可重新申请，已加入用户被明确拒绝。
- [ ] 审批入班不会让学生现有有效 Session 变成 401。
- [ ] 降权和角色变化仍会安全地使旧 Session 失效。
- [ ] 权限变化导致的 401 有稳定错误码和明确中文提示。
- [ ] 后端单元测试、真实 HTTP 测试、前端测试、类型检查和构建全部通过。
- [ ] migration 在包含既有班级数据的数据库上升级成功。
- [ ] 手工完成一次全新账号端到端验收。
- [ ] 没有遗留调试日志、临时脚本或绕过权限的代码。
- [ ] 交付说明列明改动、测试结果、尚未解决的问题和回退方法。
