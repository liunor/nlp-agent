# Nova 用户管理与 RBAC 权限系统架构说明

## 一、核心概念与关系图

### 1.1 实体关系总览

```
┌─────────────────────────────────────────────────────────────┐
│                    NOVA 用户管理架构                         │
─────────────────────────────────────────────────────────────┘

User (用户账号)
├── id, username, password_hash, display_name
├── status: active/disabled/locked
├── deleted_at: 软删除标记
└── Relationships:
    ├── UserRoleModel → RoleModel (多对多，通过中间表)
    ├── WorkspaceMemberModel → WorkspaceModel (多对多，通过中间表)
    ├── SessionModel (一对多，登录会话)
    └── ClassroomMemberModel → ClassroomModel (多对多，通过中间表)

Role (角色)
── id, code, name, description
├── is_builtin: 是否内置角色（guest/student/teacher/developer）
└── Relationships:
    ├── UserRoleModel → UserModel (多对多)
    ├── RolePermissionModel → PermissionModel (多对多)
    └── RoleMenuModel → MenuModel (多对多，前端菜单可见性)

Permission (权限)
├── id, code, domain_name, resource_name, action_name
├── 例：system:user:manage, learning:exercise:submit
└── Relationships:
    ── RolePermissionModel → RoleModel (多对多)

Workspace (工作空间)
├── id, slug, name
├── 每个用户注册时自动创建一个个人工作空间
└── Relationships:
    ├── WorkspaceMemberModel → UserModel (多对多)
    ── ClassroomModel (一对一，班级绑定到工作空间)

Classroom (班级)
├── id, name, workspace_id
├── 教师创建的虚拟组织单元
└── Relationships:
    └── ClassroomMemberModel → UserModel (多对多)
```

### 1.2 关键设计原则

1. **用户 ≠ 角色**：用户拥有多个角色（如 student + teacher），角色决定权限集合
2. **用户 ≠ 工作空间**：用户可属于多个工作空间，工作空间是资源隔离边界
3. **角色 ≠ 权限**：角色是权限的集合，权限是最小授权单元
4. **软删除优先**：用户删除标记 `deleted_at`，保留历史数据完整性

---

## 二、核心表结构详解

### 2.1 用户身份层

#### `nlp_users` - 用户主表
存储用户的基本信息和状态。

**关键字段：**
- `id`: UUID，用户唯一标识
- `username`: 用户名（唯一）
- `username_lower`: 计算列，用于大小写不敏感查询（确保 "Alice" 和 "alice" 视为同一用户）
- `password_hash`: Argon2 密码哈希
- `display_name`: 显示名称
- `status`: 账号状态（active/disabled/locked）
- `authorization_version`: 权限版本号，每次修改角色/权限时递增，使旧会话自动失效
- `deleted_at`: 软删除时间戳（NULL = 活跃用户）
- `last_login_at`: 最后登录时间

#### `nlp_user_roles` - 用户角色关联表
连接用户和角色的多对多关系表。

**用途：**
- 支持一个用户拥有多个角色（如既是学生又是教师）
- 支持临时角色（通过过期时间字段）
- 审计追踪（记录谁分配的、何时分配）

---

### 2.2 RBAC 权限层

#### `nlp_roles` - 角色定义表
定义系统中的角色类型。

**内置角色：**
| 角色代码 | 描述 | 典型用途 |
|---------|------|----------|
| `guest` | 访客 | 仅查看公开内容 |
| `student` | 学生 | 学习、提交练习、查看个人进度 |
| `teacher` | 教师 | 创建班级、管理学生、查看班级进度 |
| `developer` | 开发者 | 系统配置、用户管理、监控日志 |

#### `nlp_permissions` - 权限定义表
定义最小授权单元，采用 `{domain}:{resource}:{action}` 命名规范。

**示例权限：**
- `identity:profile:read_self` - 读取自己的个人资料
- `learning:exercise:submit` - 提交练习题
- `classroom:member:manage` - 管理班级成员
- `system:user:manage` - 管理系统用户（增删改查）
- `agent:session:create` - 创建 Agent 会话

#### `nlp_role_permissions` - 角色权限关联表
连接角色和权限的多对多关系表。

