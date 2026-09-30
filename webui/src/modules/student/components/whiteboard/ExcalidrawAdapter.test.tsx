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
const updateScene = vi.fn();
const scrollToContent = vi.fn();

vi.mock("@excalidraw/excalidraw", () => {
  function FakeExcalidraw({ children, excalidrawAPI, onLibraryChange }: { children?: ReactNode; excalidrawAPI: (value: unknown) => void; onLibraryChange: (items: typeof initialLibrary) => void }) {
    useEffect(() => {
      onLibraryChange(initialLibrary);
      excalidrawAPI({
        updateLibrary,
        updateScene,
        scrollToContent,
        getSceneElements: () => [],
        getAppState: () => ({ zoom: { value: 1 }, scrollX: 0, scrollY: 0 }),
      });
    }, [excalidrawAPI, onLibraryChange]);
    return <div data-testid="fake-excalidraw"><button type="button" className="library-unit library-unit__active">素材</button>{children}</div>;
  }
  const passthrough = ({ children }: { children?: ReactNode }) => <>{children}</>;
  const defaults = { LoadScene: passthrough, SaveToActiveFile: passthrough, Export: passthrough, SaveAsImage: passthrough, SearchMenu: passthrough, ClearCanvas: passthrough, ToggleTheme: passthrough, ChangeCanvasBackground: passthrough };
  return { CaptureUpdateAction: { IMMEDIATELY: "immediately", NEVER: "never" }, Excalidraw: FakeExcalidraw, Footer: passthrough, MainMenu: Object.assign(passthrough, { DefaultItems: defaults, Separator: passthrough, Item: passthrough }) };
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

  it("closes the library name tooltip when the material is clicked", async () => {
    render(<ExcalidrawAdapter initialScene={null} onChange={vi.fn()} />);

    const material = await screen.findByRole("button", { name: /白板素材/ });
    fireEvent.pointerOver(material);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("未命名图画");

    fireEvent.pointerDown(material);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
