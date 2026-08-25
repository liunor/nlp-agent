import { api, ensureAuth, uploadAttachment } from "./api";

describe("FastAPI client", () => {
  it("uses the authenticated session and attaches CSRF to mutations", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({
        user_id: "local",
        workspace_ids: ["default"],
        roles: ["student"],
        csrf_token: "csrf-token",
        expires_at: 123,
      }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ session_id: "session_1" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }));

    await ensureAuth();
    await api.createSession();

    const mutation = fetchMock.mock.calls[1][1];
    expect(new Headers(mutation?.headers).get("X-CSRF-Token")).toBe("csrf-token");
    expect(mutation?.credentials).toBe("include");
    fetchMock.mockRestore();
  });

  it("uploads an attachment using FormData without forcing application/json Content-Type", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          file_name: "abc.png",
          url: "/api/v1/uploads/s1/abc.png",
          media_type: "image/png",
          size_bytes: 1234,
          width: 100,
          height: 100,
          sha256: "0".repeat(64),
        }),
        { status: 201, headers: { "Content-Type": "application/json" } }
      )
    );

    const file = new File(["test-content"], "test.png", { type: "image/png" });
    const res = await uploadAttachment("s1", file);

    expect(res.file_name).toBe("abc.png");
    const call = fetchMock.mock.calls[0];
    expect(call[0]).toBe("/api/v1/uploads");
    const init = call[1];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeInstanceOf(FormData);
    expect(new Headers(init?.headers).get("Content-Type")).toBeNull();
    fetchMock.mockRestore();
  });
});
