import { Bold, BookOpen, ChevronDown, Code2, Eye, EyeOff, FilePlus2, Italic, Link2, List, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Pencil, Plus, Quote, Redo2, Save, Trash2, Undo2 } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";

import { MarkdownContent } from "@/modules/student/components/MarkdownContent";
import { ConfirmDialog } from "@/shared/ui/ConfirmDialog";
import { DEFAULT_QUESTION_TYPES, type CourseTopic, type ExerciseBlueprint, type GuidedBlueprint, type KnowledgePoint, type ReviewBlueprint, type RubricPoint } from "@/shared/types";
import { createUuid } from "@/shared/utils/uuid";

type BlueprintStatus = "draft" | "enabled" | "disabled";
type CatalogStatus = CourseTopic["status"] | BlueprintStatus;
type MarkdownFormat = "bold" | "italic" | "code" | "link" | "list" | "quote";

const makeId = (kind: string) => `${kind}_${createUuid().replaceAll("-", "").slice(0, 12)}`;
const statusLabel = (status: CatalogStatus) => status === "enabled" ? "已启用" : status === "disabled" ? "已停用" : "草稿";
const statusClass = (status: CatalogStatus) => status === "enabled" ? "enabled" : status === "disabled" ? "disabled" : "draft";

function StatusPill({ status }: { status: CatalogStatus }) {
  return <span className={`teacher-catalog-status ${statusClass(status)}`}><i aria-hidden="true" />{statusLabel(status)}</span>;
}

function replaceSelection(value: string, start: number, end: number, format: MarkdownFormat) {
  const selected = value.slice(start, end);
  const fallback = selected || (format === "link" ? "链接文字" : format === "code" ? "代码" : "文本");
  const replacement = format === "bold" ? `**${fallback}**`
    : format === "italic" ? `*${fallback}*`
      : format === "code" ? `\`\`\`python\n${fallback}\n\`\`\``
        : format === "link" ? `[${fallback}](https://)`
          : format === "list" ? fallback.split("\n").map((line) => `- ${line}`).join("\n")
            : fallback.split("\n").map((line) => `> ${line}`).join("\n");
  return { value: `${value.slice(0, start)}${replacement}${value.slice(end)}`, start, end: start + replacement.length };
}

const markdownTools: Array<{ format: MarkdownFormat; label: string; shortcut?: string; icon: typeof Bold }> = [
  { format: "bold", label: "加粗", shortcut: "Ctrl/Cmd+B", icon: Bold },
  { format: "italic", label: "斜体", shortcut: "Ctrl/Cmd+I", icon: Italic },
  { format: "code", label: "代码块", icon: Code2 },
  { format: "link", label: "链接", shortcut: "Ctrl/Cmd+K", icon: Link2 },
  { format: "list", label: "列表", icon: List },
  { format: "quote", label: "引用", icon: Quote },
];

function questionTypesForPoint(point: KnowledgePoint | undefined): string[] {
  const configured = point?.question_types?.map((value) => value.trim()).filter(Boolean);
  return configured?.length ? Array.from(new Set(configured)) : [...DEFAULT_QUESTION_TYPES];
}

function MarkdownEditor({ label, value, onChange, placeholder, inputAriaLabel, preview }: { label: string; value: string; onChange: (value: string) => void; placeholder: string; inputAriaLabel?: string; preview: boolean }) {
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const history = useRef<{ past: string[]; future: string[]; value: string }>({ past: [], future: [], value });

  useEffect(() => {
    if (history.current.value === value) return;
    history.current = { past: [], future: [], value };
  }, [value]);

  const update = (next: string) => {
    if (next === history.current.value) return;
    history.current.past.push(history.current.value);
    if (history.current.past.length > 100) history.current.past.shift();
    history.current.future = [];
    history.current.value = next;
    onChange(next);
  };

  const moveHistory = (direction: "undo" | "redo") => {
    const current = history.current;
    const next = direction === "undo" ? current.past.pop() : current.future.shift();
    if (next === undefined) return;
    if (direction === "undo") current.future.unshift(current.value);
    else current.past.push(current.value);
    current.value = next;
    onChange(next);
  };

  const apply = (format: MarkdownFormat) => {
    const editor = editorRef.current;
    const start = editor?.selectionStart ?? value.length;
    const end = editor?.selectionEnd ?? start;
    const result = replaceSelection(value, start, end, format);
    update(result.value);
    window.requestAnimationFrame(() => {
      editorRef.current?.focus();
      editorRef.current?.setSelectionRange(result.start, result.end);
    });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!(event.ctrlKey || event.metaKey)) return;
    const key = event.key.toLowerCase();
    if (key === "z") { event.preventDefault(); moveHistory("undo"); return; }
    if (key === "y") { event.preventDefault(); moveHistory("redo"); return; }
    const format: MarkdownFormat | undefined = key === "b" ? "bold" : key === "i" ? "italic" : key === "k" ? "link" : undefined;
    if (format) { event.preventDefault(); apply(format); }
  };

  return <section className="teacher-book-source teacher-catalog-markdown-editor" aria-label={`${label}编辑器`}>
    <div className="teacher-book-markdown-toolbar">
      <span>{label}</span>
      <div>
        <button type="button" disabled={preview} title="撤销（Ctrl/Cmd+Z）" aria-label="撤销" onMouseDown={(event) => event.preventDefault()} onClick={() => moveHistory("undo")}><Undo2 size={14} /></button>
        <button type="button" disabled={preview} title="重做（Ctrl/Cmd+Y）" aria-label="重做" onMouseDown={(event) => event.preventDefault()} onClick={() => moveHistory("redo")}><Redo2 size={14} /></button>
        <span className="teacher-catalog-toolbar-divider" aria-hidden="true" />
        {markdownTools.map(({ format, label: toolLabel, shortcut, icon: Icon }) => <button key={format} type="button" disabled={preview} title={shortcut ? `${toolLabel}（${shortcut}）` : toolLabel} aria-label={toolLabel} onMouseDown={(event) => event.preventDefault()} onClick={() => apply(format)}><Icon size={14} />{toolLabel}</button>)}
      </div>
      <small>支持 Markdown、代码块和 Ctrl/Cmd+Z、Y；此处不上传图片，也不生成小标题目录。</small>
    </div>
    {preview ? <div className="teacher-book-preview teacher-catalog-markdown-preview"><MarkdownContent>{value || "暂无内容"}</MarkdownContent></div> : <textarea ref={editorRef} className="teacher-book-textarea teacher-catalog-markdown-textarea" aria-label={inputAriaLabel ?? `${label} Markdown`} value={value} onChange={(event) => update(event.target.value)} onKeyDown={onKeyDown} placeholder={placeholder} />}
  </section>;
}

