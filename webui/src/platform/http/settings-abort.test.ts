import { api, AUTH_REQUIRED_EVENT } from "./api";

it("does not log out the new account when an aborted settings 401 finishes parsing", async () => {
  const controller = new AbortController();
  let parsed!: (value: object) => void;
  const response = new Response("{}", { status: 401 });
  vi.spyOn(response, "json").mockReturnValue(new Promise((resolve) => { parsed = resolve; }));
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
  const authRequired = vi.fn();
  window.addEventListener(AUTH_REQUIRED_EVENT, authRequired);
  const request = api.getSettings(controller.signal);
  const outcome = request.catch((error: Error) => error);
  await Promise.resolve();
  controller.abort();
  parsed({ code: "session_expired" });
  const result = await outcome;
  expect(result).toBeInstanceOf(DOMException);
  expect(authRequired).not.toHaveBeenCalled();
  window.removeEventListener(AUTH_REQUIRED_EVENT, authRequired);
  fetchMock.mockRestore();
});
