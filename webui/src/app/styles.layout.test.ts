import { readFileSync } from "node:fs";

const stylesheet = readFileSync("src/app/styles.css", "utf8");

describe("sandbox titlebar layout", () => {
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
});