type SaveProps = { saving?: boolean; saveMessage?: string; onSave?: () => void };

function closeDirectoryMenu(event: MouseEvent<HTMLButtonElement>) {
  event.currentTarget.closest("details")?.removeAttribute("open");
}

function DirectoryMenu({ label, children }: { label: string; children: ReactNode }) {
  return <details className="teacher-book-tree-menu"><summary role="button" aria-label={label}><MoreHorizontal size={16} /></summary><div>{children}</div></details>;
}

type CatalogEditorRender = (preview: boolean, onTogglePreview: () => void) => ReactNode;

function CatalogEditorLayout({ eyebrow, title, description, sidebarTitle, count, search, onSearch, createLabel, onCreate, canCreate = true, directory, selected, selectedTitle, selectedMeta, status, previewKey, renderContent, overlays, saveProps }: {
  eyebrow: string;
  title: string;
  description: string;
  sidebarTitle: string;
  count: number;
  search: string;
  onSearch: (value: string) => void;
  createLabel?: string;
  onCreate?: () => void;
  canCreate?: boolean;
  directory: ReactNode;
  selected: boolean;
  selectedTitle: string;
  selectedMeta: string;
  status?: CatalogStatus;
  previewKey: string;
  renderContent: CatalogEditorRender;
  overlays?: ReactNode;
  saveProps?: SaveProps;
}) {
  const [directoryCollapsed, setDirectoryCollapsed] = useState(false);
  const [previewState, setPreviewState] = useState({ key: previewKey, value: false });
  const preview = previewState.key === previewKey ? previewState.value : false;
  const togglePreview = () => setPreviewState({ key: previewKey, value: !preview });

  useEffect(() => {
    const closeOpenMenus = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      document.querySelectorAll<HTMLDetailsElement>(".teacher-catalog-layout .teacher-book-tree-menu[open]").forEach((menu) => {
        if (!menu.contains(target)) menu.open = false;
      });
    };
    document.addEventListener("pointerdown", closeOpenMenus);
    return () => document.removeEventListener("pointerdown", closeOpenMenus);
  }, []);

  return <div className="teacher-catalog-page">
    <section className="teacher-catalog-page-summary"><div><span className="teacher-eyebrow">{eyebrow}</span><h2>{title}</h2><p>{description}</p></div><BookOpen size={42} /></section>
    <div className={`teacher-book-layout teacher-catalog-layout ${directoryCollapsed ? "directory-collapsed" : ""}`}>
      <aside className={`teacher-book-tree teacher-catalog-directory ${directoryCollapsed ? "collapsed" : ""}`} aria-label={`${sidebarTitle}目录`}>
        <button type="button" className="teacher-book-tree-collapsed-toggle" aria-label={`展开${sidebarTitle}目录`} onClick={() => setDirectoryCollapsed(false)}><PanelLeftOpen size={16} /></button>
        <div className="teacher-book-tree-heading"><div><strong>{sidebarTitle}目录</strong><small>{count} 个项目</small></div><div className="teacher-book-tree-actions">{onCreate && <DirectoryMenu label={`${sidebarTitle}目录选项`}><button type="button" onClick={(event) => { closeDirectoryMenu(event); onCreate(); }} disabled={!canCreate}><Plus size={14} />{createLabel}</button></DirectoryMenu>}<button type="button" aria-label={`收起${sidebarTitle}目录`} onClick={() => setDirectoryCollapsed(true)}><PanelLeftClose size={15} /></button></div></div>
        <label className="teacher-book-tree-search"><span aria-hidden="true">⌕</span><input type="search" aria-label={`搜索${sidebarTitle}`} value={search} onChange={(event) => onSearch(event.target.value)} placeholder={`搜索${sidebarTitle}`} /></label>
        <div className="teacher-book-tree-groups teacher-catalog-directory-list">{directory}</div>
      </aside>
      <main className="teacher-book-workspace teacher-catalog-workspace">
        {selected ? <>
          <header className="teacher-book-page-heading teacher-catalog-workspace-header"><div className="teacher-book-page-heading-info"><div className="teacher-book-page-breadcrumb"><span className="teacher-book-page-topic">{eyebrow}</span><span className="teacher-book-page-chevron" aria-hidden="true">›</span><h3>{selectedTitle}</h3></div><span className="teacher-book-version"><strong>{sidebarTitle}</strong><span aria-hidden="true">·</span><span>{selectedMeta}</span>{status && <StatusPill status={status} />}</span></div><div className="teacher-book-page-actions teacher-catalog-workspace-actions"><button type="button" className={preview ? "active" : ""} onClick={togglePreview}><Eye size={15} />{preview ? "返回编辑" : "预览正文"}</button>{saveProps?.onSave && <button type="button" className="teacher-book-publish" onClick={saveProps.onSave} disabled={saveProps.saving}><Save size={15} />{saveProps.saving ? "正在保存…" : "保存教学目录"}</button>}</div></header>
          <div className="teacher-book-workspace-scroll teacher-catalog-workspace-scroll">{renderContent(preview, togglePreview)}{overlays}</div>
          {saveProps?.saveMessage && <p className="teacher-catalog-save-message" role="status">{saveProps.saveMessage}</p>}
        </> : <div className="teacher-catalog-empty"><BookOpen size={30} /><strong>还没有可编辑项目</strong><p>{onCreate && canCreate ? `点击左上角“${createLabel}”开始创建。` : "请在知识点旁的“···”菜单中新建蓝图。"}</p></div>}
      </main>
    </div>
  </div>;
}

