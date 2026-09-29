import { describe, expect, it } from "vitest";

import {
  clearWhiteboardScene,
  readWhiteboardScene,
  serializeWhiteboardScene,
  storageKeyForUser,
  writeWhiteboardScene,
} from "./storage";

describe("whiteboard local scene storage", () => {
  it("scopes the storage key to the signed-in user", () => {
    expect(storageKeyForUser("user/one")).toBe("nova.whiteboard.v1:user%2Fone");
    expect(storageKeyForUser("user/two")).not.toBe(storageKeyForUser("user/one"));
  });

  it("keeps scene elements, files, and only restorable app state", () => {
    const elements = [{ id: "rectangle-1", type: "rectangle" }] as never[];
    const files = { "file-1": { id: "file-1", mimeType: "image/png", dataURL: "data:image/png;base64,abc", created: 1 } } as never;
    const scene = serializeWhiteboardScene(elements, {
      theme: "dark",
      viewBackgroundColor: "#fff",
      scrollX: 12,
      scrollY: -4,
      selectedElementIds: { "rectangle-1": true },
      collaborators: new Map(),
    } as never, files);

    expect(scene.elements).toEqual(elements);
    expect(scene.files).toEqual(files);
    expect(scene.appState).toEqual({ theme: "dark", viewBackgroundColor: "#fff", scrollX: 12, scrollY: -4 });
    expect(scene.appState).not.toHaveProperty("collaborators");
  });

  it("round-trips a valid scene and ignores malformed local data", () => {
    const local = new Map<string, string>();
    const storage = {
      getItem: (key: string) => local.get(key) ?? null,
      setItem: (key: string, value: string) => local.set(key, value),
      removeItem: (key: string) => local.delete(key),
    } as unknown as Storage;
    const scene = serializeWhiteboardScene([{ id: "text-1", type: "text" }] as never[], {} as never, {});

    expect(writeWhiteboardScene("student-1", scene, storage)).toBe(true);
    expect(readWhiteboardScene("student-1", storage)).toEqual(scene);
    expect(readWhiteboardScene("student-2", storage)).toBeNull();

    local.set(storageKeyForUser("student-1"), "not-json");
    expect(readWhiteboardScene("student-1", storage)).toBeNull();

    expect(writeWhiteboardScene("student-1", scene, storage)).toBe(true);
    clearWhiteboardScene("student-1", storage);
    expect(readWhiteboardScene("student-1", storage)).toBeNull();
  });

  it("reports when the browser rejects a scene write", () => {
    const storage = {
      getItem: () => null,
      setItem: () => { throw new Error("quota exceeded"); },
      removeItem: () => undefined,
    } as unknown as Storage;
    const scene = serializeWhiteboardScene([], {} as never, {});

    expect(writeWhiteboardScene("student-1", scene, storage)).toBe(false);
  });
});
