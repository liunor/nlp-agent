import { readFileSync } from "node:fs";

const stylesheet = readFileSync("src/app/styles.css", "utf8");

describe("sandbox titlebar layout", () => {
  it("keeps long student release notes in a borderless fixed-height scroller", () => {
    const releaseRule = [...stylesheet.matchAll(/\.release-notes-list\s*\{([^}]*)\}/g)].at(-1)?.[1] ?? "";

    expect(releaseRule).toContain("max-height: 300px");
    expect(releaseRule).toContain("overflow-y: auto");
    expect(releaseRule).toContain("border: 0");
    expect(releaseRule).toContain("background: transparent");
  });

  it("keeps the developer control plane white and full-width", () => {
    const compact = (value: string) => value.replace(/\s+/g, "");
    const matchingRule = (pattern: RegExp, fragment: string) => [...stylesheet.matchAll(pattern)]
      .map((match) => compact(match[1]))
      .find((rule) => rule.includes(fragment)) ?? "";
    const shellRule = matchingRule(/\.developer-shell\s*\{([^}]*)\}/g, "background:#fafafa");
    const contentRule = matchingRule(/\.developer-content\s*\{([^}]*)\}/g, "width:100%");
    const pageRule = matchingRule(/\.developer-page-grid\s*\{([^}]*)\}/g, "width:100%");

    expect(shellRule).toContain("--bg:#fff");
    expect(shellRule).toContain("--dev-panel:#fff");
    expect(shellRule).toContain("color:#27272a");
    expect(contentRule).toContain("width:100%");
    expect(contentRule).toContain("max-width:none");
    expect(contentRule).toContain("box-sizing:border-box");
    expect(pageRule).toContain("width:100%");
  });

  it("fills the dock and clips trailing actions instead of letting them overlap the file tab", () => {
    const workbenchRule = stylesheet.match(/\.sandbox-workbench\s*\{([^}]*)\}/)?.[1] ?? "";
    const titlebarRule = stylesheet.match(/\.sandbox-workbench-titlebar\s*\{([^}]*)\}/)?.[1] ?? "";
    const themeSwitcherRule = stylesheet.match(/\.sandbox-theme-switcher\s*\{([^}]*)\}/)?.[1] ?? "";
    const environmentBarRule = stylesheet.match(/\.sandbox-environment-bar\s*\{([^}]*)\}/)?.[1] ?? "";
    const gutterRule = stylesheet.match(/\.sandbox-code-gutter\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(workbenchRule).toContain("width:100%");
    expect(workbenchRule).toContain("9px minmax(0,auto)");
    expect(titlebarRule).toContain("display:flex");
    expect(titlebarRule).toContain("overflow-x:auto");
    expect(titlebarRule).toContain("scrollbar-width:none");
    expect(titlebarRule).toContain("white-space:nowrap");
    expect(themeSwitcherRule).toContain("margin-left:auto");
    expect(environmentBarRule).toContain("scrollbar-width:none");
    expect(gutterRule).toContain("min-width:42px");
    expect(gutterRule).toContain("padding:16px 8px 16px 0");
  });

  it("reserves a complete safe area for the tool picker trigger beside fixed header actions", () => {
    const tabRules = [...stylesheet.matchAll(/\.tool-dock-tabs\s*\{([^}]*)\}/g)].map((match) => match[1]);

    expect(tabRules[0]).toContain("padding: 0 124px 0 8px");
    expect(tabRules.slice(1)).toHaveLength(2);
    expect(tabRules.slice(1).every((rule) => rule.includes("padding-right: 124px"))).toBe(true);
  });

  it("keeps the dock resize handle and add-tool picker above the learning panel", () => {
    const handleRule = stylesheet.match(/\.tool-dock-resize-handle\s*\{([^}]*)\}/)?.[1] ?? "";
    const dockLearningRule = stylesheet.match(/\.tool-dock \.learning-panel\s*\{([^}]*)\}/)?.[1] ?? "";

    // The handle stays fully inside the dock so overflow:hidden no longer clips it to ~4px.
    expect(handleRule).toContain("left: 0");
    expect(handleRule).not.toContain("left: -4px");
    // The panel must not leak its floating-rail z-index above the dock chrome.
    expect(dockLearningRule).toContain("z-index: auto");
  });

  it("lets a narrow knowledge-book toolbar scroll instead of overlapping its controls", () => {
    const toolbarRule = stylesheet.match(/\.knowledge-book-toolbar\s*\{([^}]*)\}/)?.[1] ?? "";
    const brandRule = stylesheet.match(/\.knowledge-book-brand\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(toolbarRule).toContain("overflow-x: auto");
    expect(toolbarRule).toContain("scrollbar-width: none");
    expect(toolbarRule).toContain("white-space: nowrap");
    expect(brandRule).toContain("overflow: hidden");
    expect(stylesheet).toContain(".knowledge-book-toolbar::-webkit-scrollbar { display: none; }");
  });

  it("passes the viewport height through the teacher book editing surface", () => {
    const lastRule = (pattern: RegExp) => [...stylesheet.matchAll(pattern)].at(-1)?.[1] ?? "";
    const teacherMainRule = lastRule(/\.teacher-main\.teacher-book-main\s*\{([^}]*)\}/g);
    const bookContentRule = lastRule(/\.teacher-content-book\s*\{([^}]*)\}/g);
    const bookEditorRule = lastRule(/\.teacher-content-book \.teacher-book-editor\s*\{([^}]*)\}/g);
    const bookLayoutRule = lastRule(/\.teacher-content-book \.teacher-book-layout\s*\{([^}]*)\}/g);

    expect(teacherMainRule).toContain("display: grid");
    expect(teacherMainRule).toContain("grid-template-rows: 58px minmax(0,1fr)");
    expect(teacherMainRule).toContain("min-height: 0");
    expect(bookContentRule).toContain("display: flex");
    expect(bookContentRule).toContain("flex: 1 1 auto");
    expect(bookEditorRule).toContain("flex: 1 1 auto");
    expect(bookLayoutRule).toContain("flex: 1 1 auto");
  });

  it("locks the teacher book workbench to the available viewport instead of shrinking to content", () => {
    const bookContentRules = [...stylesheet.matchAll(/\.teacher-content-book\s*\{([^}]*)\}/g)].map((match) => match[1]);
    const bookLayoutRules = [...stylesheet.matchAll(/\.teacher-content-book \.teacher-book-layout\s*\{([^}]*)\}/g)].map((match) => match[1]);
    const bookContentRule = bookContentRules.at(-1) ?? "";
    const bookLayoutRule = bookLayoutRules.at(-1) ?? "";

    expect(bookContentRule).toContain("width: 100%");
    expect(bookContentRule).toContain("margin: 0");
    expect(bookContentRule).toContain("height: 100%");
    expect(bookContentRule).toContain("overflow: hidden");
    expect(bookLayoutRule).toContain("height: 100%");
    expect(bookLayoutRule).toContain("overflow: hidden");
  });

  it("allocates the teacher book in the remaining viewport row so the editor reaches the bottom", () => {
    const teacherMainRule = [...stylesheet.matchAll(/\.teacher-main\.teacher-book-main\s*\{([^}]*)\}/g)].at(-1)?.[1] ?? "";
    const bookContentRule = [...stylesheet.matchAll(/\.teacher-content-book\s*\{([^}]*)\}/g)].at(-1)?.[1] ?? "";

    expect(teacherMainRule).toContain("display: grid");
    expect(teacherMainRule).toContain("grid-template-rows: 58px minmax(0,1fr)");
    expect(bookContentRule).toContain("height: 100%");
    expect(bookContentRule).toContain("min-height: 0");
  });

  it("lets student questions use the full teacher viewport while keeping the report scrollable", () => {
    const teacherMainRule = stylesheet.match(/\.teacher-main\.teacher-questions-main\s*\{([^}]*)\}/)?.[1] ?? "";
    const questionsContentRule = stylesheet.match(/\.teacher-content-questions\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(teacherMainRule).toContain("display: grid");
    expect(teacherMainRule).toContain("height: 100vh");
    expect(teacherMainRule).toContain("grid-template-rows: 58px minmax(0, 1fr)");
    expect(questionsContentRule).toContain("width: 100%");
    expect(questionsContentRule).toContain("max-width: none");
    expect(questionsContentRule).toContain("height: 100%");
    expect(questionsContentRule).toContain("overflow: auto");
  });

  it("lets learning analysis use the same full teacher viewport as student questions", () => {
    const teacherMainRule = stylesheet.match(/\.teacher-main\.teacher-analysis-main\s*\{([^}]*)\}/)?.[1] ?? "";
    const analysisContentRule = stylesheet.match(/\.teacher-content-analysis\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(teacherMainRule).toContain("display:grid");
    expect(teacherMainRule).toContain("height:100vh");
    expect(teacherMainRule).toContain("grid-template-rows:58px minmax(0,1fr)");
    expect(analysisContentRule).toContain("width:100%");
    expect(analysisContentRule).toContain("max-width:none");
    expect(analysisContentRule).toContain("height:100%");
    expect(analysisContentRule).toContain("overflow:auto");
  });

  it("allows question distribution panels to size themselves from their data", () => {
    const gridRule = stylesheet.match(/\.teacher-question-grid\s*\{([^}]*)\}/)?.[1] ?? "";
    const panelRule = stylesheet.match(/\.teacher-question-panel\s*\{([^}]*)\}/)?.[1] ?? "";
    const dailyScrollRule = stylesheet.match(/\.teacher-question-chart-scroll\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(gridRule).toContain("align-items: start");
    expect(panelRule).toContain("align-self: start");
    expect(dailyScrollRule).toContain("overflow-x: auto");
  });

  it("keeps five-row distribution cards equal and lets long topic names scroll", () => {
    const distributionRule = stylesheet.match(/\.teacher-question-distribution\s*\{([^}]*)\}/)?.[1] ?? "";
    const topicNameRule = stylesheet.match(/\.teacher-question-distribution-name\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(distributionRule).toContain("grid-template-rows: repeat(5");
    expect(topicNameRule).toContain("overflow-x: auto");
    expect(stylesheet).toContain(".teacher-question-month-tabs");
  });

  it("provides compact visual affordances for chart hover and pie callouts", () => {
    expect(stylesheet).toContain(".teacher-question-line-tooltip");
    expect(stylesheet).toContain(".teacher-question-line-hover-target");
    expect(stylesheet).toContain(".teacher-question-pie-callout");
    expect(stylesheet).toContain(".teacher-question-pie-label");
  });

  it("gives each trend chart a full-width row with enough horizontal detail", () => {
    const trendRule = stylesheet.match(/\.teacher-question-grid-trend\s*\{([^}]*)\}/)?.[1] ?? "";
    const lineChartRule = stylesheet.match(/\.teacher-question-line-chart\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(trendRule).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(lineChartRule).toContain("min-width: 960px");
  });

  it("uses a light, borderless D2L-style code surface and a wider reading column", () => {
    expect(stylesheet).toContain(".knowledge-book-article .code-shell { margin: 28px 0 32px; border: 0 !important;");
    expect(stylesheet).toContain(".knowledge-book-article .code-shell pre { border: 0 !important;");
    expect(stylesheet).toContain(".knowledge-book-article { width: min(100%,1080px);");
    expect(stylesheet).toContain(".knowledge-book-article .markdown-content > pre,.teacher-book-preview .markdown-content > pre");
    expect(stylesheet).toContain(".knowledge-book-article .code-shell,.teacher-book-preview .code-shell { overflow: hidden; border: 1px solid #e1e4ea !important;");
    expect(stylesheet).toContain(".knowledge-book-article .code-toolbar,.teacher-book-preview .code-toolbar { justify-content: flex-end;");
  });

  it("keeps knowledge-book anchors in normal flow while deferring syntax highlighting", () => {
    expect(stylesheet).not.toContain(".knowledge-book-article .markdown-image-figure,.knowledge-book-article .code-shell { content-visibility: auto;");
  });

  it("keeps feedback bubbles packed at the top instead of stretching grid rows", () => {
    const feedbackMessagesRule = [...stylesheet.matchAll(/\.developer-feedback-messages\s*\{([^}]*)\}/g)].at(-1)?.[1] ?? "";

    expect(feedbackMessagesRule).toContain("align-content: start");
  });

  it("keeps the quota daily and weekly grids at the same seven-row height", () => {
    const activityGridRule = [...stylesheet.matchAll(/\.quota-activity-grid\s*\{([^}]*)\}/g)].at(-1)?.[1] ?? "";
    const weeklyColumnRule = stylesheet.match(/\.quota-activity-week-column\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(activityGridRule).toContain("grid-template-rows: repeat(7, 14px)");
    expect(activityGridRule).toContain("min-height: 122px");
    expect(weeklyColumnRule).toContain("height: 122px");
  });

  it("lets developer quota management fill the control-plane workspace with compact controls", () => {
    const matchingRule = (pattern: RegExp, fragment: string) =>
      [...stylesheet.matchAll(pattern)].map((match) => match[1]).find((rule) => rule.includes(fragment)) ?? "";
    const pageRule = matchingRule(/\.developer-quota-page\s*\{([^}]*)\}/g, "width:100%");
    const tabsRule = matchingRule(/\.developer-quota-page \.quota-management-tabs\s*\{([^}]*)\}/g, "width:max-content");
    const tabButtonRule = matchingRule(/\.developer-quota-page \.quota-management-tabs button\s*\{([^}]*)\}/g, "flex:0 0 auto");
    const panelRule = matchingRule(/\.developer-quota-page \.quota-panel\s*\{([^}]*)\}/g, "margin-bottom:0");

    expect(pageRule).toContain("width:100%");
    expect(pageRule).toContain("max-width:none");
    expect(pageRule).toContain("min-height:100%");
    expect(pageRule).toContain("box-sizing:border-box");
    expect(tabsRule).toContain("width:max-content");
    expect(tabsRule).toContain("max-width:100%");
    expect(tabButtonRule).toContain("flex:0 0 auto");
    expect(tabButtonRule).toContain("min-height:34px");
    expect(panelRule).toContain("margin-bottom:0");
  });

  it("keeps developer quota collections fixed with internal scrolling", () => {
    const matchingRule = (pattern: RegExp, fragment: string) =>
      [...stylesheet.matchAll(pattern)].map((match) => match[1]).find((rule) => rule.includes(fragment)) ?? "";
    const pageRule = matchingRule(/\.developer-quota-page\s*\{([^}]*)\}/g, "overflow:hidden");
    const controlRule = matchingRule(/\.developer-quota-page \.quota-control-grid\s*\{([^}]*)\}/g, "grid-template-rows:repeat(2,minmax(0,1fr))");
    const panelRule = matchingRule(/\.developer-quota-page \.quota-control-grid \.quota-panel\s*\{([^}]*)\}/g, "display:flex");
    const tableRule = matchingRule(/\.developer-quota-page \.quota-control-grid \.quota-panel \.quota-table-wrap\s*\{([^}]*)\}/g, "overflow:auto");
    const operationsRule = matchingRule(/\.developer-quota-page \.quota-operations-grid\s*\{([^}]*)\}/g, "grid-template-rows:minmax(0,auto) minmax(0,1fr)");
    const recoveryRule = matchingRule(/\.developer-quota-page \.quota-recovery-grid\s*\{([^}]*)\}/g, "overflow:hidden");

    expect(pageRule).toContain("overflow:hidden");
    expect(controlRule).toContain("grid-template-rows:repeat(2,minmax(0,1fr))");
    expect(controlRule).toContain("min-height:0");
    expect(panelRule).toContain("display:flex");
    expect(panelRule).toContain("flex-direction:column");
    expect(tableRule).toContain("overflow:auto");
    expect(operationsRule).toContain("min-height:0");
    expect(recoveryRule).toContain("flex:1");
  });

  it("keeps developer quota subroutes in one fixed panel with internal collection scrolling", () => {
    const matchingRule = (pattern: RegExp, fragment: string) =>
      [...stylesheet.matchAll(pattern)].map((match) => match[1]).find((rule) => rule.includes(fragment)) ?? "";
    const subrouteRule = matchingRule(/\.developer-quota-page \.quota-subroute-tabs\s*\{([^}]*)\}/g, "overflow-x:auto");
    const routeRule = matchingRule(/\.developer-quota-page > \.quota-route-panel\s*\{([^}]*)\}/g, "overflow:hidden");
    const contentRule = matchingRule(/\.developer-quota-page \.quota-route-panel > \.quota-route-content\s*\{([^}]*)\}/g, "flex-direction:column");
    const tableRule = matchingRule(/\.developer-quota-page \.quota-route-content \.quota-table-wrap\s*\{([^}]*)\}/g, "overflow:auto");

    expect(subrouteRule).toContain("flex:0 0 auto");
    expect(routeRule).toContain("min-height:0");
    expect(routeRule).toContain("flex:1 1 auto");
    expect(contentRule).toContain("min-height:0");
    expect(contentRule).toContain("overflow:hidden");
    expect(tableRule).toContain("min-height:0");
    expect(tableRule).toContain("flex:1 1 auto");
  });

  it("keeps user management fixed with an internal user table scroller", () => {
    const matchingRule = (pattern: RegExp, fragment: string) => [...stylesheet.matchAll(pattern)]
      .map((match) => match[1])
      .find((rule) => rule.includes(fragment)) ?? "";
    const pageRule = matchingRule(/\.user-manage-page\s*\{([^}]*)\}/g, "height: 100%");
    const tableRule = matchingRule(/\.user-table-card\s*\{([^}]*)\}/g, "flex: 1 1 auto");
    const scrollRule = matchingRule(/\.user-table-scroll\s*\{([^}]*)\}/g, "overflow: auto");

    expect(pageRule).toContain("height: 100%");
    expect(pageRule).toContain("min-height: 0");
    expect(pageRule).toContain("overflow: hidden");
    expect(tableRule).toContain("min-height: 0");
    expect(tableRule).toContain("flex: 1 1 auto");
    expect(tableRule).toContain("overflow: hidden");
    expect(scrollRule).toContain("min-height: 0");
    expect(scrollRule).toContain("overflow: auto");
  });

  it("keeps the role permission workbench fixed with scrolling only in the permission catalog", () => {
    const matchingRule = (pattern: RegExp, fragment: string) => [...stylesheet.matchAll(pattern)]
      .map((match) => match[1])
      .find((rule) => rule.includes(fragment)) ?? "";
    const shellRule = matchingRule(/\.developer-shell:has\(\.developer-role-page\)\s*\{([^}]*)\}/g, "height: 100%");
    const mainRule = matchingRule(/\.developer-shell:has\(\.developer-role-page\) \.developer-main\s*\{([^}]*)\}/g, "height: 100%");
    const contentRule = matchingRule(/\.developer-content:has\(\.developer-role-page\)\s*\{([^}]*)\}/g, "height: 100%");
    const pageRule = matchingRule(/\.developer-role-page\s*\{([^}]*)\}/g, "height: 100%");
    const layoutRule = matchingRule(/\.developer-role-layout\s*\{([^}]*)\}/g, "min-height: 0");
    const permissionScrollRule = matchingRule(/\.developer-role-permission-scroll\s*\{([^}]*)\}/g, "overflow: auto");

    expect(shellRule).toContain("height: 100%");
    expect(shellRule).toContain("overflow: hidden");
    expect(stylesheet).toContain(".developer-eyebrow,.user-eyebrow { display: none; }");
    expect(mainRule).toContain("height: 100%");
    expect(mainRule).toContain("min-height: 0");
    expect(mainRule).toContain("overflow: hidden");
    expect(contentRule).toContain("height: 100%");
    expect(contentRule).toContain("overflow: hidden");
    expect(pageRule).toContain("height: 100%");
    expect(pageRule).toContain("overflow: hidden");
    expect(layoutRule).toContain("min-height: 0");
    expect(permissionScrollRule).toContain("overflow: auto");
  });
});
