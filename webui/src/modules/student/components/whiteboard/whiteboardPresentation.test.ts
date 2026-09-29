import { describe, expect, it } from "vitest";

import {
  cloneWhiteboardAssetElements,
  findNearestWhiteboardAssetOrigin,
  findPresentedWhiteboardAsset,
} from "./whiteboardPresentation";

const source = [{ id: "source-1", type: "rectangle", x: 0, y: 0, width: 80, height: 80 }];

describe("whiteboard asset presentation", () => {
  it("reuses an existing presentation by asset id", () => {
    const elements = [
      { id: "presented-1", type: "rectangle", x: 10, y: 10, width: 80, height: 80, customData: { whiteboardAssetId: "asset-1" } },
      { id: "other-1", type: "rectangle", x: 200, y: 10, width: 80, height: 80 },
    ];

    expect(findPresentedWhiteboardAsset(elements, "asset-1")).toEqual([elements[0]]);
    expect(findPresentedWhiteboardAsset(elements, "missing")).toEqual([]);
  });

  it("finds a nearby non-overlapping origin around the viewport center", () => {
    const origin = findNearestWhiteboardAssetOrigin(source, [{ id: "obstacle", type: "rectangle", x: 0, y: 0, width: 100, height: 100 }], { centerX: 40, centerY: 40 });

    expect(origin).not.toEqual({ x: 0, y: 0 });
    expect(origin.x + 80 <= 0 || origin.x >= 100 || origin.y + 80 <= 0 || origin.y >= 100).toBe(true);
    expect(Math.abs(origin.x - 40) + Math.abs(origin.y - 40)).toBeLessThan(400);
  });

  it("tags every cloned element with the source asset id", () => {
    const cloned = cloneWhiteboardAssetElements(source, { x: 120, y: 30 }, "asset-1", "注意力计算");

    expect(cloned).toHaveLength(1);
    expect(cloned[0].id).not.toBe("source-1");
    expect(cloned[0].x).toBe(120);
    expect(cloned[0].customData).toMatchObject({ whiteboardAssetId: "asset-1", whiteboardAssetName: "注意力计算" });
  });
});