**用途：**
- 定义每个角色拥有的权限集合
- 支持动态调整角色权限（无需修改代码）

---

### 2.3 工作空间与组织层

#### `nlp_workspaces` - 工作空间表
资源隔离边界，每个用户注册时自动创建一个个人工作空间。

#### `nlp_workspace_members` - 工作空间成员表
连接用户和工作空间的多对多关系表。

**用途：**
- 支持协作场景（多人共享一个工作空间）
- 当前主要用于个人工作空间（owner = 创建者）

#### `nlp_classrooms` - 班级表
教师创建的虚拟组织单元，每个班级绑定到一个工作空间（通常是教师的工作空间）。

#### `nlp_classroom_members` - 班级成员表
连接用户和班级的多对多关系表。

**用途：**
- 记录班级中的学生/教师成员
- 支持按班级查询学习进度

---

### 2.4 会话与安全层

#### `nlp_sessions` - 登录会话表
存储登录会话令牌（HTTP-only cookie），支持会话撤销和权限版本快照。

**关键字段：**
- `token_hash`: HMAC-SHA256(session_token)，用于验证会话
- `csrf_hash`: CSRF token hash，防止跨站请求伪造
- `authorization_version`: 快照用户权限版本，用于检测权限变更
- `revoked_at`: 撤销时间（非空表示已撤销）

---

## 三、RBAC 权限模型工作流程

### 3.1 登录流程

```
1. 用户输入 username/password
   ↓
2. 查询 nlp_users（通过 username_lower 匹配）
   ↓
3. 验证 password_hash（Argon2）
   ↓
4. 查询 nlp_user_roles → nlp_roles（获取用户所有角色）
   ↓
5. 查询 nlp_role_permissions → nlp_permissions（获取角色所有权限）
   ↓
6. 创建 AuthenticatedPrincipal {
       user_id, 
       roles: ["student", "teacher"],
       permissions: ["learning:exercise:submit", ...],
       workspace_ids: ["ws-xxx"]
   }
   ↓
7. 生成 session_token，存储到 nlp_sessions
   ↓
8. 返回 JWT-like claims（包含 csrf_token, expires_at）
```

### 3.2 权限检查流程

```
请求到达 → 解析 session_token → 加载 Principal
   ↓
检查权限：authorization_service.require(principal, Permission.LEARNING_EXERCISE_SUBMIT)
   ↓
1. 检查 principal.permissions 是否包含该权限
   ↓
2. 如果包含 → 允许访问
   ↓
3. 如果不包含 → 抛出 AccessDeniedError (403 Forbidden)
   ↓
4. 记录审计日志到 nlp_authorization_audit_logs
```

### 3.3 对象级权限检查（Resource Policy）

某些操作需要检查资源所有权：

```python
# 例：读取某个 Agent 会话
resource = ResourceRef(
    resource_type="agent_session",
    owner_user_id=session.owner_user_id,
    workspace_id=session.workspace_id
)

authorization_service.require_resource(
    principal, 
    Permission.AGENT_SESSION_READ, 
    resource
)
```

**检查逻辑：**
1. 首先检查角色是否有 `AGENT_SESSION_READ` 权限
2. 然后检查资源作用域：
   - `scope=own`: 要求 `resource.owner_user_id == principal.user_id`
   - `scope=workspace`: 要求 `resource.workspace_id in principal.workspace_ids`
   - `scope=classroom`: 要求 `resource.classroom_id in principal.classroom_ids`
   - `scope=public`: 允许所有人访问
   - `scope=system`: 管理员特权

---

## 四、角色权限矩阵

### 4.1 内置角色权限继承关系

