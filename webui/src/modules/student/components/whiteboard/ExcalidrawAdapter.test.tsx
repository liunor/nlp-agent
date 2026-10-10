import { useEffect, type ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/platform/http/api";
import { ExcalidrawAdapter } from "./ExcalidrawAdapter";

const initialLibrary = [{
  id: "legacy-1",
  status: "published" as const,
  created: 1,
  elements: [{ id: "element-1", type: "rectangle" }],
}];

const updateLibrary = vi.fn(async ({ libraryItems, merge = true }: { libraryItems: unknown[] | ((items: typeof initialLibrary) => unknown[]); merge?: boolean }) => {
  const next = typeof libraryItems === "function" ? libraryItems(initialLibrary) : libraryItems;
  return (merge ? [...initialLibrary, ...next] : next) as typeof initialLibrary;
});
let fakeSceneElements: unknown[] = [];
let resetPresentedSceneAfterFirstWrite = false;
let restorePartialPresentationAfterFirstWrite = false;
const updateScene = vi.fn((payload: { elements?: unknown[] }) => {
  if (!payload.elements) return;
  fakeSceneElements = payload.elements;
  if (resetPresentedSceneAfterFirstWrite && payload.elements.some((element) => (element as { customData?: { whiteboardAssetId?: string } }).customData?.whiteboardAssetId)) {
    resetPresentedSceneAfterFirstWrite = false;
    window.setTimeout(() => { fakeSceneElements = []; }, 0);
  }
  if (restorePartialPresentationAfterFirstWrite && payload.elements.some((element) => (element as { customData?: { whiteboardAssetId?: string } }).customData?.whiteboardAssetId)) {
    restorePartialPresentationAfterFirstWrite = false;
    window.setTimeout(() => {
      fakeSceneElements = payload.elements?.filter((element) => {
        const candidate = element as { customData?: { whiteboardAssetId?: string }; type?: string };
        return candidate.customData?.whiteboardAssetId !== "asset-partial" || candidate.type !== "text";
      }) ?? [];
    }, 0);
  }
});
const scrollToContent = vi.fn();
const { restoreElements } = vi.hoisted(() => ({
  restoreElements: vi.fn((elements: Array<Record<string, unknown>>) => elements.map((element) => element.type === "text"
    ? { ...element, lineHeight: element.lineHeight ?? 1.25, autoResize: element.autoResize ?? true }
    : element)),
}));

vi.mock("@excalidraw/excalidraw", () => {
  function FakeExcalidraw({ children, excalidrawAPI, onLibraryChange }: { children?: ReactNode; excalidrawAPI: (value: unknown) => void; onLibraryChange: (items: typeof initialLibrary) => void }) {
    useEffect(() => {
      onLibraryChange(initialLibrary);
      excalidrawAPI({
        updateLibrary,
        updateScene,
        scrollToContent,
        getSceneElements: () => fakeSceneElements,
        getAppState: () => ({ zoom: { value: 1 }, scrollX: 0, scrollY: 0 }),
      });
    }, [excalidrawAPI, onLibraryChange]);
    return <div data-testid="fake-excalidraw"><button type="button" className="library-unit library-unit__active">素材</button>{children}</div>;
  }
  const passthrough = ({ children }: { children?: ReactNode }) => <>{children}</>;
  const defaults = { LoadScene: passthrough, SaveToActiveFile: passthrough, Export: passthrough, SaveAsImage: passthrough, SearchMenu: passthrough, ClearCanvas: passthrough, ToggleTheme: passthrough, ChangeCanvasBackground: passthrough };
  return { CaptureUpdateAction: { IMMEDIATELY: "immediately", NEVER: "never" }, Excalidraw: FakeExcalidraw, Footer: passthrough, MainMenu: Object.assign(passthrough, { DefaultItems: defaults, Separator: passthrough, Item: passthrough }), restoreElements };
});

vi.mock("@/platform/http/api", () => ({
  api: {
    getWhiteboardLibrary: vi.fn().mockRejectedValue(new Error("HTTP 502")),
  },
}));

describe("ExcalidrawAdapter shared library loading", () => {
  beforeEach(() => {
    updateLibrary.mockClear();
    updateScene.mockClear();
    scrollToContent.mockClear();
    restoreElements.mockClear();
    fakeSceneElements = [];
    resetPresentedSceneAfterFirstWrite = false;
    restorePartialPresentationAfterFirstWrite = false;
  });

  it("does not clear the local library before a failed shared-library request", async () => {
    render(<ExcalidrawAdapter initialScene={null} onChange={vi.fn()} canManageLibrary />);

    await waitFor(() => expect(api.getWhiteboardLibrary).toHaveBeenCalled());
    const getOrder = vi.mocked(api.getWhiteboardLibrary).mock.invocationCallOrder[0];
    const clearOrders = updateLibrary.mock.invocationCallOrder.filter((_, index) => updateLibrary.mock.calls[index]?.[0]?.merge === false);
    expect(clearOrders).toHaveLength(1);
    expect(clearOrders[0]).toBeGreaterThan(getOrder);
  });

  it("presents a pending whiteboard request after the Excalidraw API becomes ready", async () => {
    render(<ExcalidrawAdapter
      initialScene={null}
      onChange={vi.fn()}
      presentRequest={{
        requestId: "request-1",
        assetId: "asset-1",
        name: "注意力计算过程",
        elements: [{ id: "element-1", type: "rectangle", x: 0, y: 0, width: 40, height: 20 }],
      }}
    />);

    await waitFor(() => expect(updateScene).toHaveBeenCalledWith(expect.objectContaining({
      elements: expect.arrayContaining([expect.objectContaining({ customData: expect.objectContaining({ whiteboardAssetId: "asset-1" }) })]),
    })));
    await waitFor(() => expect(scrollToContent).toHaveBeenCalled());
  });

  it("retries presentation when initial scene restoration overwrites the first update", async () => {
    resetPresentedSceneAfterFirstWrite = true;
    render(<ExcalidrawAdapter
      initialScene={null}
      onChange={vi.fn()}
      presentRequest={{
        requestId: "request-after-restore",
        assetId: "asset-restore",
        name: "注意力计算过程",
        elements: [{ id: "element-restore", type: "rectangle", x: 0, y: 0, width: 40, height: 20 }],
      }}
    />);

    await waitFor(() => expect(updateScene.mock.calls.length).toBeGreaterThanOrEqual(2), { timeout: 1000 });
    expect(fakeSceneElements).toEqual(expect.arrayContaining([
      expect.objectContaining({ customData: expect.objectContaining({ whiteboardAssetId: "asset-restore" }) }),
    ]));
  });

  it("replaces a partially restored presentation before considering it complete", async () => {
    restorePartialPresentationAfterFirstWrite = true;
    render(<ExcalidrawAdapter
      initialScene={null}
      onChange={vi.fn()}
      presentRequest={{
        requestId: "request-partial-restore",
        assetId: "asset-partial",
        name: "注意力计算过程",
        elements: [
          { id: "element-rectangle", type: "rectangle", x: 0, y: 0, width: 40, height: 20 },
          { id: "element-text", type: "text", x: 0, y: 0, width: 40, height: 20, text: "Matmul" },
        ],
      }}
    />);

    await waitFor(() => expect(updateScene.mock.calls.length).toBeGreaterThanOrEqual(2), { timeout: 1000 });
    await waitFor(() => expect(fakeSceneElements.filter((element) => {
      const candidate = element as { customData?: { whiteboardAssetId?: string } };
      return candidate.customData?.whiteboardAssetId === "asset-partial";
    })).toHaveLength(2));
  });

  it("normalizes legacy library text before presenting it", async () => {
    render(<ExcalidrawAdapter
      initialScene={null}
      onChange={vi.fn()}
      presentRequest={{
        requestId: "request-legacy-text",
        assetId: "asset-legacy-text",
        name: "旧版注意力图",
        elements: [{
          id: "legacy-text",
          type: "text",
          x: 0,
          y: 0,
          width: 164,
          height: 35,
          angle: 0,
          strokeColor: "#000000",
          backgroundColor: "transparent",
          fillStyle: "solid",
          strokeWidth: 2,
          strokeStyle: "solid",
          roughness: 1,
          opacity: 100,
          groupIds: [],
          seed: 1425060668,
          version: 55,
          versionNonce: 1755101188,
          isDeleted: false,
          boundElements: null,
          updated: 1649410862098,
          link: null,
          text: "Matmul",
          fontSize: 28,
          fontFamily: 1,
          textAlign: "center",
          verticalAlign: "middle",
          baseline: 25,
          containerId: null,
          originalText: "Matmul",
        }],
      }}
    />);

    await waitFor(() => expect(fakeSceneElements).toEqual(expect.arrayContaining([
      expect.objectContaining({
        type: "text",
        text: "Matmul",
        lineHeight: 1.25,
        autoResize: true,
        customData: expect.objectContaining({ whiteboardAssetId: "asset-legacy-text" }),
      }),
    ])));
  });

  it("closes the library name tooltip when the material is clicked", async () => {
    vi.mocked(api.getWhiteboardLibrary).mockImplementationOnce(
      () => new Promise<Awaited<ReturnType<typeof api.getWhiteboardLibrary>>>(() => {}),
    );
    render(<ExcalidrawAdapter initialScene={null} onChange={vi.fn()} />);

    const material = await screen.findByRole("button", { name: /白板素材/ });
    fireEvent.pointerOver(material);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("未命名图画");

    fireEvent.pointerDown(material);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