function sortedLast<T>(items: T[], isDisabled: (item: T) => boolean) {
  return items.map((item, index) => ({ item, index })).sort((left, right) => Number(isDisabled(left.item)) - Number(isDisabled(right.item)) || left.index - right.index).map(({ item }) => item);
}

type TopicSelection = { kind: "topic"; topicId: string } | { kind: "point"; topicId: string; pointId: string };

function topicMatches(topic: CourseTopic, query: string) {
  if (!query) return true;
  return topic.name.toLocaleLowerCase().includes(query) || topic.description.toLocaleLowerCase().includes(query) || topic.knowledge_points.some((point) => point.name.toLocaleLowerCase().includes(query) || point.markdown.toLocaleLowerCase().includes(query));
}

function TopicDirectory({ topics, selection, query, collapsedTopicIds, onSelect, onToggle, onAddPoint, onEditTopic, onToggleTopic, onDeleteTopic, onEditPoint, onTogglePoint, onDeletePoint }: { topics: CourseTopic[]; selection: TopicSelection; query: string; collapsedTopicIds: string[]; onSelect: (selection: TopicSelection) => void; onToggle: (topicId: string) => void; onAddPoint: (topicId: string) => void; onEditTopic: (topicId: string) => void; onToggleTopic: (topicId: string) => void; onDeleteTopic: (topic: CourseTopic) => void; onEditPoint: (topicId: string, pointId: string) => void; onTogglePoint: (topicId: string, pointId: string) => void; onDeletePoint: (topicId: string, pointId: string, name: string) => void }) {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleTopics = sortedLast(topics, (topic) => topic.status === "disabled").filter((topic) => topicMatches(topic, normalizedQuery));
  if (!visibleTopics.length) return <p className="teacher-catalog-empty-inline">没有匹配的主题或知识点。</p>;
  return <>{visibleTopics.map((topic) => {
    const expanded = normalizedQuery.length > 0 || !collapsedTopicIds.includes(topic.id);
    const matchingPoints = normalizedQuery && !topic.name.toLocaleLowerCase().includes(normalizedQuery) ? topic.knowledge_points.filter((point) => point.name.toLocaleLowerCase().includes(normalizedQuery) || point.markdown.toLocaleLowerCase().includes(normalizedQuery)) : topic.knowledge_points;
    const points = sortedLast(matchingPoints, (point) => point.status === "disabled");
    return <section className={`teacher-book-tree-topic ${topic.status === "disabled" ? "is-disabled" : ""}`} key={topic.id}>
      <div className="teacher-book-tree-topic-heading"><button type="button" className={`teacher-book-topic-toggle ${topic.status === "disabled" ? "is-disabled" : ""}`} aria-label={`${expanded ? "折叠" : "展开"}主题 ${topic.name}`} aria-expanded={expanded} onClick={() => onToggle(topic.id)}><ChevronDown size={14} /><span>{topic.name || "未命名主题"}</span><small className="teacher-book-tree-count">{topic.knowledge_points.length}</small></button><DirectoryMenu label={`${topic.name}目录选项`}><button type="button" onClick={(event) => { closeDirectoryMenu(event); onAddPoint(topic.id); }}><Plus size={14} />新增知识点</button><button type="button" onClick={(event) => { closeDirectoryMenu(event); onEditTopic(topic.id); }}><Pencil size={14} />编辑主题</button><button type="button" onClick={(event) => { closeDirectoryMenu(event); onToggleTopic(topic.id); }}>{topic.status === "enabled" ? <EyeOff size={14} /> : <Eye size={14} />}{topic.status === "enabled" ? "停用主题" : "启用主题"}</button><button type="button" className="danger" onClick={(event) => { closeDirectoryMenu(event); onDeleteTopic(topic); }}><Trash2 size={14} />删除主题</button></DirectoryMenu></div>
      {expanded && <div className="teacher-book-tree-topic-items"><div className={`teacher-book-tree-point ${selection.kind === "topic" && selection.topicId === topic.id ? "active" : ""}`}><button type="button" className="teacher-book-tree-point-main" aria-label={`选择主题 ${topic.name}`} onClick={() => onSelect({ kind: "topic", topicId: topic.id })}><BookOpen size={14} /><span>主题设置</span></button><DirectoryMenu label={`${topic.name}主题设置选项`}><button type="button" onClick={(event) => { closeDirectoryMenu(event); onEditTopic(topic.id); }}><Pencil size={14} />编辑主题</button><button type="button" onClick={(event) => { closeDirectoryMenu(event); onToggleTopic(topic.id); }}>{topic.status === "enabled" ? <EyeOff size={14} /> : <Eye size={14} />}{topic.status === "enabled" ? "停用主题" : "启用主题"}</button><button type="button" className="danger" onClick={(event) => { closeDirectoryMenu(event); onDeleteTopic(topic); }}><Trash2 size={14} />删除主题</button></DirectoryMenu></div>{points.map((point) => <div className={`teacher-book-tree-point ${selection.kind === "point" && selection.pointId === point.id ? "active" : ""} ${point.status === "disabled" ? "is-disabled" : ""}`} key={point.id}><button type="button" className="teacher-book-tree-point-main" aria-label={`选择知识点 ${point.name}`} onClick={() => onSelect({ kind: "point", topicId: topic.id, pointId: point.id })}><span>{point.name || "未命名知识点"}</span></button><DirectoryMenu label={`${point.name}选项`}><button type="button" onClick={(event) => { closeDirectoryMenu(event); onEditPoint(topic.id, point.id); }}><Pencil size={14} />编辑知识点</button><button type="button" onClick={(event) => { closeDirectoryMenu(event); onTogglePoint(topic.id, point.id); }}>{point.status === "enabled" ? <EyeOff size={14} /> : <Eye size={14} />}{point.status === "enabled" ? "停用知识点" : "启用知识点"}</button><button type="button" className="danger" onClick={(event) => { closeDirectoryMenu(event); onDeletePoint(topic.id, point.id, point.name); }}><Trash2 size={14} />删除知识点</button></DirectoryMenu></div>)}</div>}
    </section>;
  })}</>;
}

