import { BookOpen, ChevronLeft, ChevronRight, FilePlus2, MessageCircleQuestion, Plus, Power, Save, Trash2 } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "@/shared/ui/ConfirmDialog";
import type { CourseTopic, ExerciseBlueprint, GuidedBlueprint, KnowledgePoint, ReviewBlueprint, RubricPoint } from "@/shared/types";
import { createUuid } from "@/shared/utils/uuid";

const makeId = (kind: string) => `${kind}_${createUuid().replaceAll("-", "").slice(0, 12)}`;
const statusText = (status: "draft" | "enabled" | "disabled") => status === "enabled" ? "已启用" : status === "disabled" ? "已停用" : "草稿";

function RemoveButton({ label, onRemove }: { label: string; onRemove: () => void }) {
  const [open, setOpen] = useState(false);
  return <><button className="teacher-danger-button" type="button" onClick={() => setOpen(true)}><Trash2 size={15} />删除</button><ConfirmDialog open={open} title={`删除${label}？`} description="该项将从当前教学目录移除；既有学习记录和蓝图快照不会受影响。" onClose={() => setOpen(false)} onConfirm={() => { onRemove(); setOpen(false); }} /></>;
}

function StatusButton({ status, onChange }: { status: "draft" | "enabled" | "disabled"; onChange: (status: "enabled" | "disabled") => void }) {
  return <button className={status === "enabled" ? "teacher-status-button enabled" : "teacher-status-button"} type="button" onClick={() => onChange(status === "enabled" ? "disabled" : "enabled")}><Power size={14} />{status === "enabled" ? "停用" : "启用"}</button>;
}

function StatusBadge({ status }: { status: "draft" | "enabled" | "disabled" }) { return <span className={`teacher-status-badge ${status}`}>{statusText(status)}</span>; }

const CATALOG_PAGE_SIZE = 12;

function CardPager({ page, pageCount, onPageChange }: { page: number; pageCount: number; onPageChange: (page: number) => void }) {
  return <div className="teacher-card-pager"><button type="button" disabled={page <= 1} onClick={() => onPageChange(page - 1)}><ChevronLeft size={14} />上一页</button><span>第{page}页 / 共{pageCount}页</span><button type="button" disabled={page >= pageCount} onClick={() => onPageChange(page + 1)}>下一页<ChevronRight size={14} /></button></div>;
}

function TopicTile({ topic, selected, onSelect, onOpen }: { topic: CourseTopic; selected: boolean; onSelect: () => void; onOpen: () => void }) {
  return <button type="button" className={selected ? "teacher-tile selected" : "teacher-tile"} aria-pressed={selected} onClick={onSelect} onDoubleClick={onOpen}><span className="teacher-tile-icon"><BookOpen size={17} /></span><strong className="teacher-tile-name">{topic.name || "未命名主题"}</strong><span className="teacher-tile-meta">{topic.knowledge_points.length} 个知识点</span><StatusBadge status={topic.status} /></button>;
}

function PointTile({ point, onEdit }: { point: KnowledgePoint; onEdit: () => void }) {
  return <button type="button" className="teacher-tile" onClick={onEdit}><span className="teacher-tile-icon"><BookOpen size={17} /></span><strong className="teacher-tile-name">{point.name || "未命名知识点"}</strong><StatusBadge status={point.status} /></button>;
}

