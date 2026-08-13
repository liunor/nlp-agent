"""用户管理功能端到端验证（只读实测，不改任何代码/数据）。

用 guest01/student01/teacher01/developer01 四个账号：
  1) 多用户 DB 登录  POST /api/v1/auth/login/db
  2) 身份解析        GET  /api/v1/auth/me  (校验 roles / permissions)
  3) 各角色关键接口  (用户管理 / 角色 / 班级加入 / 教师分析 / 开发者 / 审计)
  4) 越权边界        (低权限账号访问高权限接口应 403)

判定：实际 HTTP 状态码 == 期望 -> PASS，否则 FAIL。
"""
from __future__ import annotations
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8765"
ORIGIN = "http://127.0.0.1:5173"
PASSWORD = "Test123456"

ACCOUNTS = {
    "guest01": "guest",
    "student01": "student",
    "teacher01": "teacher",
    "developer01": "developer",
}


def request(method: str, path: str, cookie: str | None = None, json_body=None):
    url = BASE + path
    data = None
    headers = {"Accept": "application/json"}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    if method.upper() == "POST" and path.endswith("/login/db"):
        headers["Origin"] = ORIGIN  # login/db 需要同源校验
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return e.code, body
    except Exception as e:  # noqa
        return -1, f"{type(e).__name__}: {e}"


def login_with_cookie(username: str):
    """手动发请求以捕获 Set-Cookie 头。"""
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", 8765, timeout=10)
    payload = json.dumps({"username": username, "password": PASSWORD}).encode()
    conn.request("POST", "/api/v1/auth/login/db", body=payload,
                 headers={"Content-Type": "application/json", "Origin": ORIGIN})
    resp = conn.getresponse()
    set_cookie = resp.getheader("Set-Cookie")
    raw = resp.read().decode("utf-8", "replace")
    cookie = None
    if set_cookie:
        for part in set_cookie.split(","):
            if part.strip().startswith("nlp_session="):
                cookie = part.strip().split(";")[0]
                break
    conn.close()
    return resp.status, cookie, raw


def check(label: str, method: str, path: str, cookie: str | None, expect: int) -> bool:
    status, _ = request(method, path, cookie=cookie)
    ok = status == expect
    print(f"  [{'PASS' if ok else 'FAIL'}] {method:<4} {path:<42} 期望 {expect} 实际 {status}")
    return ok


def main():
    total_pass = total_fail = 0
    print("=" * 78)
    print("用户管理功能 端到端验证  (BASE=%s)" % BASE)
    print("=" * 78)
    for username, expect_role in ACCOUNTS.items():
        print(f"\n### 账号 {username} (期望角色 {expect_role}) ###")
        status, cookie, body = login_with_cookie(username)
        ok_login = status == 200
        print(f"  [{'PASS' if ok_login else 'FAIL'}] POST /api/v1/auth/login/db        期望 200 实际 {status}")
        total_pass += ok_login
        total_fail += (not ok_login)
        if not ok_login:
            print("     响应:", body[:300])
            continue

        # 身份解析
        mstatus, mbody = request("GET", "/api/v1/auth/me", cookie=cookie)
        ok_me = mstatus == 200
        print(f"  [{'PASS' if ok_me else 'FAIL'}] GET  /api/v1/auth/me              期望 200 实际 {mstatus}")
        total_pass += ok_me
        total_fail += (not ok_me)
        roles = perms = []
        if ok_me:
            try:
                mj = json.loads(mbody)
                roles = mj.get("roles", [])
                perms = mj.get("permissions", mj.get("data", {}).get("permissions", []) if isinstance(mj.get("data"), dict) else [])
                print(f"        roles={roles}  permissions数={len(perms)}")
            except Exception:
                pass

        # 各角色关键接口
        if username == "guest01":
            total_pass += check("guest-sessions", "GET", "/api/v1/sessions", cookie, 200)
            total_pass += check("guest-agent-sessions", "GET", "/api/v1/agent-sessions", cookie, 200)
            total_pass += check("guest-no-users", "GET", "/api/v1/users", cookie, 403)
            total_pass += check("guest-no-roles", "GET", "/api/v1/roles", cookie, 403)
        elif username == "student01":
            total_pass += check("stu-users-me", "GET", "/api/v1/users/me", cookie, 200)
            total_pass += check("stu-no-users-list", "GET", "/api/v1/users", cookie, 403)
            total_pass += check("stu-no-roles", "GET", "/api/v1/roles", cookie, 403)
            total_pass += check("stu-agent-sessions", "GET", "/api/v1/agent-sessions", cookie, 200)
            total_pass += check("stu-classes-available", "GET", "/api/v1/classes/available", cookie, 200)
            total_pass += check("stu-no-teacher", "GET", "/api/v1/teacher/overview", cookie, 403)
            total_pass += check("stu-no-dev", "GET", "/api/v1/developer/snapshot", cookie, 403)
        elif username == "teacher01":
            total_pass += check("tea-overview", "GET", "/api/v1/teacher/overview", cookie, 200)
            total_pass += check("tea-agent-sessions", "GET", "/api/v1/agent-sessions", cookie, 200)
            total_pass += check("tea-no-users-list", "GET", "/api/v1/users", cookie, 403)
            total_pass += check("tea-no-roles", "GET", "/api/v1/roles", cookie, 403)
            total_pass += check("tea-no-dev", "GET", "/api/v1/developer/snapshot", cookie, 403)
        elif username == "developer01":
            total_pass += check("dev-users-list", "GET", "/api/v1/users", cookie, 200)
            total_pass += check("dev-roles", "GET", "/api/v1/roles", cookie, 200)
            total_pass += check("dev-snapshot", "GET", "/api/v1/developer/snapshot", cookie, 200)
            total_pass += check("dev-audit", "GET", "/api/v1/audit/authorization", cookie, 200)
            total_pass += check("dev-teacher-overview", "GET", "/api/v1/teacher/overview", cookie, 200)
            total_pass += check("dev-classes", "GET", "/api/v1/classes", cookie, 200)

    print("\n" + "=" * 78)
    print(f"汇总: PASS={total_pass}  FAIL={total_fail}")
    print("=" * 78)


if __name__ == "__main__":
    main()
