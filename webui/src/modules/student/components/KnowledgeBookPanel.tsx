import { ChevronDown, ChevronRight, Menu, PanelLeftClose, PanelRightClose, RefreshCw, Search, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import { api } from "@/platform/http/api";
import type { LearningBookNavigationItem, LearningBookPage } from "@/shared/types";

import { demoLearningBookNavigation, demoLearningBookPages } from "./knowledgeBookDemo";
import { indexMarkdownHeadings, readKnowledgeBookUrl, replaceKnowledgeBookUrl } from "./knowledgeBook";
import { MarkdownContent, type MarkdownCodeActions } from "./MarkdownContent";

interface TopicGroup {
  id: string;
  name: string;
  items: LearningBookNavigationItem[];
}

interface BookViewState {
  selectedId: string | null;
  expandedTopics: string[];
  scrollPositions: Record<string, number>;
  leftCollapsed: boolean;
  rightCollapsed: boolean;
}

interface SelectionPrompt {
  text: string;
  heading?: string;
  top: number;
  left: number;
}

function quoteSelection(text: string): string {
  return text.split(/\r?\n/).map((line) => `> ${line}`).join("\n");
}

function buildSelectionPrompt(page: LearningBookPage, text: string, heading: string | undefined): string {
  return [
    `我正在阅读「${page.topic_name} / ${page.title}」${heading ? `的「${heading}」小节` : ""}。`,
    "",
    "选中内容：",
    quoteSelection(text),
    "",
    "请结合本节上下文解释这段内容，并指出我理解时最需要注意的地方。",
  ].join("\n");
}

function buildCodePrompt(page: LearningBookPage, code: string, language: string, heading: string | undefined): string {
  const maxCodeLength = 6000;
  const excerpt = code.length > maxCodeLength ? `${code.slice(0, maxCodeLength)}\n……（代码过长，已截断）` : code;
  return [`我正在阅读「${page.topic_name} / ${page.title}」${heading ? `的「${heading}」小节` : ""}中的代码示例。`, "", `语言：${language}`, "", "```" + language, excerpt, "```", "", "请解释这段代码的作用、关键步骤，以及它在本节知识点中的意义。"].join("\n");
}

function isExcludedSelectionNode(node: Node | null): boolean {
  const element = node instanceof Element ? node : node?.parentElement;
  return !!element?.closest("code,button,input,textarea,select,.knowledge-book-page-nav,.knowledge-book-toc");
}

function headingBeforeSelection(article: HTMLElement, range: Range): string | undefined {
  let current: string | undefined;
  for (const heading of article.querySelectorAll<HTMLElement>("h2,h3,h4")) {
    if (heading.compareDocumentPosition(range.startContainer) & Node.DOCUMENT_POSITION_FOLLOWING) current = heading.textContent?.trim() || current;
  }
  return current;
}

function readBookViewState(workspaceId: string): BookViewState {
  const fallback: BookViewState = { selectedId: null, expandedTopics: [], scrollPositions: {}, leftCollapsed: false, rightCollapsed: false };
  try {
    const raw = window.sessionStorage.getItem(`nova:knowledge-book:${workspaceId}`);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<BookViewState>;
    return {
      selectedId: typeof parsed.selectedId === "string" ? parsed.selectedId : null,
      expandedTopics: Array.isArray(parsed.expandedTopics) ? parsed.expandedTopics.filter((value): value is string => typeof value === "string") : [],
      scrollPositions: parsed.scrollPositions && typeof parsed.scrollPositions === "object" ? parsed.scrollPositions : {},
      leftCollapsed: parsed.leftCollapsed === true,
      rightCollapsed: parsed.rightCollapsed === true,
    };
  } catch {
    return fallback;
  }
}

function groupNavigation(items: LearningBookNavigationItem[]): TopicGroup[] {
  const groups = new Map<string, TopicGroup>();
  for (const item of items) {
    const group = groups.get(item.topic_id) ?? { id: item.topic_id, name: item.topic_name, items: [] };
    group.items.push(item);
    groups.set(item.topic_id, group);
  }
  return [...groups.values()].map((group) => ({
    ...group,
    items: [...group.items].sort((left, right) => left.sort_order - right.sort_order || left.title.localeCompare(right.title, "zh-CN")),
  }));
}

function orderNavigation(items: LearningBookNavigationItem[]): LearningBookNavigationItem[] {
  return groupNavigation(items).flatMap((group) => group.items);
}

function findHeadingAnchor(root: HTMLElement, id: string): HTMLElement | undefined {
  return [...root.querySelectorAll<HTMLElement>("[data-knowledge-book-heading-anchor]")].find((candidate) => candidate.id === id);
}

function findVisibleHeading(root: HTMLElement, id: string): HTMLElement | undefined {
  return [...root.querySelectorAll<HTMLElement>("[data-knowledge-book-heading-id]")].find((candidate) => candidate.dataset.knowledgeBookHeadingId === id);
}

function scrollToHeading(root: HTMLElement | null, id: string): boolean {
  if (!root) return false;
  const anchor = findHeadingAnchor(root, id);
  if (!anchor) return false;
  const rootRect = root.getBoundingClientRect();
  const anchorRect = anchor.getBoundingClientRect();
  const top = Math.max(0, root.scrollTop + anchorRect.top - rootRect.top - 18);
  const behavior = document.documentElement.dataset.reduceMotion === "true" ? "auto" : "smooth";
  if (root.scrollTo) root.scrollTo({ top, behavior });
  else anchor.scrollIntoView({ behavior, block: "start", inline: "nearest" });
  return true;
}

function keepFocusInDrawer(event: ReactKeyboardEvent<HTMLElement>) {
  if (event.key !== "Tab") return;
  const focusable = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not([disabled])")];
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

export function KnowledgeBookPanel({ workspaceId, onAskNova, onOpenInSandbox }: { workspaceId: string; onAskNova?: (prompt: string) => void; onOpenInSandbox?: (code: string, language: string) => void }) {
  const [initialViewState] = useState(() => readBookViewState(workspaceId));
  const [initialDeepLink] = useState(() => readKnowledgeBookUrl(window.location.search));
  const demoMode = initialDeepLink.demo;
  const [navigation, setNavigation] = useState<LearningBookNavigationItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(initialDeepLink.pointId ?? initialViewState.selectedId);
  const [page, setPage] = useState<LearningBookPage | null>(null);
  const [loadingNavigation, setLoadingNavigation] = useState(true);
  const [loadingPage, setLoadingPage] = useState(false);
  const [pageReloadToken, setPageReloadToken] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const [leftCollapsed, setLeftCollapsed] = useState(initialViewState.leftCollapsed);
  const [rightCollapsed, setRightCollapsed] = useState(initialViewState.rightCollapsed);
  const [expandedTopics, setExpandedTopics] = useState<Set<string>>(() => new Set(initialViewState.expandedTopics));
  const [activeHeadingId, setActiveHeadingId] = useState<string | null>(null);
  const [selectionPrompt, setSelectionPrompt] = useState<SelectionPrompt | null>(null);
  const [navigationQuery, setNavigationQuery] = useState("");
  const contentRef = useRef<HTMLDivElement>(null);
  const articleRef = useRef<HTMLElement>(null);
  const selectionActionRef = useRef<HTMLButtonElement>(null);
  const leftDrawerRef = useRef<HTMLElement>(null);
  const rightDrawerRef = useRef<HTMLElement>(null);
  const leftToggleRef = useRef<HTMLButtonElement>(null);
  const rightToggleRef = useRef<HTMLButtonElement>(null);
  const previousLeftOpen = useRef(false);
  const previousRightOpen = useRef(false);
  const scrollPositionsRef = useRef(initialViewState.scrollPositions);
  const scrollPersistTimer = useRef<number | null>(null);
  const pageCacheRef = useRef(new Map<string, LearningBookPage>());
  const visiblePageRef = useRef<LearningBookPage | null>(null);
  const activeHeadingIdRef = useRef<string | null>(null);
  const headingIndexRef = useRef({ headings: [], headingIds: [], headingIdsByLine: {} } as ReturnType<typeof indexMarkdownHeadings>);
  const onAskNovaRef = useRef(onAskNova);
  const onOpenInSandboxRef = useRef(onOpenInSandbox);
  const topicGroups = useMemo(() => groupNavigation(navigation), [navigation]);
  const orderedNavigation = useMemo(() => orderNavigation(navigation), [navigation]);
  const visiblePage = page?.knowledge_point_id === selectedId ? page : null;
  const headingIndex = useMemo(() => indexMarkdownHeadings(visiblePage?.content_markdown ?? ""), [visiblePage?.content_markdown]);
  const markdownHasTitle = useMemo(() => /^(?: {0,3})#(?!#)[ \t]+.+/m.test(visiblePage?.content_markdown ?? ""), [visiblePage?.content_markdown]);
  useEffect(() => {
    visiblePageRef.current = visiblePage;
    activeHeadingIdRef.current = activeHeadingId;
    headingIndexRef.current = headingIndex;
    onAskNovaRef.current = onAskNova;
    onOpenInSandboxRef.current = onOpenInSandbox;
  }, [activeHeadingId, headingIndex, onAskNova, onOpenInSandbox, visiblePage]);
  const canAskNova = Boolean(onAskNova);
  const canOpenInSandbox = Boolean(onOpenInSandbox);
  const codeActions = useMemo<MarkdownCodeActions>(() => {
    const actions: MarkdownCodeActions = {};
    if (canAskNova) {
      actions.onAskNova = (code, language) => {
        const currentPage = visiblePageRef.current;
        if (!currentPage) return;
        const heading = headingIndexRef.current.headings.find((item) => item.id === activeHeadingIdRef.current)?.text;
        onAskNovaRef.current?.(buildCodePrompt(currentPage, code, language, heading));
      };
    }
    if (canOpenInSandbox) {
      actions.onOpenInSandbox = (code, language) => onOpenInSandboxRef.current?.(code, language);
    }
    return actions;
  }, [canAskNova, canOpenInSandbox]);
  const filteredTopicGroups = useMemo(() => {
    const query = navigationQuery.trim().toLocaleLowerCase();
    if (!query) return topicGroups;
    return topicGroups
      .map((group) => {
        if (group.name.toLocaleLowerCase().includes(query)) return group;
        return { ...group, items: group.items.filter((item) => item.title.toLocaleLowerCase().includes(query)) };
      })
      .filter((group) => group.items.length > 0);
  }, [navigationQuery, topicGroups]);

  const saveViewState = useCallback(() => {
    try {
      window.sessionStorage.setItem(`nova:knowledge-book:${workspaceId}`, JSON.stringify({
        selectedId,
        expandedTopics: [...expandedTopics],
        scrollPositions: scrollPositionsRef.current,
        leftCollapsed,
        rightCollapsed,
      } satisfies BookViewState));
    } catch {
      // Private browsing and restricted storage should not block reading.
    }
  }, [expandedTopics, leftCollapsed, rightCollapsed, selectedId, workspaceId]);

  const handlePageScroll = () => {
    if (!selectedId || !contentRef.current) return;
    scrollPositionsRef.current[selectedId] = contentRef.current.scrollTop;
    if (scrollPersistTimer.current !== null) window.clearTimeout(scrollPersistTimer.current);
    scrollPersistTimer.current = window.setTimeout(saveViewState, 180);
  };

  useEffect(() => {
    saveViewState();
    return () => {
      if (scrollPersistTimer.current !== null) window.clearTimeout(scrollPersistTimer.current);
      saveViewState();
    };
  }, [saveViewState]);

  const loadNavigation = useCallback(async (refreshPage = false) => {
    if (refreshPage) {
      pageCacheRef.current.clear();
      setPageReloadToken((value) => value + 1);
    }
    setLoadingNavigation(true);
    setError(null);
    try {
      const items = demoMode ? demoLearningBookNavigation : (await api.getLearningBookNavigation(workspaceId)).items;
      setNavigation(items);
      const orderedItems = orderNavigation(items);
      setSelectedId((current) => current && items.some((item) => item.knowledge_point_id === current) ? current : orderedItems[0]?.knowledge_point_id ?? null);
      setExpandedTopics((current) => {
        if (demoMode) return new Set(demoLearningBookNavigation.map((item) => item.topic_id));
        const next = new Set(current);
        if (!next.size && orderedItems[0]) next.add(orderedItems[0].topic_id);
        return next;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "教材目录加载失败");
    } finally {
      setLoadingNavigation(false);
    }
  }, [demoMode, workspaceId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadNavigation(), 0);
    return () => window.clearTimeout(timer);
  }, [loadNavigation]);

  useEffect(() => {
    replaceKnowledgeBookUrl({ pointId: selectedId });
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return undefined;
    let current = true;
    const cachedPage = pageCacheRef.current.get(selectedId);
    if (cachedPage) {
      setPage(cachedPage);
      setActiveHeadingId(null);
      setLoadingPage(false);
      return () => { current = false; };
    }
    const timer = window.setTimeout(() => {
      setLoadingPage(true);
      setError(null);
      const request = demoMode
        ? Promise.resolve({ page: demoLearningBookPages[selectedId] ?? null })
        : api.getLearningBookPage(workspaceId, selectedId);
      void request.then((response) => {
        if (!current) return;
        setPage(response.page);
        if (response.page) pageCacheRef.current.set(selectedId, response.page);
        setActiveHeadingId(null);
      }).catch((cause: unknown) => {
        if (current) setError(cause instanceof Error ? cause.message : "知识点内容加载失败");
      }).finally(() => {
        if (current) setLoadingPage(false);
      });
    }, 0);
    return () => { current = false; window.clearTimeout(timer); };
  }, [demoMode, pageReloadToken, selectedId, workspaceId]);

  useEffect(() => {
    const root = contentRef.current;
    if (!root || !headingIndex.headings.length || typeof IntersectionObserver === "undefined") return undefined;
    const observer = new IntersectionObserver((entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top);
      const headingId = (visible[0]?.target as HTMLElement | undefined)?.dataset.knowledgeBookHeadingId;
      if (headingId) setActiveHeadingId(headingId);
    }, { root, rootMargin: "-8% 0px -72% 0px", threshold: [0, 1] });
    for (const heading of headingIndex.headings) {
      const element = findVisibleHeading(root, heading.id);
      if (element) observer.observe(element);
    }
    return () => observer.disconnect();
  }, [headingIndex, visiblePage?.knowledge_point_id]);

  useEffect(() => {
    if (!visiblePage || loadingPage) return undefined;
    const timer = window.setTimeout(() => {
      const root = contentRef.current;
      const savedScrollTop = scrollPositionsRef.current[visiblePage.knowledge_point_id] ?? 0;
      if (root && savedScrollTop > 0) root.scrollTo?.({ top: savedScrollTop, behavior: "auto" });
      articleRef.current?.focus({ preventScroll: true });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadingPage, visiblePage]);

  useEffect(() => {
    if (!visiblePage || loadingPage || !initialDeepLink.headingId || (initialDeepLink.pointId && initialDeepLink.pointId !== visiblePage.knowledge_point_id) || !headingIndex.headings.length) return undefined;
    const target = headingIndex.headings.find((heading) => heading.id === initialDeepLink.headingId || heading.text === initialDeepLink.headingId);
    if (!target) return undefined;
    const timer = window.setTimeout(() => scrollToHeading(contentRef.current, target.id), 0);
    return () => window.clearTimeout(timer);
  }, [headingIndex, initialDeepLink.headingId, initialDeepLink.pointId, loadingPage, visiblePage]);

  useEffect(() => {
    if (!leftOpen && !rightOpen && !selectionPrompt) return undefined;
    const closeDrawers = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setLeftOpen(false);
      setRightOpen(false);
      setSelectionPrompt(null);
    };
    window.addEventListener("keydown", closeDrawers);
    return () => window.removeEventListener("keydown", closeDrawers);
  }, [leftOpen, rightOpen, selectionPrompt]);

  useEffect(() => {
    if (!selectionPrompt) return undefined;
    const clearSelectionPrompt = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && (selectionActionRef.current?.contains(target) || articleRef.current?.contains(target))) return;
      setSelectionPrompt(null);
    };
    document.addEventListener("pointerdown", clearSelectionPrompt);
    return () => document.removeEventListener("pointerdown", clearSelectionPrompt);
  }, [selectionPrompt]);

  useEffect(() => {
    const wasOpen = previousLeftOpen.current;
    previousLeftOpen.current = leftOpen;
    const timer = window.setTimeout(() => {
      if (leftOpen) leftDrawerRef.current?.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
      else if (wasOpen) leftToggleRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [leftOpen]);

  useEffect(() => {
    const wasOpen = previousRightOpen.current;
    previousRightOpen.current = rightOpen;
    const timer = window.setTimeout(() => {
      if (rightOpen) rightDrawerRef.current?.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
      else if (wasOpen) rightToggleRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [rightOpen]);

  const selectedIndex = orderedNavigation.findIndex((item) => item.knowledge_point_id === selectedId);
  const updateSelectionPrompt = () => {
    window.setTimeout(() => {
      if (!onAskNova || !visiblePage) return;
      const selection = window.getSelection();
      const article = articleRef.current;
      if (!selection || !article || selection.rangeCount === 0 || selection.isCollapsed) {
        setSelectionPrompt(null);
        return;
      }
      const range = selection.getRangeAt(0);
      if (!article.contains(selection.anchorNode) || !article.contains(selection.focusNode) || isExcludedSelectionNode(selection.anchorNode) || isExcludedSelectionNode(selection.focusNode)) {
        setSelectionPrompt(null);
        return;
      }
      const selectedFragment = range.cloneContents();
      if (selectedFragment.querySelector("code,button,input,textarea,select")) {
        setSelectionPrompt(null);
        return;
      }
      const text = selection.toString().trim();
      if (text.length < 2 || text.length > 1500) {
        setSelectionPrompt(null);
        return;
      }
      const rect = range.getBoundingClientRect();
      const viewportWidth = window.innerWidth || 640;
      const viewportHeight = window.innerHeight || 640;
      const buttonHeight = 36;
      const gap = 8;
      const aboveTop = rect.top - buttonHeight - gap;
      const top = aboveTop >= 8 ? aboveTop : Math.min(Math.max(8, viewportHeight - buttonHeight - 8), rect.bottom + gap);
      const buttonHalfWidth = 74;
      setSelectionPrompt({ text, heading: headingBeforeSelection(article, range), top, left: Math.min(Math.max(buttonHalfWidth + 8, rect.left + rect.width / 2), viewportWidth - buttonHalfWidth - 8) });
    }, 0);
  };

  const selectKnowledgePoint = (id: string) => {
    setSelectedId(id);
    setLeftOpen(false);
    setRightOpen(false);
    setSelectionPrompt(null);
    window.getSelection()?.removeAllRanges();
    replaceKnowledgeBookUrl({ pointId: id, headingId: null });
  };
  const selectHeading = (heading: { id: string; text: string }) => {
    replaceKnowledgeBookUrl({ pointId: selectedId, headingId: heading.id });
    scrollToHeading(contentRef.current, heading.id);
  };
  const askSelection = () => {
    if (!onAskNova || !selectionPrompt || !visiblePage) return;
    onAskNova(buildSelectionPrompt(visiblePage, selectionPrompt.text, selectionPrompt.heading));
    setSelectionPrompt(null);
    window.getSelection()?.removeAllRanges();
  };

  return <section className="knowledge-book-panel" aria-label="知识教材">
    <header className="knowledge-book-toolbar">
      <div className="knowledge-book-brand"><Menu size={16} /><strong>知识教材</strong>{demoMode && <small className="knowledge-book-demo-badge">演示教材</small>}<span>{visiblePage?.topic_name ?? "教师发布的实操内容"}</span></div>
      <div className="knowledge-book-toolbar-actions">
        <button ref={leftToggleRef} type="button" className="knowledge-book-outline-toggle left" aria-label="打开教材目录" aria-expanded={leftOpen} onClick={() => setLeftOpen((value) => !value)}>{leftOpen ? <PanelLeftClose size={15} /> : <Menu size={15} />}<span>大纲</span></button>
        <button ref={rightToggleRef} type="button" className="knowledge-book-outline-toggle right" aria-label="打开本页目录" aria-expanded={rightOpen} onClick={() => setRightOpen((value) => !value)}>{rightOpen ? <PanelRightClose size={15} /> : <PanelRightClose size={15} />}<span>本页</span></button>
        <button type="button" className="knowledge-book-refresh" aria-label="刷新教材目录" onClick={() => void loadNavigation(true)} disabled={loadingNavigation}><RefreshCw size={15} className={loadingNavigation ? "spin" : undefined} /></button>
      </div>
    </header>
    <div className={["knowledge-book-layout", leftCollapsed && "left-collapsed", rightCollapsed && "right-collapsed"].filter(Boolean).join(" ")}>
      <aside ref={leftDrawerRef} onKeyDown={keepFocusInDrawer} className={["knowledge-book-sidebar", leftOpen && "drawer-open", leftCollapsed && "collapsed"].filter(Boolean).join(" ")} aria-label="教材大纲">
        <button type="button" className="knowledge-book-collapsed-toggle" aria-label="展开教材目录" onClick={() => setLeftCollapsed(false)}><PanelLeftClose size={16} /></button>
        <div className="knowledge-book-sidebar-heading"><strong>课程目录</strong><button type="button" aria-label="收起教材目录" onClick={() => { setLeftOpen(false); setLeftCollapsed(true); }}><X size={15} /></button></div>
        <label className="knowledge-book-search"><Search size={14} /><input value={navigationQuery} onChange={(event) => setNavigationQuery(event.target.value)} placeholder="搜索主题或知识点" aria-label="搜索主题或知识点" /></label>
        {loadingNavigation ? <p className="knowledge-book-muted">正在加载目录……</p> : filteredTopicGroups.length ? filteredTopicGroups.map((group) => {
          const expanded = navigationQuery.trim().length > 0 || expandedTopics.has(group.id);
          return <section className="knowledge-book-topic" key={group.id}>
            <button type="button" className="knowledge-book-topic-heading" aria-expanded={expanded} onClick={() => setExpandedTopics((current) => { const next = new Set(current); if (next.has(group.id)) next.delete(group.id); else next.add(group.id); return next; })}><span>{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<strong>{group.name}</strong></span><small>{group.items.length}</small></button>
            {expanded && <div className="knowledge-book-topic-items">{group.items.map((item) => <button type="button" key={item.knowledge_point_id} className={item.knowledge_point_id === selectedId ? "active" : ""} onClick={() => selectKnowledgePoint(item.knowledge_point_id)}><span>{item.title}</span></button>)}</div>}
          </section>;
        }) : <p className="knowledge-book-muted">{navigationQuery.trim() ? "没有匹配的知识点。" : "教师还没有发布知识教材。"}</p>}
      </aside>
      <main className="knowledge-book-main">
        {error && <div className="knowledge-book-error" role="alert"><span>{error}</span><button type="button" onClick={() => selectedId ? setPageReloadToken((value) => value + 1) : void loadNavigation()}>重试</button></div>}
        <div className="knowledge-book-page-scroll" ref={contentRef} onScroll={handlePageScroll}>
          {loadingPage ? <div className="knowledge-book-state"><span className="spin">⟳</span><p>正在打开知识点……</p></div> : visiblePage ? <article ref={articleRef} tabIndex={-1} className="knowledge-book-article" onPointerUp={updateSelectionPrompt} onKeyUp={updateSelectionPrompt}>
            {!markdownHasTitle && <header className="knowledge-book-fallback-title"><h1>{visiblePage.title}</h1></header>}
            <MarkdownContent headingIds={headingIndex.headingIds} headingIdsByLine={headingIndex.headingIdsByLine} codeActions={codeActions}>{visiblePage.content_markdown}</MarkdownContent>
            <footer className="knowledge-book-page-nav">
              <button type="button" disabled={selectedIndex <= 0} onClick={() => selectKnowledgePoint(orderedNavigation[selectedIndex - 1].knowledge_point_id)}>上一节</button>
              <button type="button" disabled={selectedIndex < 0 || selectedIndex >= orderedNavigation.length - 1} onClick={() => selectKnowledgePoint(orderedNavigation[selectedIndex + 1].knowledge_point_id)}>下一节</button>
            </footer>
          </article> : <div className="knowledge-book-state"><Menu size={26} /><p>{loadingNavigation ? "正在加载教材……" : "从左侧目录选择一个知识点开始阅读。"}</p></div>}
        </div>
      </main>
      <aside ref={rightDrawerRef} onKeyDown={keepFocusInDrawer} className={["knowledge-book-toc", rightOpen && "drawer-open", rightCollapsed && "collapsed"].filter(Boolean).join(" ")} aria-label="本页目录">
        <button type="button" className="knowledge-book-collapsed-toggle" aria-label="展开本页目录" onClick={() => setRightCollapsed(false)}><PanelRightClose size={16} /></button>
        <div className="knowledge-book-sidebar-heading"><strong>本页目录</strong><button type="button" aria-label="收起本页目录" onClick={() => { setRightOpen(false); setRightCollapsed(true); }}><X size={15} /></button></div>
        {headingIndex.headings.length ? <nav>{headingIndex.headings.map((heading) => <button type="button" key={heading.id} className={activeHeadingId === heading.id ? "active" : ""} style={{ paddingLeft: `${12 + (heading.level - 2) * 12}px` }} onClick={() => selectHeading(heading)}>{heading.text}</button>)}</nav> : <p className="knowledge-book-muted">本页暂无小标题。</p>}
      </aside>
    </div>
    {selectionPrompt && onAskNova && <button ref={selectionActionRef} type="button" className="knowledge-book-selection-action" style={{ top: `${selectionPrompt.top}px`, left: `${selectionPrompt.left}px` }} onMouseDown={(event) => event.preventDefault()} onClick={askSelection}>向 Nova 提问</button>}
  </section>;
}
