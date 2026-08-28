import { readFileSync } from "node:fs";

const stylesheet = readFileSync("src/monitor/monitor.css", "utf8");

describe("sandbox monitor layout", () => {
  it("fits the desktop dashboard into a four-panel operational row", () => {
    expect(stylesheet).toContain("grid-template-columns:minmax(190px,1.05fr) minmax(300px,1.6fr) minmax(190px,1fr) minmax(190px,1fr)");
    expect(stylesheet).toContain(".sandbox-monitor-columns{display:contents}");
    expect(stylesheet).toContain(".sandbox-capacity-chart svg{height:150px;min-height:0");
    expect(stylesheet).toContain(".sandbox-log-list,.sandbox-runtime-list,.sandbox-execution-list{height:176px;max-height:176px");
  });
});
