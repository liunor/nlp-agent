import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WhiteboardAssetThumbnail } from "./WhiteboardAssetThumbnail";

describe("WhiteboardAssetThumbnail", () => {
  it("fits drawings whose Excalidraw coordinates are far from the origin", () => {
    render(<WhiteboardAssetThumbnail
      label="Data collection"
      elements={[
        {
          id: "arrow",
          type: "arrow",
          x: -13708.14,
          y: 4091.25,
          width: 23,
          height: 24,
          points: [[0, 0], [23, 24]],
          strokeColor: "#343a40",
          backgroundColor: "transparent",
          strokeWidth: 2,
        },
        {
          id: "box",
          type: "rectangle",
          x: -13650,
          y: 4090,
          width: 120,
          height: 80,
          strokeColor: "#343a40",
          backgroundColor: "#fff",
          strokeWidth: 2,
        },
      ]}
    />);

    const thumbnail = screen.getByRole("img", { name: "预览：Data collection" });
    const viewBox = thumbnail.getAttribute("viewBox")?.split(" ").map(Number) ?? [];
    expect(viewBox).toHaveLength(4);
    expect(viewBox[0]).toBeLessThan(-13700);
    expect(viewBox[2]).toBeLessThan(300);
    expect(thumbnail.querySelectorAll("polyline, rect")).toHaveLength(3);
  });

  it("preserves multi-segment arrows and arrowheads in the preview", () => {
    render(<WhiteboardAssetThumbnail
      label="流程图"
      elements={[{
        id: "arrow",
        type: "arrow",
        x: 0,
        y: 0,
        width: 80,
        height: 50,
        points: [[0, 0], [40, 20], [80, 50]],
        endArrowhead: "arrow",
        strokeColor: "#343a40",
      }]}
    />);

    const thumbnail = screen.getByRole("img", { name: "预览：流程图" });
    expect(thumbnail.querySelector("polyline")).toHaveAttribute("points", "0,0 40,20 80,50");
    expect(thumbnail.querySelector("polyline")).toHaveAttribute("marker-end", "url(#whiteboard-thumbnail-arrow)");
  });
});
