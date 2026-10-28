import { StudentSocket } from "./client";
import { api } from "@/platform/http/api";

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  readyState = FakeWebSocket.CONNECTING;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public readonly url: string) {}
  open() { this.readyState = FakeWebSocket.OPEN; this.onopen?.(); }
  send(value: string) { this.sent.push(value); }
  close() { this.readyState = FakeWebSocket.CLOSED; }
  disconnect() { this.readyState = FakeWebSocket.CLOSED; this.onclose?.(); }
}

describe("StudentSocket", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("fetches the default ticket when recovery is provided as the fourth callback", async () => {
    const instances: FakeWebSocket[] = [];
    vi.stubGlobal("WebSocket", class extends FakeWebSocket {
      constructor(url: string) { super(url); instances.push(this); }
    });
    const ticket = vi.spyOn(api, "createWsTicket").mockResolvedValue({ ticket: "signed-ticket", expires_in: 30 });
    const recover = vi.fn(async () => undefined);
    const client = new StudentSocket(vi.fn(), vi.fn(), undefined, recover);
    client.connect();
    await vi.waitFor(() => expect(instances).toHaveLength(1));
    expect(ticket).toHaveBeenCalledOnce();
    expect(instances[0].url).toContain("ticket=signed-ticket");
    instances[0].open();
    instances[0].onmessage?.({ data: JSON.stringify({ type: "connection.ready", payload: {} }) });
    expect(recover).toHaveBeenCalledOnce();
    client.close();
  });

  it("uses the versioned backend command envelope and session subscription", async () => {
    const instances: FakeWebSocket[] = [];
    vi.stubGlobal("WebSocket", class extends FakeWebSocket {
      constructor(url: string) { super(url); instances.push(this); }
    });
    const client = new StudentSocket(vi.fn(), vi.fn(), async () => "test-ticket");
    client.setSession("session_1");
    await Promise.resolve();
    const instance = instances[0];
    instance.open();
    expect(instance.sent).toEqual([]);
    instance.onmessage?.({ data: JSON.stringify({ v: "1", type: "connection.ready", timestamp: new Date().toISOString(), payload: {} }) });
    client.sendChat("session_1", "hello", "request_1", {
      topic: "Transformer", level: "intermediate", mode: "practice",
    }, "qwen", undefined, false, {
      workspace_id: "workspace-1",
      topic_id: "topic-1",
      topic_name: "基础",
      knowledge_point_id: "point-1",
      title: "词法分析",
      heading: "核心概念",
      selected_text: "词元",
      content_markdown: "## 核心概念\n\n词元",
    });

    const frames = instance.sent.map((value) => JSON.parse(value) as { type: string; v: string; payload: Record<string, unknown> });
    expect(frames[0]).toMatchObject({ v: "1", type: "session.subscribe", payload: { session_id: "session_1" } });
    expect(frames[1]).toMatchObject({ v: "1", type: "chat.send", payload: { session_id: "session_1", content: "hello", idempotency_key: "request_1", model_profile: "qwen", thinking_enabled: false, learning_context: { topic: "Transformer", level: "intermediate", mode: "practice" }, knowledge_book_context: { knowledge_point_id: "point-1", selected_text: "词元" } } });
    client.close();
  });

  it("refreshes the HTTP auth session after every protocol-level reconnect", async () => {
    const instances: FakeWebSocket[] = [];
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("WebSocket", class extends FakeWebSocket {
      constructor(url: string) { super(url); instances.push(this); }
    });
    const client = new StudentSocket(vi.fn(), vi.fn(), async () => "test-ticket", refreshSession);
    client.setSession("session_1");
    await Promise.resolve();
    instances[0].open();
    instances[0].onmessage?.({ data: JSON.stringify({ v: "1", type: "connection.ready", timestamp: new Date().toISOString(), payload: {} }) });

    await Promise.resolve();
    expect(refreshSession).toHaveBeenCalledTimes(1);
    client.close();
  });

  it("waits for auth/history recovery before replaying commands", async () => {
    const instances: FakeWebSocket[] = [];
    vi.stubGlobal("WebSocket", class extends FakeWebSocket {
      constructor(url: string) { super(url); instances.push(this); }
    });
    let release!: () => void;
    const recovered = new Promise<void>((resolve) => { release = resolve; });
    const status = vi.fn();
    const client = new StudentSocket(vi.fn(), status, async () => "test-ticket", () => recovered);
    client.setSession("session-1");
    client.sendChat("session-1", "pending question", "request-1");
    await Promise.resolve();
    instances[0].open();
    instances[0].onmessage?.({ data: JSON.stringify({ type: "connection.ready", payload: {} }) });
    expect(instances[0].sent).toEqual([]);
    expect(status).not.toHaveBeenCalledWith("connected");
    release();
    await recovered;
    await Promise.resolve();
    expect(status).toHaveBeenCalledWith("connected");
    expect(instances[0].sent.map((frame) => JSON.parse(frame).type)).toEqual(["session.subscribe", "chat.send"]);
    client.close();
  });

  it("subscribes to a newly created session when randomUUID is unavailable over HTTP", async () => {
    const instances: FakeWebSocket[] = [];
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.fill(7);
        return bytes;
      },
    });
    vi.stubGlobal("WebSocket", class extends FakeWebSocket {
      constructor(url: string) { super(url); instances.push(this); }
    });

    const client = new StudentSocket(vi.fn(), vi.fn(), async () => "test-ticket");
    client.setSession("session_http");
    await Promise.resolve();
    instances[0].open();

    expect(() => instances[0].onmessage?.({
      data: JSON.stringify({ v: "1", type: "connection.ready", timestamp: new Date().toISOString(), payload: {} }),
    })).not.toThrow();
    expect(instances[0].sent.map((value) => JSON.parse(value))).toContainEqual(expect.objectContaining({
      type: "session.subscribe",
      payload: { session_id: "session_http" },
    }));
    client.close();
  });

  it.each(["quick", "light", "deep", "extreme", true, false, undefined] as const)("preserves this message's thinking selection (%s)", async (selection) => {
    const instances: FakeWebSocket[] = [];
    vi.stubGlobal("WebSocket", class extends FakeWebSocket {
      constructor(url: string) { super(url); instances.push(this); }
    });
    const client = new StudentSocket(vi.fn(), vi.fn(), async () => "test-ticket");
    client.setSession("session_1");
    await Promise.resolve();
    instances[0].open();
    instances[0].onmessage?.({ data: JSON.stringify({ type: "connection.ready", payload: {} }) });
    client.sendChat("session_1", "hello", "request_1", undefined, "deepseek", undefined, selection);

    const command = instances[0].sent.map((value) => JSON.parse(value)).find((frame) => frame.type === "chat.send");
    if (typeof selection === "string") {
      expect(command.payload.thinking_level).toBe(selection);
      expect(command.payload).not.toHaveProperty("thinking_enabled");
    } else if (typeof selection === "boolean") {
      expect(command.payload.thinking_enabled).toBe(selection);
      expect(command.payload).not.toHaveProperty("thinking_level");
    } else {
      expect(command.payload).not.toHaveProperty("thinking_level");
      expect(command.payload).not.toHaveProperty("thinking_enabled");
    }
    client.close();
  });

  it("resends an idempotent chat command when the connection drops before its acknowledgement", async () => {
    vi.useFakeTimers();
    const instances: FakeWebSocket[] = [];
    vi.stubGlobal("WebSocket", class extends FakeWebSocket {
      constructor(url: string) { super(url); instances.push(this); }
    });
    const client = new StudentSocket(vi.fn(), vi.fn(), async () => "test-ticket");
    client.setSession("session_1");
    await Promise.resolve();
    instances[0].open();
    instances[0].onmessage?.({ data: JSON.stringify({ v: "1", type: "connection.ready", timestamp: new Date().toISOString(), payload: {} }) });
    client.sendChat(
      "session_1",
      "hello",
      "request_1",
      undefined,
      undefined,
      [{ file_name: "safe-image.png" }],
      "quick",
    );

    instances[0].disconnect();
    vi.advanceTimersByTime(500);
    await Promise.resolve();
    instances[1].open();
    instances[1].onmessage?.({ data: JSON.stringify({ v: "1", type: "connection.ready", timestamp: new Date().toISOString(), payload: {} }) });

    const resent = instances[1].sent.map((value) => JSON.parse(value) as { type: string; request_id: string; payload: Record<string, unknown> });
    expect(resent).toContainEqual(expect.objectContaining({
      type: "chat.send",
      request_id: "request_1",
      payload: expect.objectContaining({ attachments: [{ file_name: "safe-image.png" }], thinking_level: "quick" }),
    }));
    client.close();
  });

  it("does not resend a chat command that the server already acknowledged", async () => {
    vi.useFakeTimers();
    const instances: FakeWebSocket[] = [];
    vi.stubGlobal("WebSocket", class extends FakeWebSocket {
      constructor(url: string) { super(url); instances.push(this); }
    });
    const client = new StudentSocket(vi.fn(), vi.fn(), async () => "test-ticket");
    client.setSession("session_1");
    await Promise.resolve();
    instances[0].open();
    instances[0].onmessage?.({ data: JSON.stringify({ v: "1", type: "connection.ready", timestamp: new Date().toISOString(), payload: {} }) });
    client.sendChat("session_1", "hello", "request_1");
    instances[0].onmessage?.({ data: JSON.stringify({ v: "1", type: "command.ack", request_id: "request_1", timestamp: new Date().toISOString(), payload: {} }) });

    instances[0].disconnect();
    vi.advanceTimersByTime(500);
    await Promise.resolve();
    instances[1].open();
    instances[1].onmessage?.({ data: JSON.stringify({ v: "1", type: "connection.ready", timestamp: new Date().toISOString(), payload: {} }) });

    const resent = instances[1].sent.map((value) => JSON.parse(value) as { type: string; request_id: string });
    expect(resent).not.toContainEqual(expect.objectContaining({ type: "chat.send", request_id: "request_1" }));
    client.close();
  });

  it("includes attachments in chat.send payload when provided", async () => {
    const instances: FakeWebSocket[] = [];
    vi.stubGlobal("WebSocket", class extends FakeWebSocket {
      constructor(url: string) { super(url); instances.push(this); }
    });
    const client = new StudentSocket(vi.fn(), vi.fn(), async () => "test-ticket");
    client.setSession("session_att");
    await Promise.resolve();
    instances[0].open();
    instances[0].onmessage?.({ data: JSON.stringify({ v: "1", type: "connection.ready", timestamp: new Date().toISOString(), payload: {} }) });
    client.sendChat("session_att", "check picture", "req_att", undefined, undefined, [{ file_name: "test.png" }]);

    const frames = instances[0].sent.map((value) => JSON.parse(value) as { type: string; payload: Record<string, unknown> });
    expect(frames[1]).toMatchObject({
      type: "chat.send",
      payload: {
        session_id: "session_att",
        content: "check picture",
        attachments: [{ file_name: "test.png" }],
      },
    });
    client.close();
  });
});
