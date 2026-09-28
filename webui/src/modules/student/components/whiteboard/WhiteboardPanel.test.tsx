import { readFileSync } from "node:fs";

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const excalidraw = vi.hoisted(() => ({
  render: vi.fn(),
  loadLibraryFromBlob: vi.fn().mockResolvedValue([]),
}));

const whiteboardApi = vi.hoisted(() => ({
  getWhiteboardLibrary: vi.fn().mockResolvedValue({ items: [] }),
  createWhiteboardLibraryItem: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({ api: whiteboardApi }));

vi.mock("@excalidraw/excalidraw", () => ({
  CaptureUpdateAction: { NEVER: "NEVER" },
  loadLibraryFromBlob: excalidraw.loadLibraryFromBlob,
  Excalidraw: (props: { initialData?: unknown; onChange?: (elements: unknown, appState: unknown, files: unknown) => void; onLibraryChange?: (items: unknown[]) => void | Promise<unknown>; validateEmbeddable?: (link: string) => boolean | undefined; children?: React.ReactNode }) => {
    excalidraw.render(props);
    return <><button type="button" onClick={() => props.onChange?.([{ id: "line-1", type: "line" }] as never, { theme: "light", viewBackgroundColor: "#fff" } as never, {})}>模拟绘图</button>{props.children}</>;
  },
  MainMenu: Object.assign(({ children }: { children?: React.ReactNode }) => <nav data-testid="whiteboard-main-menu">{children}</nav>, {
    Item: ({ children, ...props }: { children?: React.ReactNode; onClick?: () => void }) => <button type="button" {...props}>{children}</button>,
    DefaultItems: {
      LoadScene: () => null,
      SaveToActiveFile: () => null,
      Export: () => null,
      SaveAsImage: () => null,
      SearchMenu: () => null,
      Help: () => <span data-testid="whiteboard-help-menu-item" />,
      ClearCanvas: () => null,
      ToggleTheme: () => null,
      ChangeCanvasBackground: () => null,
      Socials: () => <span data-testid="whiteboard-excalidraw-links" />,
    },
    Separator: () => null,
  }),
  Footer: ({ children }: { children?: React.ReactNode }) => <footer data-testid="whiteboard-footer">{children}</footer>,
}));

import { WhiteboardPanel } from "./WhiteboardPanel";
import { WHITEBOARD_LIBRARY_ASSETS } from "./libraryAssets";
import { storageKeyForUser } from "./storage";
import { resetBundledLibrariesCache } from "./ExcalidrawAdapter";

describe("WhiteboardPanel", () => {
  beforeEach(() => {
    localStorage.clear();
    excalidraw.render.mockClear();
    excalidraw.loadLibraryFromBlob.mockClear();
    excalidraw.loadLibraryFromBlob.mockResolvedValue([]);
    whiteboardApi.getWhiteboardLibrary.mockReset();
    whiteboardApi.getWhiteboardLibrary.mockResolvedValue({ items: [] });
    whiteboardApi.createWhiteboardLibraryItem.mockReset();
    resetBundledLibrariesCache();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("loads the current user's local scene into the embedded engine", () => {
    localStorage.setItem(storageKeyForUser("student-1"), JSON.stringify({
      schemaVersion: 1,
      elements: [{ id: "saved-1", type: "rectangle" }],
      appState: { theme: "dark" },
      files: {},
    }));

    render(<WhiteboardPanel userId="student-1" />);

    expect(excalidraw.render).toHaveBeenCalledWith(expect.objectContaining({
      initialData: expect.objectContaining({ elements: [{ id: "saved-1", type: "rectangle" }] }),
      langCode: "zh-CN",
      aiEnabled: false,
    }));
    const props = excalidraw.render.mock.calls.at(-1)?.[0] as { validateEmbeddable?: (link: string) => boolean | undefined };
    expect(props.validateEmbeddable?.("https://example.com")).toBe(false);
  });

  it("filters embeddable elements from a restored local scene", () => {
    const onSceneChange = vi.fn();
    localStorage.setItem(storageKeyForUser("student-1"), JSON.stringify({
      schemaVersion: 1,
      elements: [
        { id: "embed-1", type: "embeddable" },
        { id: "saved-1", type: "rectangle" },
      ],
      appState: { theme: "dark" },
      files: {},
    }));

    render(<WhiteboardPanel userId="student-1" onSceneChange={onSceneChange} />);

    expect(excalidraw.render.mock.calls.at(-1)?.[0]).toEqual(expect.objectContaining({
      initialData: expect.objectContaining({
        elements: [{ id: "saved-1", type: "rectangle" }],
      }),
    }));
    expect(onSceneChange).toHaveBeenCalledWith(expect.objectContaining({
      elements: [{ id: "saved-1", type: "rectangle" }],
    }));
  });

  it("reloads the scene when the signed-in user changes", () => {
    localStorage.setItem(storageKeyForUser("student-1"), JSON.stringify({
      schemaVersion: 1,
      elements: [{ id: "saved-1", type: "rectangle" }],
      appState: { theme: "light" },
      files: {},
    }));
    localStorage.setItem(storageKeyForUser("student-2"), JSON.stringify({
      schemaVersion: 1,
      elements: [{ id: "saved-2", type: "ellipse" }],
      appState: { theme: "dark" },
      files: {},
    }));

    const view = render(<WhiteboardPanel userId="student-1" />);
    view.rerender(<WhiteboardPanel userId="student-2" />);

    expect(excalidraw.render.mock.calls.at(-1)?.[0]).toEqual(expect.objectContaining({
      initialData: expect.objectContaining({ elements: [{ id: "saved-2", type: "ellipse" }] }),
    }));
  });

  it("shows only supported shortcuts in the in-product help", () => {
    render(<WhiteboardPanel userId="student-1" />);

    expect(screen.getByTestId("whiteboard-main-menu")).toBeInTheDocument();
    const helpButtons = screen.getAllByRole("button", { name: "帮助" });
    expect(helpButtons).toHaveLength(2);
    helpButtons[0].focus();
    fireEvent.click(helpButtons[0]);
    const dialog = screen.getByRole("dialog", { name: "白板快捷键" });
    const closeButton = screen.getByRole("button", { name: "关闭帮助" });
    expect(dialog).toBeInTheDocument();
    expect(closeButton).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(closeButton).toHaveFocus();
    expect(screen.getByText("选择工具")).toBeInTheDocument();
    expect(screen.getByText("撤销")).toBeInTheDocument();
    expect(screen.getByText("重做")).toBeInTheDocument();
    expect(screen.getByText("Ctrl/Cmd + +")).toBeInTheDocument();
    expect(screen.queryByText("+ / - 或滚轮")).not.toBeInTheDocument();
    expect(screen.queryByText("Crop image")).not.toBeInTheDocument();
    expect(screen.queryByText("Create a flowchart from a generic element")).not.toBeInTheDocument();
    expect(screen.queryByTestId("whiteboard-excalidraw-links")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "关闭帮助" }));
    expect(helpButtons[0]).toHaveFocus();
  });

  it("routes the help shortcut to the filtered in-product dialog", () => {
    render(<WhiteboardPanel userId="student-1" />);

    fireEvent.keyDown(screen.getByRole("region", { name: "白板绘图" }), { key: "?", shiftKey: true });

    expect(screen.getByRole("dialog", { name: "白板快捷键" })).toBeInTheDocument();
    expect(screen.queryByText("Crop image")).not.toBeInTheDocument();
  });

  it("hides only the embed action in Excalidraw's extra-tools menu", () => {
    const style = document.createElement("style");
    style.textContent = readFileSync("src/modules/student/components/whiteboard/whiteboard.css", "utf8");
    document.head.appendChild(style);

    const panel = document.createElement("section");
    panel.className = "whiteboard-panel";
    panel.innerHTML = `
      <div class="App-toolbar__extra-tools-dropdown">
        <div class="dropdown-menu-container">
          <button data-testid="toolbar-frame"></button>
          <button data-testid="toolbar-embeddable"></button>
          <button data-testid="toolbar-laser"></button>
          <div>Generate</div>
          <button data-testid="toolbar-embeddable"></button>
        </div>
      </div>`;
    document.body.appendChild(panel);

    const [embed, mermaid] = panel.querySelectorAll<HTMLButtonElement>('[data-testid="toolbar-embeddable"]');
    expect(getComputedStyle(embed).display).toBe("none");
    expect(getComputedStyle(mermaid).display).not.toBe("none");

    panel.remove();
    style.remove();
  });

  it("hides personal library items even before bundled items finish loading", () => {
    const style = document.createElement("style");
    style.textContent = readFileSync("src/modules/student/components/whiteboard/whiteboard.css", "utf8");
    document.head.appendChild(style);

    const panel = document.createElement("section");
    panel.className = "whiteboard-panel";
    panel.innerHTML = `
      <div class="library-menu-control-buttons" data-testid="library-controls"></div>
      <div class="library-menu-dropdown-container" data-testid="library-dropdown"></div>
      <div class="library-menu-items-container__items">
        <div class="library-menu-items-container__header">个人素材库</div>
        <div class="library-menu-items-container__grid" data-testid="personal-grid"></div>
        <div class="library-menu-items-container__header library-menu-items-container__header--excal">Excalidraw</div>
        <div class="library-menu-items-container__grid" data-testid="bundled-grid"></div>
        <input class="library-unit__checkbox" data-testid="library-checkbox" />
      </div>`;
    document.body.appendChild(panel);

    const contextMenu = document.createElement("ul");
    contextMenu.className = "context-menu";
    contextMenu.innerHTML = '<li data-testid="addToLibrary">加入素材库</li>';
    document.body.appendChild(contextMenu);

    expect(getComputedStyle(panel.querySelector('[data-testid="personal-grid"]')!).display).toBe("none");
    expect(getComputedStyle(panel.querySelector('[data-testid="bundled-grid"]')!).display).not.toBe("none");
    expect(getComputedStyle(panel.querySelector('[data-testid="library-controls"]')!).display).toBe("none");
    expect(getComputedStyle(panel.querySelector('[data-testid="library-dropdown"]')!).display).toBe("none");
    expect(getComputedStyle(panel.querySelector('[data-testid="library-checkbox"]')!).display).toBe("none");
    expect(getComputedStyle(contextMenu.querySelector('[data-testid="addToLibrary"]')!).display).toBe("none");

    panel.remove();
    contextMenu.remove();
    style.remove();
  });

  it("exposes native library creation only to users who can manage the global library", () => {
    const style = document.createElement("style");
    style.textContent = readFileSync("src/modules/student/components/whiteboard/whiteboard.css", "utf8");
    document.head.appendChild(style);

    const panel = document.createElement("section");
    panel.className = "whiteboard-panel whiteboard-can-manage-library";
    panel.innerHTML = `
      <div class="library-menu-control-buttons" data-testid="library-controls"></div>
      <div class="library-menu-dropdown-container" data-testid="library-dropdown"></div>
      <input class="library-unit__checkbox" data-testid="library-checkbox" />`;
    document.body.appendChild(panel);

    const contextMenu = document.createElement("ul");
    contextMenu.className = "context-menu";
    contextMenu.innerHTML = '<li data-testid="addToLibrary">加入素材库</li>';
    document.body.appendChild(contextMenu);
    document.body.classList.add("whiteboard-library-manager");

    expect(getComputedStyle(panel.querySelector('[data-testid="library-controls"]')!).display).not.toBe("none");
    expect(getComputedStyle(panel.querySelector('[data-testid="library-dropdown"]')!).display).not.toBe("none");
    expect(getComputedStyle(panel.querySelector('[data-testid="library-checkbox"]')!).display).not.toBe("none");
    expect(getComputedStyle(contextMenu.querySelector('[data-testid="addToLibrary"]')!).display).not.toBe("none");

    panel.remove();
    contextMenu.remove();
    document.body.classList.remove("whiteboard-library-manager");
    style.remove();
  });

  it("publishes a newly created library item and makes it shared", async () => {
    vi.useRealTimers();
    const updateLibrary = vi.fn().mockResolvedValue([]);
    const createdItem = {
      id: "library-1",
      status: "published",
      created: 123,
      name: "我的素材",
      elements: [{ id: "shape-1", type: "rectangle" }],
    };
    whiteboardApi.createWhiteboardLibraryItem.mockResolvedValue({ item: createdItem });
    vi.stubGlobal("prompt", vi.fn().mockReturnValue("自定义素材"));
    const blob = new Blob([JSON.stringify({ type: "excalidrawlib", version: 2, libraryItems: [] })], { type: "application/json" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(blob) }));

    render(<WhiteboardPanel userId="teacher-1" canManageLibrary />);
    const props = excalidraw.render.mock.calls.at(-1)?.[0] as {
      excalidrawAPI?: (api: { updateLibrary: typeof updateLibrary }) => void;
      onLibraryChange?: (items: unknown[]) => void | Promise<unknown>;
    };
    props.excalidrawAPI?.({ updateLibrary });
    await waitFor(() => expect(updateLibrary).toHaveBeenCalled());

    await props.onLibraryChange?.([{
      id: "library-1",
      status: "unpublished",
      created: 123,
      elements: [{ id: "shape-1", type: "rectangle" }],
    }]);

    expect(whiteboardApi.createWhiteboardLibraryItem).toHaveBeenCalledWith(
      "自定义素材",
      [{ id: "shape-1", type: "rectangle" }],
    );
    const publishOptions = updateLibrary.mock.calls.at(-1)?.[0] as {
      merge?: boolean;
      libraryItems?: (currentItems: Array<{ id: string }>) => unknown;
    };
    expect(publishOptions.merge).toBe(false);
    expect(publishOptions.libraryItems?.([{ id: "library-1" }])).toEqual([createdItem]);
  });

  it("shows a visible error when publishing a library item fails", async () => {
    vi.useRealTimers();
    const updateLibrary = vi.fn().mockResolvedValue([]);
    whiteboardApi.createWhiteboardLibraryItem.mockRejectedValue(new Error("network failure"));
    vi.stubGlobal("prompt", vi.fn().mockReturnValue("失败素材"));
    const blob = new Blob([JSON.stringify({ type: "excalidrawlib", version: 2, libraryItems: [] })], { type: "application/json" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(blob) }));

    render(<WhiteboardPanel userId="teacher-1" canManageLibrary />);
    const props = excalidraw.render.mock.calls.at(-1)?.[0] as {
      excalidrawAPI?: (api: { updateLibrary: typeof updateLibrary }) => void;
      onLibraryChange?: (items: unknown[]) => void | Promise<unknown>;
    };
    props.excalidrawAPI?.({ updateLibrary });
    await waitFor(() => expect(updateLibrary).toHaveBeenCalled());

    await props.onLibraryChange?.([{
      id: "library-failed",
      status: "unpublished",
      created: 123,
      elements: [{ id: "shape-1", type: "rectangle" }],
    }]);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("暂未全局共享"));
  });

  it("ships stable source metadata for bundled libraries", () => {
    for (const asset of WHITEBOARD_LIBRARY_ASSETS) {
      const contents = readFileSync(`public/excalidraw/libraries/${asset.fileName}`, "utf8");
      expect(contents).not.toContain("localhost");
    }
  });

  it("loads the bundled teaching libraries through the Excalidraw API", async () => {
    vi.useRealTimers();
    const updateLibrary = vi.fn().mockResolvedValue([]);
    const blob = new Blob([JSON.stringify({ type: "excalidrawlib", version: 2, libraryItems: [] })], { type: "application/json" });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(blob) });
    vi.stubGlobal("fetch", fetchMock);

    render(<WhiteboardPanel userId="student-1" />);
    const props = excalidraw.render.mock.calls.at(-1)?.[0] as { excalidrawAPI?: (api: { updateLibrary: typeof updateLibrary }) => void };
    props.excalidrawAPI?.({ updateLibrary });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await waitFor(() => expect(updateLibrary).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledTimes(WHITEBOARD_LIBRARY_ASSETS.length);
    expect(excalidraw.loadLibraryFromBlob).toHaveBeenCalledTimes(WHITEBOARD_LIBRARY_ASSETS.length);
    expect(updateLibrary).toHaveBeenCalledTimes(WHITEBOARD_LIBRARY_ASSETS.length + 1);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(expect.arrayContaining([
      expect.stringContaining("deep-learning.excalidrawlib"),
      expect.stringContaining("data-processing.excalidrawlib"),
      expect.stringContaining("mathematical-symbols.excalidrawlib"),
      expect.stringContaining("flow-chart-symbols.excalidrawlib"),
      expect.stringContaining("montessori-basic-grammar-symbols.excalidrawlib"),
      expect.stringContaining("bubbles.excalidrawlib"),
    ]));
    expect(updateLibrary).toHaveBeenNthCalledWith(1, expect.objectContaining({ libraryItems: [], merge: false, defaultStatus: "published" }));
    expect(updateLibrary.mock.calls.slice(1).every(([options]) => options.merge === true && options.defaultStatus === "published")).toBe(true);

    const secondUpdateLibrary = vi.fn().mockResolvedValue([]);
    render(<WhiteboardPanel userId="student-2" />);
    const secondProps = excalidraw.render.mock.calls.at(-1)?.[0] as { excalidrawAPI?: (api: { updateLibrary: typeof secondUpdateLibrary }) => void };
    secondProps.excalidrawAPI?.({ updateLibrary: secondUpdateLibrary });
    await waitFor(() => expect(secondUpdateLibrary).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledTimes(WHITEBOARD_LIBRARY_ASSETS.length);
    expect(excalidraw.loadLibraryFromBlob).toHaveBeenCalledTimes(WHITEBOARD_LIBRARY_ASSETS.length);
  });

  it("loads server-created library items for every signed-in user", async () => {
    vi.useRealTimers();
    const updateLibrary = vi.fn().mockResolvedValue([]);
    const sharedItem = {
      id: "shared-1",
      status: "published" as const,
      created: 456,
      name: "全局流程",
      elements: [{ id: "shape-1", type: "rectangle" }],
    };
    whiteboardApi.getWhiteboardLibrary.mockResolvedValue({ items: [sharedItem] });
    const blob = new Blob([JSON.stringify({ type: "excalidrawlib", version: 2, libraryItems: [] })], { type: "application/json" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(blob) }));

    render(<WhiteboardPanel userId="student-1" />);
    const props = excalidraw.render.mock.calls.at(-1)?.[0] as { excalidrawAPI?: (api: { updateLibrary: typeof updateLibrary }) => void };
    props.excalidrawAPI?.({ updateLibrary });

    await waitFor(() => expect(updateLibrary).toHaveBeenCalledWith(expect.objectContaining({
      libraryItems: [sharedItem],
      merge: true,
      defaultStatus: "published",
    })));
  });

  it("keeps library installation active under StrictMode effect replay", async () => {
    vi.useRealTimers();
    const updateLibrary = vi.fn().mockResolvedValue([]);
    const blob = new Blob([JSON.stringify({ type: "excalidrawlib", version: 2, libraryItems: [] })], { type: "application/json" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(blob) }));

    render(<StrictMode><WhiteboardPanel userId="student-1" /></StrictMode>);
    const props = excalidraw.render.mock.calls.at(-1)?.[0] as { excalidrawAPI?: (api: { updateLibrary: typeof updateLibrary }) => void };
    props.excalidrawAPI?.({ updateLibrary });

    await waitFor(() => expect(updateLibrary).toHaveBeenCalled());
  });

  it("recovers the bundled library when clearing the engine library initially fails", async () => {
    vi.useRealTimers();
    const updateLibrary = vi.fn()
      .mockRejectedValueOnce(new Error("library unavailable"))
      .mockResolvedValue([]);
    const blob = new Blob([JSON.stringify({ type: "excalidrawlib", version: 2, libraryItems: [] })], { type: "application/json" });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(blob) });
    vi.stubGlobal("fetch", fetchMock);

    render(<WhiteboardPanel userId="student-1" />);
    const props = excalidraw.render.mock.calls.at(-1)?.[0] as { excalidrawAPI?: (api: { updateLibrary: typeof updateLibrary }) => void };
    props.excalidrawAPI?.({ updateLibrary });

    await waitFor(() => expect(screen.getByText("白板素材区初始化失败，已尽力恢复，请刷新白板后重试。")).toBeInTheDocument());
    expect(updateLibrary).toHaveBeenCalledTimes(WHITEBOARD_LIBRARY_ASSETS.length + 1);
    expect(fetchMock).toHaveBeenCalledTimes(WHITEBOARD_LIBRARY_ASSETS.length);
    expect(updateLibrary.mock.calls[1]?.[0]).toEqual(expect.objectContaining({ merge: false }));
  });

  it("retries bundled library loading after a failed attempt", async () => {
    vi.useRealTimers();
    const updateLibrary = vi.fn().mockResolvedValue([]);
    const blob = new Blob([JSON.stringify({ type: "excalidrawlib", version: 2, libraryItems: [] })], { type: "application/json" });
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue({ ok: true, blob: () => Promise.resolve(blob) });
    vi.stubGlobal("fetch", fetchMock);

    render(<WhiteboardPanel userId="student-1" />);
    const firstProps = excalidraw.render.mock.calls.at(-1)?.[0] as { excalidrawAPI?: (api: { updateLibrary: typeof updateLibrary }) => void };
    firstProps.excalidrawAPI?.({ updateLibrary });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(WHITEBOARD_LIBRARY_ASSETS.length));

    const secondUpdateLibrary = vi.fn().mockResolvedValue([]);
    render(<WhiteboardPanel userId="student-2" />);
    const secondProps = excalidraw.render.mock.calls.at(-1)?.[0] as { excalidrawAPI?: (api: { updateLibrary: typeof secondUpdateLibrary }) => void };
    secondProps.excalidrawAPI?.({ updateLibrary: secondUpdateLibrary });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(WHITEBOARD_LIBRARY_ASSETS.length * 2));
    expect(secondUpdateLibrary).toHaveBeenCalled();
    expect(screen.getByText("部分教学素材加载失败，请刷新白板后重试。")).toBeInTheDocument();
  });

  it("clears a previous user's library warning when switching accounts", async () => {
    vi.useRealTimers();
    const fetchMock = vi.fn().mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);

    const view = render(<WhiteboardPanel userId="student-1" />);
    const firstProps = excalidraw.render.mock.calls.at(-1)?.[0] as { excalidrawAPI?: (api: { updateLibrary: ReturnType<typeof vi.fn> }) => void };
    firstProps.excalidrawAPI?.({ updateLibrary: vi.fn().mockResolvedValue([]) });
    await waitFor(() => expect(screen.getByText("部分教学素材加载失败，请刷新白板后重试。")).toBeInTheDocument());

    view.rerender(<WhiteboardPanel userId="student-2" />);

    expect(screen.queryByText("部分教学素材加载失败，请刷新白板后重试。")).not.toBeInTheDocument();
  });

  it("does not install libraries after the adapter unmounts", async () => {
    vi.useRealTimers();
    let resolveFetch: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn().mockImplementation(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    const updateLibrary = vi.fn().mockResolvedValue([]);

    const view = render(<WhiteboardPanel userId="student-1" />);
    const props = excalidraw.render.mock.calls.at(-1)?.[0] as { excalidrawAPI?: (api: { updateLibrary: typeof updateLibrary }) => void };
    props.excalidrawAPI?.({ updateLibrary });
    view.unmount();
    resolveFetch?.({ ok: true, blob: () => Promise.resolve(new Blob(["{}"], { type: "application/json" })) } as Response);
    await Promise.resolve();
    await Promise.resolve();

    expect(updateLibrary).toHaveBeenCalledTimes(1);
  });

  it("writes scene changes to local storage for that user", () => {
    render(<WhiteboardPanel userId="student-1" />);

    fireEvent.click(screen.getByRole("button", { name: "模拟绘图" }));
    act(() => vi.advanceTimersByTime(250));

    const stored = JSON.parse(localStorage.getItem(storageKeyForUser("student-1")) ?? "null") as { elements: Array<{ id: string }> };
    expect(stored.elements).toEqual([{ id: "line-1", type: "line" }]);
    expect(localStorage.getItem(storageKeyForUser("student-2"))).toBeNull();
  });

  it("flushes the latest scene when the page is hidden", () => {
    render(<WhiteboardPanel userId="student-1" />);

    fireEvent.click(screen.getByRole("button", { name: "模拟绘图" }));
    act(() => window.dispatchEvent(new Event("pagehide")));

    const stored = JSON.parse(localStorage.getItem(storageKeyForUser("student-1")) ?? "null") as { elements: Array<{ id: string }> };
    expect(stored.elements).toEqual([{ id: "line-1", type: "line" }]);
  });

  it("shows a backup warning when the page-hide flush fails", () => {
    const setItem = vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });

    render(<WhiteboardPanel userId="student-1" />);
    fireEvent.click(screen.getByRole("button", { name: "模拟绘图" }));
    act(() => window.dispatchEvent(new Event("pagehide")));

    expect(screen.getByRole("alert")).toHaveTextContent("本地保存失败");
    setItem.mockRestore();
  });

  it("shows a backup warning when the browser rejects a scene write", () => {
    const setItem = vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });

    render(<WhiteboardPanel userId="student-1" />);
    fireEvent.click(screen.getByRole("button", { name: "模拟绘图" }));
    act(() => vi.advanceTimersByTime(250));

    expect(screen.getByRole("alert")).toHaveTextContent("本地保存失败");
    setItem.mockRestore();
  });

  it("does not mount an editable board for an unauthenticated user", () => {
    render(<WhiteboardPanel userId={null} />);

    expect(excalidraw.render).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("removes embeddable elements from imported scene changes", () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["{}"], { type: "application/json" })) }));
    const updateScene = vi.fn();
    const onSceneChange = vi.fn();
    render(<WhiteboardPanel userId="student-1" onSceneChange={onSceneChange} />);
    const props = excalidraw.render.mock.calls.at(-1)?.[0] as {
      onChange?: (elements: unknown, appState: unknown, files: unknown) => void;
      excalidrawAPI?: (api: { updateScene: typeof updateScene; updateLibrary: ReturnType<typeof vi.fn> }) => void;
    };
    props.excalidrawAPI?.({ updateScene, updateLibrary: vi.fn().mockResolvedValue([]) });
    props.onChange?.([
      { id: "embed-1", type: "embeddable" },
      { id: "line-1", type: "line" },
    ], { theme: "light" }, {});

    expect(updateScene).toHaveBeenCalledWith(expect.objectContaining({
      elements: [{ id: "line-1", type: "line" }],
      captureUpdate: "NEVER",
    }));
    expect(onSceneChange).toHaveBeenLastCalledWith(expect.objectContaining({
      elements: [{ id: "line-1", type: "line" }],
    }));
  });

  it("forwards structured scene changes to page-level actions", () => {
    const onSceneChange = vi.fn();

    render(<WhiteboardPanel userId="student-1" onSceneChange={onSceneChange} />);
    fireEvent.click(screen.getByRole("button", { name: "模拟绘图" }));

    expect(onSceneChange).toHaveBeenCalledWith(expect.objectContaining({
      schemaVersion: 1,
      elements: [{ id: "line-1", type: "line" }],
      files: {},
    }));
  });

  it("forwards a restored scene before the next edit", () => {
    const onSceneChange = vi.fn();
    const savedScene = {
      schemaVersion: 1,
      elements: [{ id: "saved-1", type: "rectangle" }],
      appState: { theme: "dark" },
      files: {},
    };
    localStorage.setItem(storageKeyForUser("student-1"), JSON.stringify(savedScene));

    render(<WhiteboardPanel userId="student-1" onSceneChange={onSceneChange} />);

    expect(onSceneChange).toHaveBeenCalledWith(savedScene);
  });
});
