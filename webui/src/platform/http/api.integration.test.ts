import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { existsSync } from "node:fs";
import http from "node:http";
import net from "node:net";
import path from "node:path";
import { WebSocket as NetworkWebSocket } from "ws";

import { api, ensureAuth } from "./api";
import { StudentSocket } from "@/platform/realtime/client";

type IntegrationResponse = Response & { integrationSetCookie?: string };

async function freePort(): Promise<number> {
  const server = net.createServer();
  await new Promise<void>((resolve, reject) => server.listen(0, "127.0.0.1", resolve).once("error", reject));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("failed to allocate integration-test port");
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return address.port;
}

async function waitForServer(origin: string, process: ChildProcess, timeoutMs = 15_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastError = "health check was not attempted";
  while (Date.now() < deadline) {
    if (process.exitCode != null) throw new Error(`FastAPI integration server exited with ${process.exitCode}`);
    try {
      const response = await networkFetch(`${origin}/health/live`);
      if (response.ok) return;
      lastError = `health endpoint returned ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
      // Server startup is asynchronous; retry on the next short interval.
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`FastAPI integration server did not become ready within ${timeoutMs}ms: ${lastError}`);
}

function networkFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const target = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
  const requestHeaders = Object.fromEntries(new Headers(init.headers).entries());
  return new Promise<Response>((resolve, reject) => {
    const request = http.request({
      hostname: target.hostname,
      port: target.port,
      path: `${target.pathname}${target.search}`,
      method: init.method ?? "GET",
      headers: requestHeaders,
    }, (response) => {
      const chunks: Uint8Array[] = [];
      response.on("data", (chunk: Buffer) => chunks.push(chunk));
      response.on("end", () => {
        const headers = new Headers();
        for (const [name, value] of Object.entries(response.headers)) {
          if (value != null) headers.set(name, Array.isArray(value) ? value.join(", ") : value);
        }
        const result = new Response(Buffer.concat(chunks), { status: response.statusCode ?? 500, headers }) as IntegrationResponse;
        result.integrationSetCookie = response.headers["set-cookie"]?.[0];
        resolve(result);
      });
    });
    request.once("error", reject);
    request.setTimeout(1_000, () => request.destroy(new Error("HTTP request timed out")));
    if (typeof init.body === "string") request.write(init.body);
    request.end();
  });
}

function waitUntil<T>(subscribe: (resolve: (value: T) => void) => void, timeoutMs = 10_000): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("integration event timed out")), timeoutMs);
    subscribe((value) => { clearTimeout(timeout); resolve(value); });
  });
}

describe.sequential("real frontend API client to FastAPI integration", () => {
  const integrationUsername = process.env.PRO_NLP_INTEGRATION_USERNAME ?? "integration";
  const integrationPassword = process.env.PRO_NLP_INTEGRATION_PASSWORD ?? "integration-password";
  let serverProcess: ChildProcess;
  let origin = "";
  let cookie = "";
  let serverStdout = "";
  let serverStderr = "";

  beforeAll(async () => {
    const port = await freePort();
    origin = `http://127.0.0.1:${port}`;
    const repositoryRoot = path.resolve(process.cwd(), "..");
    const virtualEnvironmentPython = process.platform === "win32"
      ? path.join(repositoryRoot, ".venv", "Scripts", "python.exe")
      : path.join(repositoryRoot, ".venv", "bin", "python");
    const python = process.env.PRO_NLP_PYTHON ?? (existsSync(virtualEnvironmentPython) ? virtualEnvironmentPython : "python");
    const script = path.join(repositoryRoot, "tests", "support", "run_web_api_server.py");
    serverProcess = spawn(python, [script, String(port)], { cwd: repositoryRoot, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    serverProcess.stdout?.on("data", (chunk: Buffer) => { serverStdout += chunk.toString(); });
    serverProcess.stderr?.on("data", (chunk: Buffer) => { serverStderr += chunk.toString(); });
    try {
      await waitForServer(origin, serverProcess);
    } catch (error) {
      const serverOutput = [serverStdout, serverStderr].filter(Boolean).join("\n");
      throw new Error(`${error instanceof Error ? error.message : String(error)}${serverOutput ? `\n${serverOutput}` : ""}`, { cause: error });
    }

    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const target = typeof input === "string" && input.startsWith("/") ? `${origin}${input}` : input;
      const headers = new Headers(init.headers);
      headers.set("Origin", origin);
      if (cookie) headers.set("Cookie", cookie);
      const response = await networkFetch(target, { ...init, headers });
      const setCookie = (response as IntegrationResponse).integrationSetCookie ?? response.headers.get("set-cookie");
      if (setCookie) cookie = setCookie.split(";", 1)[0];
      return response;
    });
  }, 30_000);

  afterAll(async () => {
    vi.unstubAllGlobals();
    if (serverProcess.exitCode == null) {
      const exited = once(serverProcess, "exit");
      serverProcess.kill();
      await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 2_000))]);
    }
  });

  it("executes the real HTTP client against real authenticated FastAPI routes", async () => {
    const auth = await api.login(integrationUsername, integrationPassword);
    expect(auth.roles).toContain("developer");
    expect((await ensureAuth()).user_id).toBeTruthy();
    const workspaceId = auth.workspace_ids[0];
    if (!workspaceId) throw new Error("integration user has no authorized workspace");

    const session = await api.createSession(workspaceId);
    expect(session.workspace_id).toBe(workspaceId);
    expect((await api.listSessions()).items).toContainEqual(expect.objectContaining({ session_id: session.session_id }));

    expect((await api.updateSettings({ theme: "dark" })).settings.theme).toBe("dark");
    expect((await api.getSettings()).preferences.settings?.theme).toBe("dark");
    expect((await api.getTeacherCatalog(workspaceId)).catalog.workspace_id).toBe(workspaceId);

    const goals = await api.updateTeachingGoals(workspaceId, {
      course_title: "Transformer 专题",
      description: "真实 API 集成测试",
      objectives: ["解释注意力"],
      focus_topics: ["Transformer"],
      target_level: "intermediate",
    });
    expect(goals.goals.course_title).toBe("Transformer 专题");
    expect((await api.getTeacherResource("reports", workspaceId)).status).toBe("interface_reserved");
  });

  it("recovers exactly one turn when the socket drops after send and before ack", async () => {
    const auth = await ensureAuth();
    const workspaceId = auth.workspace_ids[0];
    if (!workspaceId) throw new Error("integration user has no authorized workspace");
    const session = await api.createSession(workspaceId);
    let dropFirstChat = true;
    let connectionCount = 0;
    let firstConnectionObservedAck = false;
    const sentChatRequestIds: string[] = [];

    class InterruptingWebSocket {
      static readonly CONNECTING = NetworkWebSocket.CONNECTING;
      static readonly OPEN = NetworkWebSocket.OPEN;
      private readonly socket: NetworkWebSocket;
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onclose: (() => void) | null = null;
      private readonly connectionNumber = ++connectionCount;

      constructor(url: string) {
        this.socket = new NetworkWebSocket(url, { headers: { Cookie: cookie, Origin: origin } });
        this.socket.on("open", () => this.onopen?.());
        this.socket.on("message", (data) => {
          const value = data.toString();
          const event = JSON.parse(value) as { type?: string; request_id?: string };
          if (this.connectionNumber === 1 && event.type === "command.ack" && event.request_id === "request_disconnect_1") {
            firstConnectionObservedAck = true;
          }
          this.onmessage?.({ data: value });
        });
        this.socket.on("close", () => this.onclose?.());
      }

      get readyState() { return this.socket.readyState; }

      send(value: string) {
        const frame = JSON.parse(value) as { type?: string; request_id?: string };
        if (frame.type === "chat.send" && frame.request_id) sentChatRequestIds.push(frame.request_id);
        this.socket.send(value, () => {
          if (dropFirstChat && frame.type === "chat.send") {
            dropFirstChat = false;
            this.socket.terminate();
          }
        });
      }

      close(code?: number, reason?: string) { this.socket.close(code, reason); }
    }

    vi.stubGlobal("WebSocket", InterruptingWebSocket);
    vi.stubGlobal("location", { protocol: "http:", host: new URL(origin).host });
    const completed = waitUntil<Record<string, unknown>>((resolve) => {
      const client = new StudentSocket(
        (event) => {
          if (event.type === "chat.completed") {
            resolve(event.payload);
            client.close();
          }
        },
        (status) => {
          if (status === "connected" && dropFirstChat) {
            client.setSession(session.session_id);
            client.sendChat(session.session_id, "integration disconnect", "request_disconnect_1");
          }
        },
      );
      client.connect();
    });

    expect(await completed).toMatchObject({ content: "answer:integration disconnect" });
    expect(firstConnectionObservedAck).toBe(false);
    expect(connectionCount).toBeGreaterThanOrEqual(2);
    expect(sentChatRequestIds).toEqual(["request_disconnect_1", "request_disconnect_1"]);
    const turns = (await api.listTurns(session.session_id)).items;
    expect(turns).toHaveLength(1);
    expect(turns[0]).toMatchObject({ status: "completed", final_text: "answer:integration disconnect" });
  }, 20_000);
});