function QuestionTypeSettings({ point, onChange }: { point: KnowledgePoint; onChange: (questionTypes: string[]) => void }) {
  const [customType, setCustomType] = useState("");
  const selectedTypes = questionTypesForPoint(point);
  const availableTypes = Array.from(new Set([...DEFAULT_QUESTION_TYPES, ...selectedTypes]));
  const toggleType = (type: string) => {
    const next = selectedTypes.includes(type) ? selectedTypes.filter((value) => value !== type) : [...selectedTypes, type];
    if (next.length > 0) onChange(next);
  };
  const addCustomType = () => {
    const value = customType.trim();
    if (!value || selectedTypes.includes(value)) return;
    onChange([...selectedTypes, value]);
    setCustomType("");
  };
  return <fieldset className="teacher-catalog-question-types"><legend>知识点可用题型</legend><p>蓝图只能选择这里启用的题型；至少保留一种题型。</p><div className="teacher-catalog-question-type-options">{availableTypes.map((type) => <label key={type}><input type="checkbox" checked={selectedTypes.includes(type)} disabled={selectedTypes.length === 1 && selectedTypes.includes(type)} onChange={() => toggleType(type)} />{type}</label>)}</div><div className="teacher-catalog-custom-type"><input aria-label="自定义题型" value={customType} onChange={(event) => setCustomType(event.target.value)} placeholder="添加自定义题型" onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addCustomType(); } }} /><button type="button" onClick={addCustomType} disabled={!customType.trim()}><Plus size={14} />添加</button></div></fieldset>;
}