```
DEVELOPER (最高权限)
├── TEACHER
│   ├── STUDENT
│   │   ├── GUEST (最低权限)
│   │   │   ├── identity:profile:read_self
│   │   │   ├── identity:profile:update_self
│   │   │   └── learning:content:read_public
│   │   │
│   │   └── + STUDENT 额外权限：
│   │       ├── agent:session:* (创建/读取/更新/删除)
│   │       ├── agent:turn:submit
│   │       ├── agent:turn:cancel
│   │       ├── agent:event:replay
│   │       ├── agent:checkpoint:restore
│   │       ├── learning:content:read_workspace
│   │       ├── learning:exercise:submit
│   │       ├── learning:progress:read_self
│   │       └── learning:feedback:submit
│   │
│   ── + TEACHER 额外权限：
│       ├── learning:content:manage (管理课程内容)
│       ├── learning:progress:read_classroom (查看班级进度)
│       ├── learning:feedback:create (创建反馈)
│       ├── classroom:classroom:create (创建班级)
│       └── classroom:member:manage (管理班级成员)
│
└── + DEVELOPER 额外权限：
    ├── learning:feedback:read (查看所有反馈)
    ├── system:model_profile:manage (管理模型配置)
    ├── system:prompt_template:manage (管理提示词模板)
    ├── system:tool_config:manage (管理工具配置)
    ├── system:runtime:monitor (监控系统运行状态)
    ├── system:runtime:inspect (检查运行时数据)
    ├── system:user:manage (管理用户账号)
    ├── system:role:manage (管理角色权限)
    ├── system:release_notes:manage (管理发布说明)
    ├── system:permission:read (查看权限列表)
    └── system:audit:read (查看审计日志)
```

### 4.2 完整权限列表

| 权限代码 | 描述 | 适用角色 |
|---------|------|---------|
| **身份管理** | | |
| `identity:profile:read_self` | 读取自己的个人资料 | guest+ |
| `identity:profile:update_self` | 更新自己的个人资料 | guest+ |
| **学习内容** | | |
| `learning:content:read_public` | 读取公开学习内容 | guest+ |
| `learning:content:read_workspace` | 读取工作空间学习内容 | student+ |
| `learning:content:manage` | 管理学习内容（CRUD） | teacher+ |
| `learning:exercise:submit` | 提交练习题答案 | student+ |
| `learning:progress:read_self` | 读取自己的学习进度 | student+ |
| `learning:progress:read_classroom` | 读取班级学习进度 | teacher+ |
| `learning:feedback:submit` | 提交学习反馈 | student+ |
| `learning:feedback:create` | 创建反馈线程 | teacher+ |
| `learning:feedback:read` | 查看所有反馈 | developer |
| **班级管理** | | |
| `classroom:classroom:create` | 创建班级 | teacher+ |
| `classroom:member:manage` | 管理班级成员 | teacher+ |
| **Agent 会话** | | |
| `agent:session:create` | 创建会话 | student+ |
| `agent:session:read` | 读取会话 | student+ |
| `agent:session:update` | 更新会话 | student+ |
| `agent:session:delete` | 删除会话 | student+ |
| `agent:turn:submit` | 提交对话轮次 | student+ |
| `agent:turn:cancel` | 取消对话轮次 | student+ |
| `agent:event:replay` | 重放事件流 | student+ |
| `agent:checkpoint:restore` | 恢复检查点 | student+ |
| **系统管理** | | |
| `system:model_profile:manage` | 管理模型配置 | developer |
| `system:prompt_template:manage` | 管理提示词模板 | developer |
| `system:tool_config:manage` | 管理工具配置 | developer |
| `system:runtime:monitor` | 监控运行时状态 | developer |
| `system:runtime:inspect` | 检查运行时数据 | developer |
| `system:user:manage` | 管理用户账号 | developer |
| `system:role:manage` | 管理角色权限 | developer |
| `system:release_notes:manage` | 管理发布说明 | developer |
| `system:permission:read` | 查看权限列表 | developer |
| `system:audit:read` | 查看审计日志 | developer |
| `system:sensitive_data:read` | 读取敏感数据 | developer |

---

## 五、用户管理与 RBAC 的关系

### 5.1 职责分离

| 模块 | 职责 | 相关表 |
|-----|------|-------|
| **用户管理** | 账号生命周期（注册、登录、密码重置、软删除） | `nlp_users`, `nlp_sessions` |
| **RBAC 权限** | 访问控制（谁能做什么） | `nlp_roles`, `nlp_permissions`, `nlp_role_permissions`, `nlp_user_roles` |
| **工作空间** | 资源隔离（数据属于哪个空间） | `nlp_workspaces`, `nlp_workspace_members` |
| **班级组织** | 教学组织（教师-学生关系） | `nlp_classrooms`, `nlp_classroom_members` |

### 5.2 交互流程示例

#### 场景 1：学生注册并登录

