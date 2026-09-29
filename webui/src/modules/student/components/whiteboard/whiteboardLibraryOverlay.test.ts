import { describe, expect, it } from "vitest";

import { getSingleWhiteboardLibrarySelection, getWhiteboardLibraryDisplayName, getWhiteboardLibraryItemsRemoved, getWhiteboardLibraryItemsToMigrate, getWhiteboardLibraryTooltipPosition, normalizeWhiteboardLibraryItem } from "./whiteboardLibraryOverlay";

describe("whiteboard library overlay helpers", () => {
  it("keeps unnamed materials identifiable by their stable code", () => {
    expect(getWhiteboardLibraryDisplayName({ id: "asset-1", asset_code: "WB-000001" })).toBe("未命名图画 · WB-000001");
    expect(getWhiteboardLibraryDisplayName({ id: "asset-1", asset_code: "WB-000001", name: "注意力结构" })).toBe("注意力结构");
  });

  it("normalizes legacy records that do not yet have a code or metadata", () => {
    expect(normalizeWhiteboardLibraryItem({ id: "193c7240-ee91-4570-981c-a3db65c8deba", elements: [] })).toMatchObject({
      id: "193c7240-ee91-4570-981c-a3db65c8deba",
      asset_code: "WB-193C7240",
      status: "published",
      created: 0,
    });
  });

  it("keeps the name tooltip inside the viewport and flips it above near the bottom", () => {
    expect(getWhiteboardLibraryTooltipPosition({ left: 4, right: 59, top: 720, bottom: 775 }, 800, 800)).toEqual({ top: 660, left: 12 });
    expect(getWhiteboardLibraryTooltipPosition({ left: 740, right: 795, top: 120, bottom: 175 }, 800, 800)).toEqual({ top: 183, left: 568 });
  });

  it("only exposes a rename target for one selected material", () => {
    const first = { id: "asset-1" };
    expect(getSingleWhiteboardLibrarySelection([])).toBeNull();
    expect(getSingleWhiteboardLibrarySelection([first])).toBe(first);
    expect(getSingleWhiteboardLibrarySelection([first, { id: "asset-2" }])).toBeNull();
  });

  it("finds newly published materials when calculating deletions", () => {
    const published = { id: "asset-1", asset_code: "WB-000001", status: "published" as const, created: 1, elements: [] };
    const newlyPublished = { id: "asset-2", asset_code: "WB-000002", status: "published" as const, created: 2, elements: [] };
    expect(getWhiteboardLibraryItemsRemoved([published, newlyPublished], [published])).toEqual([newlyPublished]);
  });

  it("migrates restored legacy entries even when Excalidraw marks them published", () => {
    const legacyPublished = { id: "legacy-1", status: "published", elements: [{ id: "element-1" }] };
    const legacyUnpublished = { id: "legacy-2", status: "unpublished", elements: [{ id: "element-2" }] };
    const shared = { id: "shared-1", asset_code: "WB-SHARED1", status: "published", elements: [{ id: "element-3" }] };
    expect(getWhiteboardLibraryItemsToMigrate([legacyPublished, legacyUnpublished, shared])).toEqual([legacyPublished, legacyUnpublished]);
  });
});