export function TopicCatalogEditor({ topics, onChange, saveProps }: { topics: CourseTopic[]; onChange: (topics: CourseTopic[]) => void; saveProps?: SaveProps }) {
  const [selection, setSelection] = useState<TopicSelection>(() => topics[0] ? { kind: "topic", topicId: topics[0].id } : { kind: "topic", topicId: "" });
  const [query, setQuery] = useState("");
  const [collapsedTopicIds, setCollapsedTopicIds] = useState<string[]>(() => topics.map((topic) => topic.id));
  const [deleteTarget, setDeleteTarget] = useState<{ kind: "topic" | "point"; topicId: string; pointId?: string; name: string } | null>(null);
  const selectedTopicByState = topics.find((topic) => topic.id === selection.topicId);
  const selectedPointByState = selection.kind === "point" ? selectedTopicByState?.knowledge_points.find((point) => point.id === selection.pointId) : undefined;
  const effectiveSelection: TopicSelection = selectedTopicByState && (selection.kind === "topic" || selectedPointByState) ? selection : topics[0] ? { kind: "topic", topicId: topics[0].id } : { kind: "topic", topicId: "" };
  const selectedTopic = topics.find((topic) => topic.id === effectiveSelection.topicId);
  const selectedPoint = effectiveSelection.kind === "point" ? selectedTopic?.knowledge_points.find((point) => point.id === effectiveSelection.pointId) : undefined;

  const updateTopic = (next: CourseTopic) => onChange(topics.map((topic) => topic.id === next.id ? next : topic));
  const createTopic = () => { const topic: CourseTopic = { id: makeId("topic"), name: "新建主题", description: "", status: "enabled", knowledge_points: [] }; onChange([...topics, topic]); setSelection({ kind: "topic", topicId: topic.id }); };
  const addPoint = (topicId: string) => { const topic = topics.find((value) => value.id === topicId); if (!topic) return; const point: KnowledgePoint = { id: makeId("kp"), name: "新知识点", markdown: "", status: "enabled", sort_order: topic.knowledge_points.length, question_types: [...DEFAULT_QUESTION_TYPES] }; updateTopic({ ...topic, knowledge_points: [...topic.knowledge_points, point] }); setCollapsedTopicIds((current) => current.filter((id) => id !== topicId)); setSelection({ kind: "point", topicId, pointId: point.id }); };
  const onEditTopic = (topicId: string) => setSelection({ kind: "topic", topicId });
  const onEditPoint = (topicId: string, pointId: string) => setSelection({ kind: "point", topicId, pointId });
  const currentTitle = selectedPoint?.name || selectedTopic?.name || "选择主题";
  const currentMeta = selectedPoint ? `${selectedTopic?.name ?? ""} · 知识点说明` : "主题设置 · 知识点目录";
  const currentStatus = selectedPoint?.status ?? selectedTopic?.status;
  const toggleTopic = (topicId: string) => { const topic = topics.find((value) => value.id === topicId); if (topic) updateTopic({ ...topic, status: topic.status === "enabled" ? "disabled" : "enabled" }); };
  const togglePoint = (topicId: string, pointId: string) => { const topic = topics.find((value) => value.id === topicId); if (topic) updateTopic({ ...topic, knowledge_points: topic.knowledge_points.map((point) => point.id === pointId ? { ...point, status: point.status === "enabled" ? "disabled" : "enabled" } : point) }); };
  const deleteTopic = (topic: CourseTopic) => setDeleteTarget({ kind: "topic", topicId: topic.id, name: topic.name });
  const deletePoint = (topicId: string, pointId: string, name: string) => setDeleteTarget({ kind: "point", topicId, pointId, name });
  const confirmRemove = () => { if (!deleteTarget) return; if (deleteTarget.kind === "topic") onChange(topics.filter((topic) => topic.id !== deleteTarget.topicId)); else { const topic = topics.find((value) => value.id === deleteTarget.topicId); if (topic && deleteTarget.pointId) updateTopic({ ...topic, knowledge_points: topic.knowledge_points.filter((point) => point.id !== deleteTarget.pointId) }); } setDeleteTarget(null); };

  return <CatalogEditorLayout eyebrow="COURSE CATALOG" title="主题与知识点" description="维护学生学习范围与智能体可引用的知识边界。所有修改先保存在当前目录草稿，点击右上角保存后通过教师接口同步。" sidebarTitle="主题与知识点" count={topics.reduce((total, topic) => total + 1 + topic.knowledge_points.length, 0)} search={query} onSearch={setQuery} createLabel="新建主题" onCreate={createTopic} directory={<TopicDirectory topics={topics} selection={effectiveSelection} query={query} collapsedTopicIds={collapsedTopicIds} onSelect={setSelection} onToggle={(topicId) => setCollapsedTopicIds((current) => current.includes(topicId) ? current.filter((id) => id !== topicId) : [...current, topicId])} onAddPoint={addPoint} onEditTopic={onEditTopic} onToggleTopic={toggleTopic} onDeleteTopic={deleteTopic} onEditPoint={onEditPoint} onTogglePoint={togglePoint} onDeletePoint={deletePoint} />} selected={Boolean(selectedTopic || selectedPoint)} selectedTitle={currentTitle} selectedMeta={currentMeta} status={currentStatus} previewKey={`${effectiveSelection.kind}-${effectiveSelection.topicId}-${effectiveSelection.kind === "point" ? effectiveSelection.pointId : ""}`} renderContent={(preview: boolean) => selectedPoint && selectedTopic ? <div className="teacher-catalog-editor-content"><div className="teacher-catalog-field-grid"><label>知识点名称<input aria-label="知识点名称" value={selectedPoint.name} onChange={(event) => updateTopic({ ...selectedTopic, knowledge_points: selectedTopic.knowledge_points.map((point) => point.id === selectedPoint.id ? { ...point, name: event.target.value } : point) })} placeholder="例如：缩放点积注意力" /></label><div className="teacher-catalog-info-card"><span>所属主题</span><strong>{selectedTopic.name}</strong><small>编辑、启停和删除请使用左侧项目旁的“···”菜单</small></div></div><QuestionTypeSettings point={selectedPoint} onChange={(question_types) => updateTopic({ ...selectedTopic, knowledge_points: selectedTopic.knowledge_points.map((point) => point.id === selectedPoint.id ? { ...point, question_types } : point) })} /><MarkdownEditor key={`point-${selectedPoint.id}`} label="知识点说明" preview={preview} value={selectedPoint.markdown} onChange={(markdown) => updateTopic({ ...selectedTopic, knowledge_points: selectedTopic.knowledge_points.map((point) => point.id === selectedPoint.id ? { ...point, markdown } : point) })} placeholder="用 Markdown 写清概念边界、必须覆盖的内容与容易混淆处。" /></div> : selectedTopic ? <div className="teacher-catalog-editor-content"><div className="teacher-catalog-field-grid"><label>主题名称<input aria-label="主题名称" value={selectedTopic.name} onChange={(event) => updateTopic({ ...selectedTopic, name: event.target.value })} placeholder="例如：Transformer" /></label><div className="teacher-catalog-info-card"><span>目录规模</span><strong>{selectedTopic.knowledge_points.length} 个知识点</strong><small>新增、编辑、启停和删除请使用左侧主题旁的“···”菜单</small></div></div><MarkdownEditor key={`topic-${selectedTopic.id}`} label="主题说明" preview={preview} value={selectedTopic.description} onChange={(description) => updateTopic({ ...selectedTopic, description })} placeholder="说明该主题的学习范围、前置知识和教学目标。" /><div className="teacher-catalog-related-heading"><div><strong>主题下的知识点</strong><small>选择左侧项目即可直接编辑；停用项目会自动排列到目录末尾。</small></div></div></div> : null} overlays={deleteTarget && <ConfirmDialog open title={`删除${deleteTarget.kind === "topic" ? "主题" : "知识点"}“${deleteTarget.name || "未命名"}”？`} description={deleteTarget.kind === "topic" ? "该主题及其知识点会从当前教师目录移除；已有学习记录不会受影响。" : "该知识点会从当前教师目录移除；已有教材内容和学习记录不会受影响。"} onClose={() => setDeleteTarget(null)} onConfirm={confirmRemove} />} saveProps={saveProps} />;
}