```
1. POST /api/v1/auth/register
   → 创建 nlp_users 记录（username = 随机生成）
   → 创建 nlp_workspaces 记录（个人工作空间）
   → 创建 nlp_workspace_members 记录（user as owner）
   → 创建 nlp_user_roles 记录（role = guest）
   
2. POST /api/v1/auth/login
   → 查询 nlp_users（通过 username_lower 或 phone_number）
   → 验证 password_hash
   → 查询 nlp_user_roles → nlp_roles → nlp_role_permissions
   → 构建 Principal { roles: ["student"], permissions: [...] }
   → 创建 nlp_sessions 记录
   → 返回 session_token（HTTP-only cookie）
```

#### 场景 2：管理员升级用户为教师

```
1. PUT /api/v1/users/{user_id}/roles
   → 验证当前用户有 SYSTEM_USER_MANAGE 权限
   → 删除旧的 nlp_user_roles 记录
   → 插入新的 nlp_user_roles 记录（role = teacher）
   → 递增 nlp_users.authorization_version
   → 撤销该用户所有旧会话（UPDATE nlp_sessions SET revoked_at = NOW()）
   → 记录审计日志到 nlp_authorization_audit_logs
```

#### 场景 3：教师创建班级并添加学生

```
1. POST /api/v1/classrooms
   → 验证当前用户有 CLASSROOM_CREATE 权限
   → 创建 nlp_classrooms 记录（绑定到教师的工作空间）
   
2. POST /api/v1/classrooms/{classroom_id}/members
   → 验证当前用户有 CLASSROOM_MEMBER_MANAGE 权限
   → 创建 nlp_classroom_members 记录（user_id = 学生ID, member_role = student）
```

#### 场景 4：学生提交练习题

```
1. POST /api/v1/agent/sessions/{session_id}/turns
   → 解析 session_token → 加载 Principal
   → 检查权限：authorization_service.require(principal, Permission.AGENT_TURN_SUBMIT)
   → 检查资源归属：session.owner_user_id == principal.user_id
   → 允许提交
```

---

## 六、安全特性

### 6.1 密码安全
- **哈希算法**: Argon2id（抗暴力破解、抗 GPU 攻击）
- **盐值**: 自动生成，存储在 hash 中
- **强度要求**: 最少 8 字符

### 6.2 会话安全
- **Token 格式**: HMAC-SHA256(session_secret + user_id + timestamp)
- **存储方式**: HTTP-only cookie（防止 XSS 窃取）
- **CSRF 保护**: 每个会话附带 csrf_token，POST 请求必须携带
- **SameSite**: Strict（防止跨站请求伪造）
- **过期时间**: 默认 30 分钟空闲超时，1800 秒绝对过期

### 6.3 权限安全
- **最小权限原则**: 每个角色只拥有必要的权限
- **权限缓存**: Principal 在登录时一次性加载，避免每次查询数据库
- **权限版本**: `authorization_version` 用于检测权限变更，自动使旧会话失效
- **审计日志**: 所有高危操作记录到 `nlp_authorization_audit_logs`

### 6.4 数据安全
- **软删除**: 用户删除标记 `deleted_at`，保留历史数据
- **外键约束**: `ON DELETE CASCADE` / `ON DELETE RESTRICT` 保证数据一致性
- **索引优化**: 关键查询字段建立索引（username_lower, deleted_at, user_id）

---

## 七、常见问题解答

### Q1: 为什么需要 `username_lower` 计算列？
**A**: MySQL 默认区分大小写，"Alice" 和 "alice" 会被视为不同用户。通过生成列 `LOWER(username)` 和唯一索引，确保用户名在数据库层面大小写不敏感，避免重复注册。

### Q2: 用户可以有多个角色吗？
**A**: 是的。`nlp_user_roles` 是多对多关系表，一个用户可以拥有多个角色（如同时是 student 和 teacher）。权限是各角色权限的并集。

### Q3: 如何临时授予用户特殊权限？
**A**: 两种方式：
1. 设置 `nlp_user_roles.expires_at` 为未来某个时间点（临时角色）
2. 直接在 `nlp_role_permissions` 中添加新权限到现有角色

