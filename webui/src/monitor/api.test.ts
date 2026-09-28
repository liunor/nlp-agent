import { afterEach, describe, expect, it, vi } from "vitest";

import { monitorApi } from "./api";

describe("monitor API routing", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses the monitor-only API prefix for login and observability requests", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        user_id: "operator",
        roles: ["admin"],
        permissions: ["system:runtime:monitor"],
        csrf_token: "csrf",
        expires_at: 1_900_000_000,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({}), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await monitorApi.login("operator", "password");
    await monitorApi.storage();

    expect(fetchMock.mock.calls[0][0]).toBe("/monitor-api/v1/auth/login");
    expect(fetchMock.mock.calls[1][0]).toBe("/monitor-api/v1/observability/storage");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "include" });
  });
});