type Blueprint = ExerciseBlueprint | ReviewBlueprint | GuidedBlueprint;
type BlueprintKind = "exercise" | "review" | "guided";
const blueprintName = (kind: BlueprintKind) => kind === "exercise" ? "出题蓝图" : kind === "review" ? "复习蓝图" : "引导蓝图";
const blueprintField = (kind: BlueprintKind) => kind === "guided" ? "引导方向" : "Markdown 指令";

function blueprintMatches(item: Blueprint, topic: CourseTopic | undefined, query: string) {
  if (!query) return true;
  const point = topic?.knowledge_points.find((value) => value.id === item.knowledge_point_id);
  const content = "guidance" in item ? item.guidance : item.instructions;
  return `${item.name} ${content} ${topic?.name ?? ""} ${point?.name ?? ""}`.toLocaleLowerCase().includes(query);
}

function BlueprintDirectory({ kind, topics, blueprints, selectedId, query, collapsedTopicIds, onSelect, onToggle, onCreate, onToggleStatus, onDelete }: { kind: BlueprintKind; topics: CourseTopic[]; blueprints: Blueprint[]; selectedId: string; query: string; collapsedTopicIds: string[]; onSelect: (id: string) => void; onToggle: (id: string) => void; onCreate: (topicId: string, pointId: string) => void; onToggleStatus: (item: Blueprint) => void; onDelete: (item: Blueprint) => void }) {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleTopics = sortedLast(topics, (topic) => topic.status === "disabled").filter((topic) => {
    if (!normalizedQuery) return true;
    return topic.name.toLocaleLowerCase().includes(normalizedQuery) || topic.knowledge_points.some((point) => point.name.toLocaleLowerCase().includes(normalizedQuery) || blueprints.some((item) => item.topic_id === topic.id && item.knowledge_point_id === point.id && blueprintMatches(item, topic, normalizedQuery)));
  });
  if (!visibleTopics.length) return <p className="teacher-catalog-empty-inline">还没有匹配的蓝图。</p>;
  return <>{visibleTopics.map((topic) => {
    const expanded = normalizedQuery.length > 0 || !collapsedTopicIds.includes(topic.id);
    const matchingPoints = topic.knowledge_points.filter((point) => !normalizedQuery || topic.name.toLocaleLowerCase().includes(normalizedQuery) || point.name.toLocaleLowerCase().includes(normalizedQuery) || blueprints.some((item) => item.topic_id === topic.id && item.knowledge_point_id === point.id && blueprintMatches(item, topic, normalizedQuery)));
    const visiblePoints = sortedLast(matchingPoints, (point) => point.status === "disabled");
    return <section className={`teacher-book-tree-topic ${topic.status === "disabled" ? "is-disabled" : ""}`} key={topic.id}>
      <div className="teacher-book-tree-topic-heading">
        <button type="button" className={`teacher-book-topic-toggle ${topic.status === "disabled" ? "is-disabled" : ""}`} aria-label={`${expanded ? "折叠" : "展开"}主题 ${topic.name}`} aria-expanded={expanded} onClick={() => onToggle(topic.id)}><ChevronDown size={14} /><span>{topic.name || "未命名主题"}</span><small className="teacher-book-tree-count">{blueprints.filter((item) => item.topic_id === topic.id).length}</small></button>
      </div>
      {expanded && <div className="teacher-book-tree-topic-items">{visiblePoints.map((point) => <div className={`teacher-catalog-blueprint-point ${point.status === "disabled" ? "is-disabled" : ""}`} key={point.id}>
        <div className="teacher-catalog-blueprint-point-heading"><span>{point.name || "未命名知识点"}</span><DirectoryMenu label={`${point.name}${blueprintName(kind)}选项`}><button type="button" onClick={(event) => { closeDirectoryMenu(event); onCreate(topic.id, point.id); }} disabled={topic.status === "disabled" || point.status === "disabled"}><Plus size={14} />新建{blueprintName(kind)}</button></DirectoryMenu></div>
        {sortedLast(blueprints.filter((item) => item.topic_id === topic.id && item.knowledge_point_id === point.id && blueprintMatches(item, topic, normalizedQuery)), (item) => item.status === "disabled").map((item) => <div className={`teacher-book-tree-point ${selectedId === item.id ? "active" : ""} ${item.status === "disabled" ? "is-disabled" : ""}`} key={item.id}><button type="button" className="teacher-book-tree-point-main" aria-label={`选择${blueprintName(kind)} ${item.name}`} onClick={() => onSelect(item.id)}><FilePlus2 size={13} /><span>{item.name || "未命名蓝图"}</span></button><DirectoryMenu label={`${item.name}选项`}><button type="button" onClick={(event) => { closeDirectoryMenu(event); onSelect(item.id); }}><Pencil size={14} />编辑{blueprintName(kind)}</button><button type="button" onClick={(event) => { closeDirectoryMenu(event); onToggleStatus(item); }}>{item.status === "enabled" ? <EyeOff size={14} /> : <Eye size={14} />}{item.status === "enabled" ? `停用${blueprintName(kind)}` : `启用${blueprintName(kind)}`}</button><button type="button" className="danger" onClick={(event) => { closeDirectoryMenu(event); onDelete(item); }}><Trash2 size={14} />删除{blueprintName(kind)}</button></DirectoryMenu></div>)}
      </div>)}</div>}
    </section>;
  })}</>;
}

