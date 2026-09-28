# 数据库迁移约定（Alembic）

运行时 schema 仅由 Alembic 管理；应用不调用 `create_all`、不执行运行时 DDL。部署入口统一为 `python main.py bootstrap-db`：先执行 `alembic upgrade head`，再安装内置定价并验证生产覆盖。定价属于版本化运行数据，不写进 Alembic 迁移。

`bootstrap-db` 可安全重复运行。系统创建的旧开放定价规则会在同一事务内闭合并切换到新版本；人工或来源不明的重叠规则不会被自动修改。配额开启时，冲突或缺失会返回非零退出码，Compose 的常驻服务因此不会启动；配额关闭时，缺失项会作为警告输出，相关用量保持 `pending`。

## RBAC 权限 / 角色播种必须幂等

**背景（已知坑）**：迁移 `20260804_12_rbac_foundation.py` 用

```python
[permission_row(item) for item in Permission]
```

遍历**运行时**的 `Permission` 枚举来播种 `nlp_permissions`（以及 `nlp_role_permissions`、`nlp_role_permission_scopes`）。因此，往 `core/rbac.py` 的 `Permission` 枚举新增任何成员后，**全新数据库**上迁移 12 会先把该权限播进去，后续迁移再 `bulk_insert` 同一条记录就会主键冲突：

```
pymysql.err.IntegrityError: (1062, "Duplicate entry '...' for key 'nlp_permissions.PRIMARY'")
```

CI 每次在全新 MySQL 上跑 `alembic upgrade head`，所以这类迁移在 CI 必挂。

**规则**：

1. 任何往 `nlp_permissions` / `nlp_roles` / `nlp_role_permissions` / `nlp_role_permission_scopes` 播种新权限或新角色的迁移，必须**先查存在、缺才插**（幂等），不能无条件 `bulk_insert`。
2. 参考实现：`migrations/versions/20260813_16_release_notes.py` —— 对权限、角色授权、scope 三处各自 `SELECT ... first()` 判断后再插入。
3. offline 模式（`alembic upgrade head --sql`）下 `op.get_bind()` 不可用，回退为纯 `bulk_insert`（渲染出的 SQL 不会自动执行，故无冲突），与既有 `context.is_offline_mode()` 防护一致。

**反模式**：从活枚举派生种子数据（`[permission_row(p) for p in Permission]`）。种子要么是冻结的常量快照，要么由新增迁移做幂等插入。

## 通用约定

- 迁移链：每个迁移的 `down_revision` 指向当前 head；`revision` 使用 `YYYYMMDD_NN` 序号，与文件名一致。
- 时间戳列统一 `mysql.DATETIME(fsp=6)` + `server_default=sa.text("UTC_TIMESTAMP(6)")`，与 `server/infrastructure/mysql/base.py` 的 `TimestampedModel` 保持一致。
- `downgrade()` 必须能回滚 `upgrade()` 的全部副作用（含种子数据的删除），顺序与 `upgrade()` 相反。
- 新增迁移时优先复用既有表的 `sa.table()` 投影（见 `20260813_16`），避免在迁移里重复声明完整列。

## 相关文档

- `docs/mysql-foundation-baseline.md` —— MySQL 阶段基线。
- `server/rbac/catalog.py` —— 权限/角色 ID 与种子行的唯一来源（`uuid5` 稳定 ID）。
