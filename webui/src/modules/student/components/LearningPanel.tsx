import { BookMarked, CheckCircle2, Circle, Download, Lightbulb, ListChecks, Target, X } from "lucide-react";

import type { ChatMessage, LearningContext, SessionLearningMeta, TeacherCatalog } from "@/shared/types";

const LEVEL_LABEL = { beginner: "入门", intermediate: "进阶", advanced: "深入" } as const;
const MODE_LABEL = { explain: "讲解模式", socratic: "引导模式", practice: "练习模式", review: "复习模式" } as const;

interface KnowledgePointDisplay {
  id: string;
  name: string;
  covered: boolean;
}

function pointMatches(point: KnowledgePointDisplay, concepts: string[], messageText: string) {
  const name = point.name.toLowerCase();
  if (messageText.includes(name)) return true;
  return concepts.some((concept) => {
    const normalized = concept.toLowerCase();
    return normalized.includes(name) || name.includes(normalized);
  });
}

function buildTopicScope(topicName: string, pointNames: string[]) {
  const topicPart = topicName ? "当前主题是《" + topicName + "》" : "当前还没有选择学习主题";
  const pointPart = pointNames.length ? "，需要重点掌握：" + pointNames.join("、") : "，请先帮我梳理该主题的核心知识点";
  return topicPart + pointPart;
}

function practicePrompt(kind: "practice" | "check", topicName: string, pointNames: string[], concepts: string[], summary: string | undefined) {
  const scope = buildTopicScope(topicName, pointNames);
  const conceptPart = concepts.length ? "。最近已经涉及的概念包括：" + concepts.join("、") : "";
  const summaryPart = summary ? "。当前学习摘要：" + summary : "";
  if (kind === "practice") {
    return "请根据" + scope + "，出一道有明确考查目标的练习题。先不要直接给出答案，等我回答后再批改并讲解。" + conceptPart + summaryPart;
  }
  return "请围绕" + scope + "，用三个由浅入深的问题检查我是否真正理解了这些内容；一个问题一个问题地问，并根据我的回答决定是否继续推进。" + conceptPart + summaryPart;
}

function downloadReport(title: string, context: LearningContext, meta: SessionLearningMeta, messages: ChatMessage[], knowledgePoints: KnowledgePointDisplay[], reviewed: string[]) {
  const topic = (meta.topic ?? context.topic_name) || "未选择";
  const lines = [
    "# " + title,
    "",
    "> 导出时间：" + new Date().toLocaleString(),
    "",
    "## 学习设置",
    "- 学习主题：" + topic,
    "- 学习难度：" + LEVEL_LABEL[context.level],
    "- 教学模式：" + MODE_LABEL[context.mode],
    "",
    "## 学习目标",
    "理解并能够运用《" + topic + "》相关概念，通过讲解、示例与练习建立完整认识。",
    "",
    "## 知识掌握",
    knowledgePoints.length ? knowledgePoints.map((point) => "- " + (point.covered ? "[已涉及] " : "[未涉及] ") + point.name).join("\n") : "- 暂无知识地图",
    "",
    "## 待复习概念",
    reviewed.length ? reviewed.map((item) => "- " + item).join("\n") : "- 暂无",
    "",
    "## 学习摘要",
    meta.summary || "尚未生成学习摘要。",
    "",
    "## 对话记录（共 " + messages.length + " 条）",
    ...messages.map((message, index) => "\n### " + (index + 1) + " · " + (message.role === "user" ? "学生" : "NLP 教师") + "\n\n" + message.content),
  ];
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" }));
  link.download = (title || "NLP学习报告") + ".md";
  link.click();
  URL.revokeObjectURL(link.href);
}

