import { useEffect, type ReactNode } from "react";
import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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

vi.mock("@excalidraw/excalidraw", () => {
  function FakeExcalidraw({ children, excalidrawAPI, onLibraryChange }: { children?: ReactNode; excalidrawAPI: (value: unknown) => void; onLibraryChange: (items: typeof initialLibrary) => void }) {
    useEffect(() => {
      onLibraryChange(initialLibrary);
      excalidrawAPI({
        updateLibrary,
        getSceneElements: () => [],
        getAppState: () => ({ zoom: { value: 1 }, scrollX: 0, scrollY: 0 }),
      });
    }, [excalidrawAPI, onLibraryChange]);
    return <div data-testid="fake-excalidraw">{children}</div>;
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
  it("does not clear the local library before a failed shared-library request", async () => {
    render(<ExcalidrawAdapter initialScene={null} onChange={vi.fn()} canManageLibrary />);

    await waitFor(() => expect(api.getWhiteboardLibrary).toHaveBeenCalled());
    const getOrder = vi.mocked(api.getWhiteboardLibrary).mock.invocationCallOrder[0];
    const clearOrders = updateLibrary.mock.invocationCallOrder.filter((_, index) => updateLibrary.mock.calls[index]?.[0]?.merge === false);
    expect(clearOrders).toHaveLength(1);
    expect(clearOrders[0]).toBeGreaterThan(getOrder);
  });
});
