"""验证 core/rbac 的权限决策是否真的生效（角色 -> 放行/拒绝）。

运行: .venv/Scripts/python.exe verify_rbac_permissions.py
说明: 本脚本只验证"请求时真正被调用的那一层"——core/rbac.authorization_service，
      也就是 server/*/controller.py 里 authorization_service.require(...) 所用的同一对象。
      它不依赖数据库（审计记录是内存级 ContextVar）。
"""
from __future__ import annotations

from core.rbac import (
    Permission,
    ROLE_PERMISSIONS,
    authorization_service,
    ResourceRef,
)
from core.identity import AuthenticatedPrincipal, AccessDeniedError

# catalog.py 是“代码词表 -> 数据库播种”的桥：它把每个权限映射成默认作用域，
# 并由迁移 20260804_12 写进 nlp_role_permission_scopes。运行时 principal 的
# permission_scopes 正是这张表读回来的结果。这里用它来构造“迁移后运行时”的 principal，
# 从而真实地验证 require_resource 的对象级作用域是否生效。
from server.rbac.catalog import permission_scope

ROLES = ["guest", "student", "teacher", "developer", "admin"]


def principal(roles, *, workspace_ids=("ws1",), classroom_ids=(), permissions=()):
    """角色目录型 principal（无显式 permission_scopes）——核心 rbac 的兜底路径。"""
    return AuthenticatedPrincipal(
        user_id="test-actor",
        roles=frozenset(roles),
        workspace_ids=frozenset(workspace_ids),
        classroom_ids=frozenset(classroom_ids),
        permissions=frozenset(permissions),
    )


def production_principal(role, *, workspace_ids=("ws1",), classroom_ids=()):
    """模拟 principal_for_user_id 在迁移已应用后的产出：
    permissions 来自 ROLE_PERMISSIONS[role]，permission_scopes 来自 catalog.permission_scope。
    """
    perms = ROLE_PERMISSIONS[role]
    scopes: dict[str, frozenset[str]] = {
        p.value: frozenset({permission_scope(p)}) for p in perms
    }
    return AuthenticatedPrincipal(
        user_id="test-actor",
        roles=frozenset({role}),
        workspace_ids=frozenset(workspace_ids),
        classroom_ids=frozenset(classroom_ids),
        permissions=frozenset(p.value for p in perms),
        permission_scopes=scopes,
    )


def decision(role: str, perm: Permission) -> str:
    """实地调用 authorization_service.require，返回 ALLOW / DENY。"""
    p = principal([role])
    try:
        authorization_service.require(p, perm)
        return "ALLOW"
    except AccessDeniedError:
        return "DENY"


# ---- 1. 角色级放行/拒绝矩阵（期望结果与实现一致才算通过）----
CASES = [
    # (角色, 权限, 期望)
    ("guest", Permission.IDENTITY_PROFILE_READ_SELF, "ALLOW"),
    ("guest", Permission.IDENTITY_PROFILE_UPDATE_SELF, "ALLOW"),
    ("guest", Permission.LEARNING_CONTENT_READ_PUBLIC, "ALLOW"),
    ("guest", Permission.AGENT_SESSION_CREATE, "DENY"),
    ("guest", Permission.SYSTEM_USER_MANAGE, "DENY"),

    ("student", Permission.AGENT_SESSION_CREATE, "ALLOW"),
    ("student", Permission.AGENT_TURN_SUBMIT, "ALLOW"),
    ("student", Permission.LEARNING_CONTENT_READ_WORKSPACE, "ALLOW"),
    ("student", Permission.LEARNING_EXERCISE_SUBMIT, "ALLOW"),
    ("student", Permission.CLASSROOM_CREATE, "DENY"),   # 学生不能建教室
    ("student", Permission.SYSTEM_USER_MANAGE, "DENY"),

    ("teacher", Permission.CLASSROOM_CREATE, "ALLOW"),
    ("teacher", Permission.CLASSROOM_MEMBER_MANAGE, "ALLOW"),
    ("teacher", Permission.LEARNING_CONTENT_MANAGE, "ALLOW"),
    ("teacher", Permission.LEARNING_FEEDBACK_CREATE, "ALLOW"),
    ("teacher", Permission.SYSTEM_USER_MANAGE, "DENY"),  # 教师不能管用户
    ("teacher", Permission.SYSTEM_ROLE_MANAGE, "DENY"),

    ("developer", Permission.SYSTEM_USER_MANAGE, "ALLOW"),
    ("developer", Permission.SYSTEM_ROLE_MANAGE, "ALLOW"),
    ("developer", Permission.SYSTEM_AUDIT_READ, "ALLOW"),
    ("developer", Permission.SYSTEM_MODEL_PROFILE_MANAGE, "ALLOW"),

    ("admin", Permission.SYSTEM_USER_MANAGE, "ALLOW"),   # admin == developer
    ("admin", Permission.SYSTEM_ROLE_MANAGE, "ALLOW"),
    ("admin", Permission.SYSTEM_SENSITIVE_DATA_READ, "DENY"),  # 任何角色都没有此权限
]


def run_case_matrix():
    print("=" * 78)
    print("1) 角色级权限矩阵 (authorization_service.require 实跑)")
    print("=" * 78)
    header = f"{'角色':<10}{'权限':<42}{'期望':<7}{'实际':<7}结果"
    print(header)
    print("-" * 78)
    all_pass = True
    for role, perm, expect in CASES:
        got = decision(role, perm)
        ok = got == expect
        all_pass &= ok
        flag = "PASS" if ok else "FAIL"
        print(f"{role:<10}{perm.value:<42}{expect:<7}{got:<7}{flag}")
    return all_pass


