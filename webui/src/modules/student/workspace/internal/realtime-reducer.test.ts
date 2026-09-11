import type { ChatMessage, LearningPreferences, ServerEvent } from "@/shared/types";

import { createRealtimeEventHandler } from "./realtime-reducer";

describe("realtime acknowledgement reconciliation", () => {
  it("reconciles an acknowledgement that arrives after the user switches sessions", () => {
    let messages: ChatMessage[] = [];
    const pendingRequests = { current: new Map([["request-1", "request-1:user"]]) };
    const inFlightTurnIds = { current: new Set(["request-1"]) };
    const handler = createRealtimeEventHandler({
      socketRef: { current: null },
      activeSessionRef: { current: "session-2" },
      pendingRequests,
      inFlightTurnIds,
      setMessages: (update) => {
        messages = typeof update === "function" ? update(messages) : update;
      },
      setActiveSessionId: vi.fn(),
      setRequestError: vi.fn(),
      persistPreferences: vi.fn((update: (value: LearningPreferences) => LearningPreferences) => update({
        version: 2,
        context: { topic_id: null, topic_name: "", level: "beginner", mode: "explain" },
        sessions: {},
        categories: [],
      })),
      updateSessionMeta: vi.fn(),
      loadSessions: vi.fn(async () => []),
      loadTurns: vi.fn(async () => undefined),
    });
    const ack: ServerEvent = {
      v: "1",
      type: "command.ack",
      request_id: "request-1",
      session_id: "session-1",
      turn_id: "turn-1",
      timestamp: new Date().toISOString(),
      payload: { command: "chat.send" },
    };

    handler(ack);

    expect(pendingRequests.current.has("request-1")).toBe(false);
    expect(inFlightTurnIds.current.has("request-1")).toBe(false);
    expect(inFlightTurnIds.current.has("turn-1")).toBe(true);
  });

  it("keeps a cancelling turn closed against late deltas and completion events", () => {
    let messages: ChatMessage[] = [{
      id: "turn-1:assistant",
      turnId: "turn-1",
      role: "assistant",
      content: "partial",
      status: "cancelling",
      createdAt: "2026-09-09T00:00:00Z",
    }];
    const pendingRequests = { current: new Map<string, string>() };
    const inFlightTurnIds = { current: new Set(["turn-1"]) };
    const cancelledTurnIds = { current: new Set(["turn-1"]) };
    const updateSessionMeta = vi.fn();
    const loadSessions = vi.fn(async () => []);
    const handler = createRealtimeEventHandler({
      socketRef: { current: null },
      activeSessionRef: { current: "session-1" },
      pendingRequests,
      inFlightTurnIds,
      cancelledTurnIds,
      setMessages: (update) => {
        messages = typeof update === "function" ? update(messages) : update;
      },
      setActiveSessionId: vi.fn(),
      setRequestError: vi.fn(),
      persistPreferences: vi.fn(),
      updateSessionMeta,
      loadSessions,
      loadTurns: vi.fn(async () => undefined),
    });
    const event = (type: string, payload: Record<string, unknown> = {}): ServerEvent => ({
      v: "1",
      type,
      session_id: "session-1",
      turn_id: "turn-1",
      timestamp: "2026-09-09T00:00:01Z",
      payload,
    });

    handler(event("chat.delta", { delta: "late" }));
    expect(messages[0].content).toBe("partial");
    handler(event("chat.cancelled"));
    handler(event("chat.completed", { content: "late final" }));

    expect(messages[0].status).toBe("cancelled");
    expect(messages[0].content).toBe("partial");
    expect(inFlightTurnIds.current.has("turn-1")).toBe(false);
    expect(updateSessionMeta).not.toHaveBeenCalled();
    expect(loadSessions).not.toHaveBeenCalled();
  });
});
