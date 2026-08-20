import { StudentSocket } from "./client";

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
    vi.useRealTimers();
    vi.unstubAllGlobals();
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
    }, "qwen");

    const frames = instance.sent.map((value) => JSON.parse(value) as { type: string; v: string; payload: Record<string, unknown> });
    expect(frames[0]).toMatchObject({ v: "1", type: "session.subscribe", payload: { session_id: "session_1" } });
    expect(frames[1]).toMatchObject({ v: "1", type: "chat.send", payload: { session_id: "session_1", content: "hello", idempotency_key: "request_1", model_profile: "qwen", learning_context: { topic: "Transformer", level: "intermediate", mode: "practice" } } });
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
    client.sendChat("session_1", "hello", "request_1");

    instances[0].disconnect();
    vi.advanceTimersByTime(500);
    await Promise.resolve();
    instances[1].open();
    instances[1].onmessage?.({ data: JSON.stringify({ v: "1", type: "connection.ready", timestamp: new Date().toISOString(), payload: {} }) });

    const resent = instances[1].sent.map((value) => JSON.parse(value) as { type: string; request_id: string });
    expect(resent).toContainEqual(expect.objectContaining({ type: "chat.send", request_id: "request_1" }));
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
});