function TopicDetailView({ topic, onBack, onChange, onRemove, onAddPoint, onEditPoint }: { topic: CourseTopic; onBack: () => void; onChange: (topic: CourseTopic) => void; onRemove: () => void; onAddPoint: () => void; onEditPoint: (pointId: string) => void }) {
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(topic.knowledge_points.length / CATALOG_PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const visiblePoints = topic.knowledge_points.slice((safePage - 1) * CATALOG_PAGE_SIZE, safePage * CATALOG_PAGE_SIZE);
  return <section className="teacher-panel">
    <header className="teacher-detail-bar"><button className="teacher-secondary-button" type="button" onClick={onBack}><ChevronLeft size={15} />返回主题目录</button><div className="teacher-detail-title"><BookOpen size={17} /><h2>{topic.name || "未命名主题"}</h2><StatusBadge status={topic.status} /></div><div className="teacher-detail-actions"><StatusButton status={topic.status} onChange={(status) => onChange({ ...topic, status })} /><RemoveButton label={`主题“${topic.name || "未命名"}”`} onRemove={onRemove} /><button className="teacher-primary-button" type="button" onClick={onAddPoint}><Plus size={16} />新建知识点</button></div></header>
    <div className="teacher-catalog-body">
      <div className="teacher-editor-grid teacher-inset-grid">
        <div className="teacher-editor-two-column"><label>主题名称<input value={topic.name} onChange={(event) => onChange({ ...topic, name: event.target.value })} /></label><label>主题说明<textarea rows={2} value={topic.description} onChange={(event) => onChange({ ...topic, description: event.target.value })} /></label></div>
      </div>
      {topic.knowledge_points.length ? <><div className="teacher-tile-grid">{visiblePoints.map((point) => <PointTile key={point.id} point={point} onEdit={() => onEditPoint(point.id)} />)}</div><CardPager page={safePage} pageCount={pageCount} onPageChange={setPage} /></> : <Empty text="暂无知识点；点击右上角“新建知识点”开始编写 Markdown 教学边界。" compact />}
    </div>
  </section>;
}

function PointEditorView({ point, onBack, onSave, onRemove }: { point: KnowledgePoint; onBack: () => void; onSave: (point: KnowledgePoint) => void; onRemove: () => void }) {
  const [draft, setDraft] = useState(point); const [saved, setSaved] = useState(false);
  const edit = (patch: Partial<KnowledgePoint>) => { setDraft({ ...draft, ...patch }); setSaved(false); };
  return <section className="teacher-panel">
    <header className="teacher-detail-bar"><button className="teacher-secondary-button" type="button" onClick={onBack}><ChevronLeft size={15} />返回知识点列表</button><div className="teacher-detail-title"><BookOpen size={16} /><h2>{draft.name || "未命名知识点"}</h2><StatusBadge status={draft.status} /></div><div className="teacher-detail-actions"><StatusButton status={draft.status} onChange={(status) => edit({ status })} /><RemoveButton label={`知识点“${draft.name || "未命名"}”`} onRemove={onRemove} /><button className="teacher-primary-button" type="button" onClick={() => { onSave(draft); setSaved(true); }}><Save size={15} />保存知识点</button></div></header>
    <div className="teacher-editor-grid"><label>知识点名称<input value={draft.name} onChange={(event) => edit({ name: event.target.value })} placeholder="例如：缩放点积注意力" /></label><label>知识点 Markdown<textarea rows={12} value={draft.markdown} onChange={(event) => edit({ markdown: event.target.value })} placeholder="用 Markdown 写清概念边界、必须覆盖的内容与容易混淆处。" /></label><p className="teacher-field-hint">启用的知识点会成为该主题的回答范围约束；可使用公式、表格、代码和 Mermaid。</p>{saved && <p className="teacher-field-hint" role="status">已保存到当前目录；点击下方“保存教学目录”同步到后端。</p>}</div>
  </section>;
}

export function TopicCatalogEditor({ topics, onChange }: { topics: CourseTopic[]; onChange: (topics: CourseTopic[]) => void }) {
  const [name, setName] = useState(""); const [description, setDescription] = useState("");
  const [view, setView] = useState<CatalogView>({ kind: "list" }); const [selectedTopicId, setSelectedTopicId] = useState(""); const [listPage, setListPage] = useState(1);
  const update = (topic: CourseTopic) => onChange(topics.map((item) => item.id === topic.id ? topic : item));
  const create = () => { if (!name.trim()) return; const topic = { id: makeId("topic"), name: name.trim(), description: description.trim(), status: "enabled" as const, knowledge_points: [] }; onChange([...topics, topic]); setSelectedTopicId(topic.id); setListPage(Math.floor(topics.length / CATALOG_PAGE_SIZE) + 1); setName(""); setDescription(""); };
  const addPoint = (topic: CourseTopic) => { const point = { id: makeId("kp"), name: "新知识点", markdown: "", status: "enabled" as const, sort_order: topic.knowledge_points.length }; update({ ...topic, knowledge_points: [...topic.knowledge_points, point] }); setView({ kind: "point", topicId: topic.id, pointId: point.id }); };
  const updatePoint = (topicId: string, point: KnowledgePoint) => { const topic = topics.find((item) => item.id === topicId); if (!topic) return; update({ ...topic, knowledge_points: topic.knowledge_points.map((item) => item.id === point.id ? point : item) }); };
  const removePoint = (topicId: string, pointId: string) => { const topic = topics.find((item) => item.id === topicId); if (!topic) return; update({ ...topic, knowledge_points: topic.knowledge_points.filter((item) => item.id !== pointId) }); setView({ kind: "topic", topicId }); };
  const removeTopic = (topicId: string) => { onChange(topics.filter((item) => item.id !== topicId)); setView({ kind: "list" }); };
  const topicPageCount = Math.max(1, Math.ceil(topics.length / CATALOG_PAGE_SIZE));
  const safeListPage = Math.min(listPage, topicPageCount);
  const visibleTopics = topics.slice((safeListPage - 1) * CATALOG_PAGE_SIZE, safeListPage * CATALOG_PAGE_SIZE);
  const detailTopic = view.kind === "topic" ? topics.find((item) => item.id === view.topicId) : undefined;
  const pointView = view.kind === "point" ? view : null;
  const editPoint = pointView ? topics.find((item) => item.id === pointView.topicId)?.knowledge_points.find((item) => item.id === pointView.pointId) : undefined;
  return <div className="teacher-stack">
    {!detailTopic && !pointView && <>
      <section className="teacher-panel teacher-create-card"><header><div><span className="teacher-eyebrow">课程导航</span><h2>{topics.length ? "新建主题" : "创建第一个主题"}</h2><p>主题、知识点和蓝图均由教师手动创建并保存；不会导入预置课程数据。</p></div></header><div className="teacher-editor-grid"><label>主题名称<input aria-label="主题名称" value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：Transformer" /></label><label>主题说明<textarea aria-label="主题说明" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明该主题的学习范围" /></label><button className="teacher-primary-button" type="button" disabled={!name.trim()} onClick={create}><Plus size={16} />创建主题</button></div></section>
      <section className="teacher-panel"><header><div><h2>主题目录</h2><p>按每页 3 行 × 4 列分页，共 12 个主题；单击选中主题，双击进入知识点管理。</p></div></header>{topics.length ? <div className="teacher-catalog-body"><div className="teacher-tile-grid">{visibleTopics.map((topic) => <TopicTile key={topic.id} topic={topic} selected={selectedTopicId === topic.id} onSelect={() => setSelectedTopicId(topic.id)} onOpen={() => { setSelectedTopicId(topic.id); setView({ kind: "topic", topicId: topic.id }); }} />)}</div><CardPager page={safeListPage} pageCount={topicPageCount} onPageChange={setListPage} /></div> : <Empty text="还没有主题。请先创建一个课程主题，再添加知识点。" />}</section>
    </>}
    {detailTopic && view.kind === "topic" && <TopicDetailView topic={detailTopic} onBack={() => setView({ kind: "list" })} onChange={update} onRemove={() => removeTopic(detailTopic.id)} onAddPoint={() => addPoint(detailTopic)} onEditPoint={(pointId) => setView({ kind: "point", topicId: detailTopic.id, pointId })} />}
    {pointView && editPoint && <PointEditorView key={editPoint.id} point={editPoint} onBack={() => setView({ kind: "topic", topicId: pointView.topicId })} onSave={(point) => updatePoint(pointView.topicId, point)} onRemove={() => removePoint(pointView.topicId, editPoint.id)} />}
  </div>;
}

type CatalogView = { kind: "list" } | { kind: "topic"; topicId: string } | { kind: "point"; topicId: string; pointId: string };

type Blueprint = ExerciseBlueprint | ReviewBlueprint;
type BlueprintKind = "exercise" | "review";
function blueprintName(kind: BlueprintKind) { return kind === "exercise" ? "出题蓝图" : "复习蓝图"; }
function catalogTileMeta(topicId: string, pointId: string, topics: CourseTopic[]) { const topic = topics.find((value) => value.id === topicId); const point = topic?.knowledge_points.find((value) => value.id === pointId); return [topic?.name ?? "", point?.name ?? ""].filter(Boolean).join(" · ") || "未关联知识点"; }

function BlueprintTile({ item, topics, onOpen }: { item: Blueprint; topics: CourseTopic[]; onOpen: () => void }) {
  return <button type="button" className="teacher-tile" onClick={onOpen}><span className="teacher-tile-icon"><FilePlus2 size={17} /></span><strong className="teacher-tile-name">{item.name || "未命名蓝图"}</strong><span className="teacher-tile-meta">{catalogTileMeta(item.topic_id, item.knowledge_point_id, topics)}</span><StatusBadge status={item.status} /></button>;
}

function BlueprintEditorView({ kind, item, topics, exerciseBlueprints, onBack, onSave, onRemove }: { kind: BlueprintKind; item: Blueprint; topics: CourseTopic[]; exerciseBlueprints: ExerciseBlueprint[]; onBack: () => void; onSave: (item: Blueprint) => void; onRemove: () => void }) {
  const [draft, setDraft] = useState(item); const [saved, setSaved] = useState(false);
  const edit = (patch: Partial<Blueprint>) => { setDraft({ ...draft, ...patch } as Blueprint); setSaved(false); };
  const allowedPoints = topics.find((value) => value.id === draft.topic_id)?.knowledge_points ?? [];
  return <section className="teacher-panel">
    <header className="teacher-detail-bar"><button className="teacher-secondary-button" type="button" onClick={onBack}><ChevronLeft size={15} />返回{blueprintName(kind)}目录</button><div className="teacher-detail-title"><FilePlus2 size={16} /><h2>{draft.name || "未命名蓝图"}</h2></div><StatusBadge status={draft.status} /></header>
    <div className="teacher-editor-grid">
      <div className="teacher-editor-two-column"><label>{blueprintName(kind)}名称<input value={draft.name} onChange={(event) => edit({ name: event.target.value })} /></label><label>所属主题<select value={draft.topic_id} onChange={(event) => edit({ topic_id: event.target.value, knowledge_point_id: "" })}>{topics.map((value) => <option value={value.id} key={value.id}>{value.name}</option>)}</select></label></div>
      <div className="teacher-editor-two-column"><label>关联知识点<select value={draft.knowledge_point_id} onChange={(event) => edit({ knowledge_point_id: event.target.value })}>{allowedPoints.map((point) => <option value={point.id} key={point.id}>{point.name}</option>)}</select></label><label>题型<input value={draft.question_type} onChange={(event) => edit({ question_type: event.target.value })} /></label></div>
      {kind === "review" && <label>关联练习蓝图<select value={(draft as ReviewBlueprint).exercise_blueprint_id ?? ""} onChange={(event) => edit({ exercise_blueprint_id: event.target.value || null })}><option value="">不关联，使用自身指令</option>{exerciseBlueprints.filter((blueprint) => blueprint.topic_id === draft.topic_id && blueprint.knowledge_point_id === draft.knowledge_point_id).map((blueprint) => <option value={blueprint.id} key={blueprint.id}>{blueprint.name}</option>)}</select></label>}
      <label>单题 Markdown 指令<textarea rows={7} value={draft.instructions} onChange={(event) => edit({ instructions: event.target.value })} /></label>
      <RubricEditor rubric={draft.rubric} onChange={(rubric) => edit({ rubric })} />
      <div className="teacher-editor-actions"><StatusButton status={draft.status} onChange={(status) => edit({ status })} /><RemoveButton label={`${blueprintName(kind)}“${draft.name || "未命名"}”`} onRemove={onRemove} /><button className="teacher-primary-button" type="button" onClick={() => { onSave(draft); setSaved(true); }}><Save size={15} />保存蓝图</button>{saved && <span role="status">已保存到当前目录；点击下方“保存教学目录”同步到后端。</span>}</div>
    </div>
  </section>;
}

export function BlueprintCatalogEditor({ kind, topics, blueprints, exerciseBlueprints, onChange }: { kind: BlueprintKind; topics: CourseTopic[]; blueprints: Blueprint[]; exerciseBlueprints: ExerciseBlueprint[]; onChange: (items: Blueprint[]) => void }) {
  const [name, setName] = useState(""); const [topicId, setTopicId] = useState(""); const [pointId, setPointId] = useState(""); const [instructions, setInstructions] = useState(""); const [questionType, setQuestionType] = useState("简答");
  const [editingId, setEditingId] = useState(""); const [listPage, setListPage] = useState(1);
  const topicOptions = topics.filter((topic) => topic.status !== "disabled"); const selectedTopic = topicOptions.find((topic) => topic.id === topicId); const points = selectedTopic?.knowledge_points.filter((point) => point.status === "enabled") ?? [];
  const editing = blueprints.find((item) => item.id === editingId);
  const pageCount = Math.max(1, Math.ceil(blueprints.length / CATALOG_PAGE_SIZE)); const safeListPage = Math.min(listPage, pageCount);
  const visibleBlueprints = blueprints.slice((safeListPage - 1) * CATALOG_PAGE_SIZE, safeListPage * CATALOG_PAGE_SIZE);
  const create = () => { if (!name.trim() || !topicId || !pointId || !instructions.trim() || !questionType.trim()) return; const base = { id: makeId(kind), name: name.trim(), topic_id: topicId, knowledge_point_id: pointId, instructions: instructions.trim(), question_type: questionType.trim(), rubric: [], status: "draft" as const }; const item: Blueprint = kind === "exercise" ? base : { ...base, exercise_blueprint_id: null }; onChange([...blueprints, item]); setName(""); setInstructions(""); setQuestionType("简答"); setListPage(Math.floor(blueprints.length / CATALOG_PAGE_SIZE) + 1); };
  return <div className="teacher-stack">
    {!editing && <>
      <section className="teacher-panel teacher-create-card"><header><div><span className="teacher-eyebrow">单题蓝图</span><h2>创建{blueprintName(kind)}</h2><p>每张蓝图只生成一道题，必须绑定主题中的一个知识点；学生开始练习时后端会随机抽取一张有效蓝图。</p></div></header>{topicOptions.length ? <div className="teacher-editor-grid"><div className="teacher-editor-two-column"><label>{blueprintName(kind)}名称<input aria-label={`${kind}蓝图名称`} value={name} onChange={(event) => setName(event.target.value)} /></label><label>所属主题<select aria-label={`${kind}所属主题`} value={topicId} onChange={(event) => { setTopicId(event.target.value); setPointId(""); }}><option value="">选择主题</option>{topicOptions.map((topic) => <option value={topic.id} key={topic.id}>{topic.name}</option>)}</select></label></div><div className="teacher-editor-two-column"><label>关联知识点<select aria-label={`${kind}关联知识点`} value={pointId} disabled={!topicId} onChange={(event) => setPointId(event.target.value)}><option value="">选择一个知识点</option>{points.map((point) => <option value={point.id} key={point.id}>{point.name}</option>)}</select></label><label>题型<input aria-label={`${kind}题型`} value={questionType} onChange={(event) => setQuestionType(event.target.value)} placeholder="例如：简答" /></label></div><label>单题 Markdown 指令<textarea aria-label={`${kind}Markdown 指令`} rows={6} value={instructions} onChange={(event) => setInstructions(event.target.value)} placeholder="只描述这一道题的题干范围、数据要求与讲评规则。" /></label><button className="teacher-primary-button" type="button" disabled={!name.trim() || !topicId || !pointId || !instructions.trim() || !questionType.trim()} onClick={create}><FilePlus2 size={16} />创建单题草稿蓝图</button></div> : <Empty text="请先创建并启用一个主题和知识点，才能创建蓝图。" />}</section>
      <section className="teacher-panel"><header><div><h2>{blueprintName(kind)}目录</h2><p>按每页 3 行 × 4 列分页，共 12 张蓝图；单击蓝图进入详情编辑。</p></div></header>{blueprints.length ? <div className="teacher-catalog-body"><div className="teacher-tile-grid">{visibleBlueprints.map((item) => <BlueprintTile key={item.id} item={item} topics={topics} onOpen={() => setEditingId(item.id)} />)}</div><CardPager page={safeListPage} pageCount={pageCount} onPageChange={setListPage} /></div> : <Empty text={`还没有${blueprintName(kind)}。请在上方绑定主题和知识点，创建第一张草稿蓝图。`} />}</section>
    </>}
    {editing && <BlueprintEditorView key={editing.id} kind={kind} item={editing} topics={topics} exerciseBlueprints={exerciseBlueprints} onBack={() => setEditingId("")} onSave={(next) => onChange(blueprints.map((item) => item.id === next.id ? next : item))} onRemove={() => { onChange(blueprints.filter((item) => item.id !== editing.id)); setEditingId(""); }} />}
  </div>;
}

function RubricEditor({ rubric, onChange }: { rubric: RubricPoint[]; onChange: (rubric: RubricPoint[]) => void }) { return <fieldset className="teacher-rubric"><legend>评分标准</legend>{rubric.map((item, index) => <div key={item.id ?? index}><input aria-label={`评分标准 ${index + 1}`} value={item.criterion} placeholder="例如：正确解释注意力权重" onChange={(event) => onChange(rubric.map((value, i) => i === index ? { ...value, criterion: event.target.value } : value))} /><input aria-label={`评分权重 ${index + 1}`} type="number" min="0" max="100" value={item.weight} onChange={(event) => onChange(rubric.map((value, i) => i === index ? { ...value, weight: Number(event.target.value) } : value))} /><button type="button" aria-label={`删除评分标准 ${index + 1}`} onClick={() => onChange(rubric.filter((_, i) => i !== index))}><Trash2 size={14} /></button></div>)}<button className="teacher-secondary-button" type="button" onClick={() => onChange([...rubric, { criterion: "", weight: 0 }])}><Plus size={14} />添加评分标准</button></fieldset>; }

function Empty({ text, compact = false }: { text: string; compact?: boolean }) { return <p className={compact ? "teacher-empty-inline" : "teacher-empty-state"}>{text}</p>; }

function GuidedTile({ item, topics, onOpen }: { item: GuidedBlueprint; topics: CourseTopic[]; onOpen: () => void }) {
  return <button type="button" className="teacher-tile" onClick={onOpen}><span className="teacher-tile-icon"><MessageCircleQuestion size={17} /></span><strong className="teacher-tile-name">{item.name || "未命名蓝图"}</strong><span className="teacher-tile-meta">{catalogTileMeta(item.topic_id, item.knowledge_point_id, topics)}</span><StatusBadge status={item.status} /></button>;
}

function GuidedBlueprintEditorView({ item, topics, onBack, onSave, onRemove }: { item: GuidedBlueprint; topics: CourseTopic[]; onBack: () => void; onSave: (item: GuidedBlueprint) => void; onRemove: () => void }) {
  const [draft, setDraft] = useState(item); const [saved, setSaved] = useState(false);
  const edit = (patch: Partial<GuidedBlueprint>) => { setDraft({ ...draft, ...patch }); setSaved(false); };
  const allowedPoints = topics.find((value) => value.id === draft.topic_id)?.knowledge_points ?? [];
  return <section className="teacher-panel">
    <header className="teacher-detail-bar"><button className="teacher-secondary-button" type="button" onClick={onBack}><ChevronLeft size={15} />返回引导蓝图目录</button><div className="teacher-detail-title"><MessageCircleQuestion size={16} /><h2>{draft.name || "未命名蓝图"}</h2></div><StatusBadge status={draft.status} /></header>
    <div className="teacher-editor-grid">
      <div className="teacher-editor-two-column"><label>引导蓝图名称<input value={draft.name} onChange={(event) => edit({ name: event.target.value })} /></label><label>所属主题<select value={draft.topic_id} onChange={(event) => edit({ topic_id: event.target.value, knowledge_point_id: "" })}>{topics.map((value) => <option value={value.id} key={value.id}>{value.name}</option>)}</select></label></div>
      <label>关联知识点<select value={draft.knowledge_point_id} onChange={(event) => edit({ knowledge_point_id: event.target.value })}>{allowedPoints.map((point) => <option value={point.id} key={point.id}>{point.name}</option>)}</select></label>
      <label>引导方向（Markdown 指令）<textarea rows={7} value={draft.guidance} onChange={(event) => edit({ guidance: event.target.value })} /></label>
      <div className="teacher-editor-actions"><StatusButton status={draft.status} onChange={(status) => edit({ status })} /><RemoveButton label={`引导蓝图“${draft.name || "未命名"}”`} onRemove={onRemove} /><button className="teacher-primary-button" type="button" onClick={() => { onSave(draft); setSaved(true); }}><Save size={15} />保存蓝图</button>{saved && <span role="status">已保存到当前目录；点击下方“保存教学目录”同步到后端。</span>}</div>
    </div>
  </section>;
}

export function GuidedBlueprintCatalogEditor({ topics, blueprints = [], onChange }: { topics: CourseTopic[]; blueprints?: GuidedBlueprint[]; onChange: (items: GuidedBlueprint[]) => void }) {
  const [name, setName] = useState(""); const [topicId, setTopicId] = useState(""); const [pointId, setPointId] = useState(""); const [guidance, setGuidance] = useState("");
  const [editingId, setEditingId] = useState(""); const [listPage, setListPage] = useState(1);
  const topicOptions = topics.filter((topic) => topic.status !== "disabled"); const selectedTopic = topicOptions.find((topic) => topic.id === topicId); const points = selectedTopic?.knowledge_points.filter((point) => point.status === "enabled") ?? [];
  const editing = blueprints.find((item) => item.id === editingId);
  const pageCount = Math.max(1, Math.ceil(blueprints.length / CATALOG_PAGE_SIZE)); const safeListPage = Math.min(listPage, pageCount);
  const visibleBlueprints = blueprints.slice((safeListPage - 1) * CATALOG_PAGE_SIZE, safeListPage * CATALOG_PAGE_SIZE);
  const create = () => { if (!name.trim() || !topicId || !pointId || !guidance.trim()) return; onChange([...blueprints, { id: makeId("guided"), name: name.trim(), topic_id: topicId, knowledge_point_id: pointId, guidance: guidance.trim(), status: "draft" }]); setName(""); setGuidance(""); setListPage(Math.floor(blueprints.length / CATALOG_PAGE_SIZE) + 1); };
  return <div className="teacher-stack">
    {!editing && <>
      <section className="teacher-panel teacher-create-card"><header><div><span className="teacher-eyebrow">引导方向</span><h2>创建引导蓝图</h2><p>每张蓝图为一个知识点定义教师引导方向；学生进入引导模式时后端会随机抽取一张启用蓝图。</p></div></header>{topicOptions.length ? <div className="teacher-editor-grid"><div className="teacher-editor-two-column"><label>引导蓝图名称<input aria-label="guided蓝图名称" value={name} onChange={(event) => setName(event.target.value)} /></label><label>所属主题<select aria-label="guided所属主题" value={topicId} onChange={(event) => { setTopicId(event.target.value); setPointId(""); }}><option value="">选择主题</option>{topicOptions.map((topic) => <option value={topic.id} key={topic.id}>{topic.name}</option>)}</select></label></div><label>关联知识点<select aria-label="guided关联知识点" value={pointId} disabled={!topicId} onChange={(event) => setPointId(event.target.value)}><option value="">选择一个知识点</option>{points.map((point) => <option value={point.id} key={point.id}>{point.name}</option>)}</select></label><label>引导方向（Markdown 指令）<textarea aria-label="guidedMarkdown 指令" rows={6} value={guidance} onChange={(event) => setGuidance(event.target.value)} placeholder="说明教师希望模型采用的引导路径、提问顺序和应聚焦的误区。" /></label><button className="teacher-primary-button" type="button" disabled={!name.trim() || !topicId || !pointId || !guidance.trim()} onClick={create}><FilePlus2 size={16} />创建引导草稿蓝图</button></div> : <Empty text="请先创建并启用一个主题和知识点，才能创建蓝图。" />}</section>
      <section className="teacher-panel"><header><div><h2>引导蓝图目录</h2><p>按每页 3 行 × 4 列分页，共 12 张蓝图；单击蓝图进入详情编辑。</p></div></header>{blueprints.length ? <div className="teacher-catalog-body"><div className="teacher-tile-grid">{visibleBlueprints.map((item) => <GuidedTile key={item.id} item={item} topics={topics} onOpen={() => setEditingId(item.id)} />)}</div><CardPager page={safeListPage} pageCount={pageCount} onPageChange={setListPage} /></div> : <Empty text="还没有引导蓝图。请在上方绑定主题和知识点，创建第一条引导方向。" />}</section>
    </>}
    {editing && <GuidedBlueprintEditorView key={editing.id} item={editing} topics={topics} onBack={() => setEditingId("")} onSave={(next) => onChange(blueprints.map((item) => item.id === next.id ? next : item))} onRemove={() => { onChange(blueprints.filter((item) => item.id !== editing.id)); setEditingId(""); }} />}
  </div>;
}