function RubricEditor({ rubric, onChange }: { rubric: RubricPoint[]; onChange: (rubric: RubricPoint[]) => void }) {
  return <fieldset className="teacher-catalog-rubric"><legend>评分标准</legend>{rubric.length ? rubric.map((item, index) => <div key={item.id ?? index}><input aria-label={`评分标准 ${index + 1}`} value={item.criterion} placeholder="例如：正确解释注意力权重" onChange={(event) => onChange(rubric.map((value, i) => i === index ? { ...value, criterion: event.target.value } : value))} /><input aria-label={`评分权重 ${index + 1}`} type="number" min="0" max="100" value={item.weight} onChange={(event) => onChange(rubric.map((value, i) => i === index ? { ...value, weight: Number(event.target.value) } : value))} /><button type="button" aria-label={`删除评分标准 ${index + 1}`} onClick={() => onChange(rubric.filter((_, i) => i !== index))}><Trash2 size={14} /></button></div>) : <p>启用蓝图前至少添加一条评分标准。</p>}<button type="button" onClick={() => onChange([...rubric, { criterion: "", weight: 0 }])}><Plus size={14} />添加评分标准</button></fieldset>;
}

function BlueprintEditor({ kind, topics, blueprints, onChange, saveProps }: { kind: BlueprintKind; topics: CourseTopic[]; blueprints: Blueprint[]; onChange: (items: Blueprint[]) => void; saveProps?: SaveProps }) {
  const [selectedId, setSelectedId] = useState(blueprints[0]?.id ?? "");
  const [query, setQuery] = useState("");
  const [collapsedTopicIds, setCollapsedTopicIds] = useState<string[]>(() => topics.map((topic) => topic.id));
  const [deleteTarget, setDeleteTarget] = useState<Blueprint | null>(null);
  const selected = blueprints.find((item) => item.id === selectedId) ?? blueprints[0];
  const topic = topics.find((item) => item.id === selected?.topic_id);
  const point = topic?.knowledge_points.find((item) => item.id === selected?.knowledge_point_id);
  const questionSelected = selected && "instructions" in selected ? selected : undefined;

  const update = (next: Blueprint) => onChange(blueprints.map((item) => item.id === next.id ? next : item));
  const create = (topicId: string, pointId: string) => {
    const targetTopic = topics.find((item) => item.id === topicId);
    const targetPoint = targetTopic?.knowledge_points.find((item) => item.id === pointId);
    if (!targetTopic || !targetPoint || targetTopic.status === "disabled" || targetPoint.status === "disabled") return;
    const base = { id: makeId(kind), name: `${targetPoint.name || "未命名知识点"} · ${blueprintName(kind)}`, topic_id: targetTopic.id, knowledge_point_id: targetPoint.id, status: "draft" as const };
    const item: Blueprint = kind === "guided" ? { ...base, guidance: "请补充教师希望模型采用的引导路径、提问顺序和应聚焦的误区。" } : { ...base, instructions: "请补充这张蓝图的题干范围、数据要求与讲评规则。", question_type: questionTypesForPoint(targetPoint)[0], rubric: [], ...(kind === "review" ? { exercise_blueprint_id: null } : {}) } as Blueprint;
    setCollapsedTopicIds((current) => current.filter((id) => id !== topicId));
    onChange([...blueprints, item]);
    setSelectedId(item.id);
  };
  const confirmRemove = () => { if (deleteTarget) { onChange(blueprints.filter((item) => item.id !== deleteTarget.id)); setDeleteTarget(null); } };
  const instructionValue = kind === "guided" ? (selected && "guidance" in selected ? selected.guidance : "") : (questionSelected?.instructions ?? "");
  const selectionMeta = topic && point ? `${topic.name} · ${point.name}` : "等待关联主题和知识点";
  const configuredQuestionTypes = questionTypesForPoint(point);
  const questionTypes = questionSelected && !configuredQuestionTypes.includes(questionSelected.question_type)
    ? [questionSelected.question_type, ...configuredQuestionTypes]
    : configuredQuestionTypes;

  return <CatalogEditorLayout eyebrow={kind === "guided" ? "GUIDED MODE" : kind === "review" ? "REVIEW BLUEPRINT" : "EXERCISE BLUEPRINT"} title={blueprintName(kind)} description={kind === "guided" ? "为智能体定义分步追问和启发路径；教师保存后，学生引导模式会使用启用的蓝图。" : `为每个知识点维护${kind === "review" ? "复习" : "练习"}生成规则。右侧编辑区支持 Markdown，保存后由后端校验并同步到学生端。`} sidebarTitle={blueprintName(kind)} count={blueprints.length} search={query} onSearch={setQuery} directory={<BlueprintDirectory kind={kind} topics={topics} blueprints={blueprints} selectedId={selected?.id ?? ""} query={query} collapsedTopicIds={collapsedTopicIds} onSelect={setSelectedId} onToggle={(topicId) => setCollapsedTopicIds((current) => current.includes(topicId) ? current.filter((id) => id !== topicId) : [...current, topicId])} onCreate={create} onToggleStatus={(item) => update({ ...item, status: item.status === "enabled" ? "disabled" : "enabled" })} onDelete={setDeleteTarget} />} selected={Boolean(selected)} selectedTitle={selected?.name || `未命名${blueprintName(kind)}`} selectedMeta={selectionMeta} status={selected?.status} previewKey={selected?.id ?? ""} renderContent={(preview: boolean) => selected && <div className="teacher-catalog-editor-content"><div className="teacher-catalog-field-grid">{kind !== "guided" && questionSelected && <label>题型<select aria-label={`${kind}题型`} value={questionSelected.question_type} onChange={(event) => update({ ...questionSelected, question_type: event.target.value })}>{questionTypes.map((value) => <option value={value} key={value}>{value}{value === questionSelected.question_type && !configuredQuestionTypes.includes(value) ? "（当前题型已停用）" : ""}</option>)}</select></label>}</div><MarkdownEditor key={`${kind}-${selected.id}`} label={blueprintField(kind)} preview={preview} inputAriaLabel={kind === "guided" ? "guidedMarkdown 指令" : `${kind}Markdown 指令`} value={instructionValue} onChange={(value) => update(kind === "guided" ? { ...selected, guidance: value } as GuidedBlueprint : { ...selected, instructions: value } as ExerciseBlueprint | ReviewBlueprint)} placeholder={kind === "guided" ? "描述模型应该怎样分步追问、提示和收束。" : "描述题干范围、数据要求、解题方向与讲评规则。"} />{kind !== "guided" && questionSelected && <RubricEditor rubric={questionSelected.rubric} onChange={(rubric) => update({ ...questionSelected, rubric })} />}<p className="teacher-catalog-form-note"><Save size={14} />当前编辑只更新本地目录草稿；点击右上角“保存教学目录”后才会调用教师目录接口。</p></div>} overlays={deleteTarget && <ConfirmDialog open title={`删除${blueprintName(kind)}“${deleteTarget.name || "未命名"}”？`} description="该蓝图会从当前教师目录移除；已有学习记录和已生成题目不会受影响。" onClose={() => setDeleteTarget(null)} onConfirm={confirmRemove} />} saveProps={saveProps} />;
}

export function BlueprintCatalogEditor({ kind, topics, blueprints, onChange, saveProps }: { kind: "exercise" | "review"; topics: CourseTopic[]; blueprints: Array<ExerciseBlueprint | ReviewBlueprint>; onChange: (items: Array<ExerciseBlueprint | ReviewBlueprint>) => void; saveProps?: SaveProps }) {
  return <BlueprintEditor kind={kind} topics={topics} blueprints={blueprints} onChange={(items) => onChange(items as Array<ExerciseBlueprint | ReviewBlueprint>)} saveProps={saveProps} />;
}

export function GuidedBlueprintCatalogEditor({ topics, blueprints = [], onChange, saveProps }: { topics: CourseTopic[]; blueprints?: GuidedBlueprint[]; onChange: (items: GuidedBlueprint[]) => void; saveProps?: SaveProps }) {
  return <BlueprintEditor kind="guided" topics={topics} blueprints={blueprints} onChange={(items) => onChange(items as GuidedBlueprint[])} saveProps={saveProps} />;
}