# ---- 2. 层级单调性: guest ⊂ student ⊂ teacher ⊂ developer(=admin) ----
def run_hierarchy():
    print()
    print("=" * 78)
    print("2) 角色权限层级 (下层权限应被上层完全包含)")
    print("=" * 78)
    order = ["guest", "student", "teacher", "developer"]
    all_pass = True
    for low, high in zip(order, order[1:]):
        ok = ROLE_PERMISSIONS[low] <= ROLE_PERMISSIONS[high]
        all_pass &= ok
        print(f"  {low} ({len(ROLE_PERMISSIONS[low])}) ⊆ {high} ({len(ROLE_PERMISSIONS[high])})"
              f"  -> {'PASS' if ok else 'FAIL'}")
    same = ROLE_PERMISSIONS["admin"] == ROLE_PERMISSIONS["developer"]
    all_pass &= same
    print(f"  admin == developer  -> {'PASS' if same else 'FAIL'}")
    return all_pass


# ---- 3. 对象级作用域 (ResourcePolicy / require_resource): 模拟迁移后运行时 principal ----
def run_scope():
    print()
    print("=" * 78)
    print("3) 对象级作用域 (require_resource) —— 用 catalog 模拟迁移后运行时 principal")
    print("   说明: 核心 rbac 的 allowed_resource 对“角色目录型(无 scopes)”主体会把非 system 权限")
    print("        回落成 own 作用域；真实运行时 principal 由 principal_for_user_id 从")
    print("        nlp_role_permission_scopes 读回 permission_scopes，故此处用 production_principal。")
    print("=" * 78)
    all_pass = True

    # 教师(在教室c1)管理 c1 成员应放行，管理 c2(非所在)应拒绝 —— app.py:577 真实守卫
    teacher = production_principal("teacher", classroom_ids=["c1"])
    allow_own = authorization_service.allowed_resource(
        teacher, Permission.CLASSROOM_MEMBER_MANAGE, ResourceRef(resource_type="classroom", classroom_id="c1"))
    deny_other = authorization_service.allowed_resource(
        teacher, Permission.CLASSROOM_MEMBER_MANAGE, ResourceRef(resource_type="classroom", classroom_id="c2"))
    ok1 = allow_own and not deny_other
    all_pass &= ok1
    print(f"  教师管理教室c1成员(自己所在): {allow_own} | 管理c2(非所在): {deny_other} -> {'PASS' if ok1 else 'FAIL'}")

    # 学生只能读自己工作区的内容
    student = production_principal("student", workspace_ids=["ws1"])
    allow_ws = authorization_service.allowed_resource(
        student, Permission.LEARNING_CONTENT_READ_WORKSPACE,
        ResourceRef(resource_type="content", workspace_id="ws1", is_public=False))
    deny_ws = authorization_service.allowed_resource(
        student, Permission.LEARNING_CONTENT_READ_WORKSPACE,
        ResourceRef(resource_type="content", workspace_id="ws2", is_public=False))
    ok2 = allow_ws and not deny_ws
    all_pass &= ok2
    print(f"  学生读ws1内容(自己所在): {allow_ws} | 读ws2(非所在): {deny_ws} -> {'PASS' if ok2 else 'FAIL'}")

    # 公开资源对 guest 也放行
    guest = production_principal("guest")
    allow_pub = authorization_service.allowed_resource(
        guest, Permission.LEARNING_CONTENT_READ_PUBLIC,
        ResourceRef(resource_type="content", is_public=True))
    ok3 = allow_pub
    all_pass &= ok3
    print(f"  guest 读公开资源: {allow_pub} -> {'PASS' if ok3 else 'FAIL'}")

    # 反例: 若迁移未应用(无 permission_scopes)，require_resource 会回落 own 作用域并拒绝教师
    teacher_bare = principal(["teacher"], classroom_ids=["c1"])
    bare_denied = not authorization_service.allowed_resource(
        teacher_bare, Permission.CLASSROOM_MEMBER_MANAGE, ResourceRef(resource_type="classroom", classroom_id="c1"))
    all_pass &= bare_denied
    print(f"  [边界] 迁移未应用时教师被回落拒绝(符合预期): {bare_denied} -> {'PASS' if bare_denied else 'FAIL'}")
    return all_pass


# ---- 4. fail-closed: SYSTEM_SENSITIVE_DATA_READ 未被任何角色授予 ----
def run_fail_closed():
    print()
    print("=" * 78)
    print("4) 未授权权限 (fail-closed) —— SYSTEM_SENSITIVE_DATA_READ 不应被任何角色拥有")
    print("=" * 78)
    granted_by = [r for r, perms in ROLE_PERMISSIONS.items() if Permission.SYSTEM_SENSITIVE_DATA_READ in perms]
    ok = len(granted_by) == 0
    print(f"  拥有该权限的角色: {granted_by or '无'} -> {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    results = [run_case_matrix(), run_hierarchy(), run_scope(), run_fail_closed()]
    print()
    print("=" * 78)
    total = "ALL PASS ✅" if all(results) else "SOME FAILED ❌"
    print(f"结论: {total}")
    print("=" * 78)
    raise SystemExit(0 if all(results) else 1)
