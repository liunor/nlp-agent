function reply(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function session(id: string) {
  return { user_id: id, csrf_token: `csrf-${id}`, roles: ["student"], workspace_ids: ["default"], expires_at: 1900000000 };
}

function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("authentication response ownership", () => {
  beforeEach(() => vi.resetModules());
  afterEach(() => vi.restoreAllMocks());

  it("ignores an old 401 after successful login and retains the new CSRF", async () => {
    const client = await import("./api");
    const old = deferred();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(reply(session("old")))
      .mockReturnValueOnce(old.promise).mockResolvedValueOnce(reply(session("new")))
      .mockResolvedValueOnce(reply({ session_id: "created" }));
    const expired = vi.fn();
    window.addEventListener(client.AUTH_EXPIRED_EVENT, expired);
    try {
      await client.ensureAuth();
      const pending = client.api.listSessions().catch((e) => e.status);
      await client.api.login("fixture", "fixture-password");
      old.resolve(reply({ code: "authentication_required" }, 401));
      expect(await pending).toBe(401);
      await client.api.createSession();
      expect(new Headers(fetchMock.mock.calls[3][1]?.headers).get("X-CSRF-Token")).toBe("csrf-new");
      expect(expired).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener(client.AUTH_EXPIRED_EVENT, expired);
    }
  });

  it("ignores a 401 from a request started during login after the new session is established", async () => {
    const client = await import("./api");
    const login = deferred();
    const duringLogin = deferred();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValueOnce(login.promise)
      .mockReturnValueOnce(duringLogin.promise).mockResolvedValueOnce(reply({ session_id: "created" }));
    const authRequired = vi.fn();
    window.addEventListener(client.AUTH_REQUIRED_EVENT, authRequired);
    try {
      const pendingLogin = client.api.login("new", "fixture-password");
      const pendingRequest = client.api.listSessions().catch((error) => error.status);

      login.resolve(reply(session("new")));
      await pendingLogin;
      duringLogin.resolve(reply({ code: "authentication_required" }, 401));
      expect(await pendingRequest).toBe(401);

      await client.api.createSession();
      expect(new Headers(fetchMock.mock.calls[2][1]?.headers).get("X-CSRF-Token")).toBe("csrf-new");
      expect(authRequired).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener(client.AUTH_REQUIRED_EVENT, authRequired);
    }
  });

  it("rejects a delayed old session restore instead of overwriting a new login", async () => {
    const client = await import("./api");
    const old = deferred();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(reply(session("new"))).mockResolvedValueOnce(reply({ session_id: "created" }));
    const restore = client.ensureAuth().catch((e) => e);
    await client.api.login("fixture", "fixture-password");
    old.resolve(reply(session("old")));
    expect(await restore).toBeInstanceOf(client.AuthStateChangedError);
    await client.api.createSession();
    expect(new Headers(fetchMock.mock.calls[2][1]?.headers).get("X-CSRF-Token")).toBe("csrf-new");
  });

  it("does not replay an old rejected mutation under a newer login", async () => {
    const client = await import("./api");
    const old = deferred();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(reply(session("old")))
      .mockReturnValueOnce(old.promise).mockResolvedValueOnce(reply(session("new")));
    await client.ensureAuth();
    const mutation = client.api.createSession().catch((e) => e.status);
    await client.api.login("fixture", "fixture-password");
    old.resolve(reply({ code: "csrf_rejected" }, 403));
    expect(await mutation).toBe(403);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("still emits expiration once for the current rejected session", async () => {
    const client = await import("./api");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(reply(session("current")))
      .mockResolvedValue(reply({ code: "authentication_required" }, 401));
    const expired = vi.fn();
    window.addEventListener(client.AUTH_EXPIRED_EVENT, expired);
    try {
      await client.ensureAuth();
      await Promise.allSettled([client.api.listSessions(), client.api.getSettings()]);
      expect(expired).toHaveBeenCalledTimes(1);
    } finally { window.removeEventListener(client.AUTH_EXPIRED_EVENT, expired); }
  });

  it("serializes concurrent login responses and publishes only the latest session", async () => {
    const client = await import("./api");
    const first = deferred();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce(reply(session("second"))).mockResolvedValueOnce(reply({ session_id: "created" }));
    const firstLogin = client.api.login("first", "fixture-password").catch((e) => e);
    const secondLogin = client.api.login("second", "fixture-password");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    first.resolve(reply(session("first")));
    expect(await firstLogin).toBeInstanceOf(client.AuthStateChangedError);
    expect((await secondLogin).user_id).toBe("second");
    await client.api.createSession();
    expect(new Headers(fetchMock.mock.calls[2][1]?.headers).get("X-CSRF-Token")).toBe("csrf-second");
  });

  it("waits for an in-flight logout before issuing a new login", async () => {
    const client = await import("./api");
    const logout = deferred();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(reply(session("old")))
      .mockReturnValueOnce(logout.promise).mockResolvedValueOnce(reply(session("new")));
    await client.ensureAuth();
    const pendingLogout = client.api.logout();
    const pendingLogin = client.api.login("new", "fixture-password");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    logout.resolve(new Response(null, { status: 204 }));
    await pendingLogout;
    expect((await pendingLogin).user_id).toBe("new");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/v1/auth/login");
  });

  it("waits for login before restoring the cookie-backed session", async () => {
    const client = await import("./api");
    const login = deferred();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValueOnce(login.promise)
      .mockResolvedValueOnce(reply(session("new")));
    const pendingLogin = client.api.login("new", "fixture-password");
    const pendingRestore = client.ensureAuth();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    login.resolve(reply(session("new")));
    await pendingLogin;
    expect((await pendingRestore).user_id).toBe("new");
  });

  it("reports the login cooldown instead of expiring a session", async () => {
    const client = await import("./api");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(reply({ code: "login_rate_limited" }, 429, { "Retry-After": "75" }));
    await expect(client.api.login("fixture", "fixture-password")).rejects.toMatchObject({
      status: 429, code: "login_rate_limited", retryAfterSeconds: 75, message: "登录尝试过多，请等待 75 秒后重试。",
    });
  });

  it("ignores old-tab 401 after a cross-tab session-change notification", async () => {
    const client = await import("./api");
    const old = deferred();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(reply(session("old")))
      .mockReturnValueOnce(old.promise).mockResolvedValueOnce(reply(session("other-tab")));
    const changed = vi.fn();
    const expired = vi.fn();
    const unsubscribe = client.subscribeAuthChanges(changed);
    window.addEventListener(client.AUTH_EXPIRED_EVENT, expired);
    try {
      await client.ensureAuth();
      const pending = client.api.listSessions().catch((e) => e.status);
      window.dispatchEvent(new StorageEvent("storage", { key: "nova:auth-changed", newValue: "new-generation" }));
      expect(changed).toHaveBeenCalledTimes(1);
      await client.ensureAuth();
      old.resolve(reply({}, 401));
      expect(await pending).toBe(401);
      expect(expired).not.toHaveBeenCalled();
    } finally { unsubscribe(); window.removeEventListener(client.AUTH_EXPIRED_EVENT, expired); }
  });

  it("does not let unavailable browser storage fail login", async () => {
    const client = await import("./api");
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => { throw new Error("storage disabled"); });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(reply(session("current")));
    expect((await client.api.login("fixture", "fixture-password")).user_id).toBe("current");
  });
});