### Q4: 工作空间和班级的区别是什么？
**A**: 
- **工作空间**: 资源隔离边界，每个用户至少有一个个人工作空间
- **班级**: 教学组织单元，由教师创建，用于管理学生群体
- 关系：一个班级绑定到一个工作空间（通常是教师的工作空间）

### Q5: 如何撤销用户的所有会话？
**A**: 管理员调用 `POST /api/v1/users/{user_id}/sessions/revoke`，后端执行：
```sql
UPDATE nlp_sessions 
SET revoked_at = NOW() 
WHERE user_id = :user_id AND revoked_at IS NULL;
```

### Q6: 权限变更后，旧会话会自动失效吗？
**A**: 是的。修改角色/权限时会递增 `nlp_users.authorization_version`，会话加载时会比对版本号，不匹配则拒绝访问。

---

## 八、API 端点概览

### 8.1 认证相关
| 端点 | 方法 | 描述 | 权限要求 |
|-----|------|------|---------|
| `/api/v1/auth/login` | POST | 用户名/手机号登录 | 无 |
| `/api/v1/auth/session` | GET | 获取当前会话信息 | 已登录 |
| `/api/v1/auth/session` | DELETE | 退出登录 | 已登录 |
| `/api/v1/auth/register` | POST | 手机号注册 | 无 |
| `/api/v1/auth/sms/send` | POST | 发送短信验证码 | 无 |

### 8.2 用户管理
| 端点 | 方法 | 描述 | 权限要求 |
|-----|------|------|---------|
| `/api/v1/users` | GET | 列出所有用户 | `system:user:manage` |
| `/api/v1/users` | POST | 创建新用户 | `system:user:manage` |
| `/api/v1/users/me` | GET | 获取当前用户信息 | 已登录 |
| `/api/v1/users/me` | PATCH | 更新当前用户资料 | `identity:profile:update_self` |
| `/api/v1/users/me/password` | POST | 修改自己的密码 | `identity:profile:update_self` |
| `/api/v1/users/{user_id}` | GET | 获取指定用户信息 | `system:user:manage` |
| `/api/v1/users/{user_id}` | PATCH | 更新指定用户资料 | `system:user:manage` |
| `/api/v1/users/{user_id}/roles` | PUT | 替换用户角色 | `system:user:manage` |
| `/api/v1/users/{user_id}` | DELETE | 软删除用户 | `system:user:manage` |
| `/api/v1/users/{user_id}/restore` | POST | 恢复已删除用户 | `system:user:manage` |
| `/api/v1/users/{user_id}/sessions/revoke` | POST | 撤销用户所有会话 | `system:user:manage` |
| `/api/v1/users/{user_id}/password` | POST | 重置用户密码 | `system:user:manage` |

### 8.3 班级管理
| 端点 | 方法 | 描述 | 权限要求 |
|-----|------|------|---------|
| `/api/v1/classrooms` | GET | 列出所有班级 | `classroom:classroom:create` |
| `/api/v1/classrooms` | POST | 创建班级 | `classroom:classroom:create` |
| `/api/v1/classrooms/{id}` | GET | 获取班级详情 | 班级成员 |
| `/api/v1/classrooms/{id}/members` | POST | 添加班级成员 | `classroom:member:manage` |
| `/api/v1/classrooms/{id}/members/{user_id}` | DELETE | 移除班级成员 | `classroom:member:manage` |

---

## 九、总结

Nova 的用户管理与 RBAC 系统采用**分层解耦**设计：

1. **用户层**（`nlp_users`）：负责账号身份和生命周期
2. **角色层**（`nlp_roles` + `nlp_user_roles`）：负责权限分组
3. **权限层**（`nlp_permissions` + `nlp_role_permissions`）：负责最小授权单元
4. **组织层**（`nlp_workspaces` + `nlp_classrooms`）：负责资源隔离和教学组织
5. **会话层**（`nlp_sessions`）：负责登录状态和安全令牌

这种设计的优势：
- ✅ **灵活性**: 支持多角色、临时角色、动态权限调整
- ✅ **安全性**: 最小权限原则、审计日志、会话撤销
- ✅ **可扩展性**: 新增角色/权限无需修改代码
- ✅ **可维护性**: 职责清晰，表结构规范化

---

*文档版本: v1.0*  
*最后更新: 2026-08-27*