export function LearningPanel({ open, onClose, title, context, meta, messages, catalog, onPrompt, onMeta }: {
  open: boolean;
  onClose: () => void;
  title: string;
  context: LearningContext;
  meta: SessionLearningMeta;
  messages: ChatMessage[];
  catalog?: TeacherCatalog | null;
  onPrompt: (content: string) => void;
  onMeta: (patch: Partial<SessionLearningMeta>) => void;
}) {
  const topic = (catalog?.topics ?? []).find((item) => item.id === context.topic_id);
  const knowledgePoints: KnowledgePointDisplay[] = (topic?.knowledge_points ?? [])
    .filter((point) => point.status !== "disabled")
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((point) => ({ id: point.id, name: point.name, covered: false }));
  const concepts = meta.concepts ?? [];
  const reviewed = meta.reviewConcepts ?? [];
  const messageText = messages.map((message) => message.content).join("\n").toLowerCase();
  for (const point of knowledgePoints) point.covered = pointMatches(point, concepts, messageText);
  const coveredCount = knowledgePoints.filter((point) => point.covered).length;
  const completedResponses = messages.filter((message) => message.role === "assistant" && message.status === "completed").length;
  const userQuestions = messages.filter((message) => message.role === "user").length;
  const progress = knowledgePoints.length
    ? Math.round((coveredCount / knowledgePoints.length) * 80 + (Math.min(completedResponses, 3) / 3) * 20)
    : Math.min(100, userQuestions * 14 + concepts.length * 6);
  const hasPracticeMaterial = messages.length > 0 && completedResponses > 0;
  const pointNames = knowledgePoints.map((point) => point.name);

  const toggleReview = (item: string) => {
    const current = reviewed.includes(item);
    onMeta({ reviewConcepts: current ? reviewed.filter((entry) => entry !== item) : [...reviewed, item] });
  };

  return (
    <aside className={"learning-panel " + (open ? "open" : "")}>
      <header><div><BookMarked size={17} /><strong>学习记录</strong><small>本次会话</small></div><button className="icon-button" type="button" onClick={onClose}><X size={17} /></button></header>

      <section>
        <h3><Target size={15} />学习目标</h3>
        <p>围绕《{(meta.topic ?? context.topic_name) || "当前问题"}》建立清晰的概念结构，能够讲清原理、辨别误区，并完成针对性练习。</p>
        {topic?.description && <p className="learning-topic-desc">{topic.description}</p>}
      </section>

      <section>
        <h3><ListChecks size={15} />知识掌握</h3>
        {knowledgePoints.length ? <>
          <div className="learning-scope-list">
            {knowledgePoints.map((point) => {
              const reviewing = reviewed.includes(point.name);
              const state = reviewing ? "review" : point.covered ? "covered" : "pending";
              return <button type="button" key={point.id} className={"learning-scope-item " + state} aria-label={(reviewing ? "移除待复习：" : "标记为待复习：") + point.name} onClick={() => toggleReview(point.name)}>
                <span className="learning-scope-icon">{reviewing ? <Circle size={13} /> : point.covered ? <CheckCircle2 size={13} /> : <Circle size={13} />}</span>
                <strong>{point.name}</strong>
                <small>{reviewing ? "待复习" : point.covered ? "已涉及" : "未涉及"}</small>
              </button>;
            })}
          </div>
          <p className="learning-coverage">已覆盖 {coveredCount}/{knowledgePoints.length} 个知识点 · 点击可标记待复习</p>
        </> : <p>选择学习主题后，这里会显示该主题的知识点地图。</p>}
      </section>

      <section>
        <h3><Lightbulb size={15} />本次涉及概念</h3>
        <div className="concept-list">
          {concepts.length ? concepts.map((concept) => {
            const reviewing = reviewed.includes(concept);
            return <button className={reviewing ? "reviewing" : ""} type="button" title="点击切换待复习状态" key={concept} onClick={() => toggleReview(concept)}>{concept}</button>;
          }) : <small>完成一次对话后自动整理</small>}
        </div>
        {!!reviewed.length && <p className="review-note">待复习：{reviewed.join("、")}</p>}
      </section>

      <section>
        <h3><Target size={15} />学习进度</h3>
        <div className="progress-track"><span style={{ width: progress + "%" }} /></div>
        <p>本次会话估算完成度 {progress}%</p>
        <div className="learning-stats"><span>{userQuestions} 次提问</span><span>{completedResponses} 次讲解</span><span>{meta.updatedAt ? "活跃于 " + new Date(meta.updatedAt).toLocaleTimeString() : "刚刚开始"}</span></div>
      </section>

      <section>
        <h3><BookMarked size={15} />对话摘要</h3>
        <p>{meta.summary || "教学 Agent 会在每次回答后自动整理本次学习摘要。"}</p>
      </section>

      <section className="practice-card">
        <h3><Lightbulb size={15} />巩固练习</h3>
        <p>这两个动作会把当前主题、已涉及概念和摘要一起发给教学 Agent，由它继续推进练习或检查理解。</p>
        <div className="practice-actions">
          <button type="button" disabled={!hasPracticeMaterial} onClick={() => onPrompt(practicePrompt("practice", (meta.topic ?? context.topic_name) || "", pointNames, concepts, meta.summary))}>生成练习</button>
          <button type="button" disabled={!hasPracticeMaterial} onClick={() => onPrompt(practicePrompt("check", (meta.topic ?? context.topic_name) || "", pointNames, concepts, meta.summary))}>检查理解</button>
        </div>
        {!hasPracticeMaterial && <p className="practice-hint">至少完成一次教师讲解后，才能基于当前内容生成练习或检查理解。</p>}
      </section>

      <button className="export-button" type="button" disabled={!messages.length} onClick={() => downloadReport(title, context, meta, messages, knowledgePoints, reviewed)}><Download size={15} />导出 Markdown 学习报告</button>
    </aside>
  );
}
