import { BadgeInfo, BookOpenCheck, ChevronDown, ChevronRight, CircleHelp, Clock3, Database, Gauge, Globe2, MessageSquarePlus, MonitorCog, Moon, Settings2, Sun, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { api } from "@/platform/http/api";
import type { FeedbackCategory, FeedbackThread, LearningContext, ReleaseNoteEntry, UserSettings } from "@/shared/types";
import { ConfirmDialog } from "@/shared/ui/ConfirmDialog";
import { supportedLocales } from "@/shared/i18n/config";
import { saveFeedback } from "@/shared/utils/feedback";
import { APP_NAME, APP_VERSION } from "@/shared/version";

type SettingsSection = "general" | "appearance" | "chat" | "learning" | "data" |  "feedback" | "updates";

const sections: Array<{ id: SettingsSection; label: string; icon: typeof Settings2 }> = [
  { id: "general", label: "通用", icon: Settings2 },
  { id: "appearance", label: "外观", icon: Sun },
  { id: "chat", label: "对话与流式", icon: Gauge },
  { id: "learning", label: "学习体验", icon: BookOpenCheck },
  { id: "data", label: "数据与隐私", icon: Database },
  { id: "feedback", label: "意见反馈", icon: MessageSquarePlus },
  { id: "updates", label: "版本与更新", icon: BadgeInfo },
];

const levelLabel: Record<LearningContext["level"], string> = { beginner: "入门", intermediate: "进阶", advanced: "高阶" };
const modeLabel: Record<LearningContext["mode"], string> = { explain: "讲解", socratic: "苏格拉底追问", practice: "练习", review: "复习" };
const FEEDBACK_DISABLED_HINT = "当前身份不支持提交反馈";
const feedbackStatusLabel: Record<string, string> = { open: "待处理", under_review: "审视中", planned: "已规划", in_progress: "进行中", complete: "已完成", closed: "已关闭" };
const feedbackCategoryLabel: Record<string, string> = { feature: "功能建议", ux: "体验问题", bug: "Bug", other: "其他" };

export function SettingsDialog({ open, settings, learningContext, roles = [], permissions, userId, onClose, onChange, onReset, onLearningContextChange, onOpenDeveloper, onOpenTeacher }: {
  open: boolean;
  settings: UserSettings;
  learningContext: LearningContext;
  roles?: string[];
  permissions?: string[];
  userId?: string;
  onClose: () => void;
  onChange: (patch: Partial<UserSettings>) => void;
  onReset: () => void;
  onLearningContextChange: (context: LearningContext) => void;
  onOpenDeveloper: () => void;
  onOpenTeacher: () => void;
}) {
  const [section, setSection] = useState<SettingsSection>("general");
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [feedbackCategory, setFeedbackCategory] = useState<FeedbackCategory>("other");
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);
  const [feedbackError, setFeedbackError] = useState("");
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [feedbackDaily, setFeedbackDaily] = useState<{ used: number; remaining: number; limit: number } | null>(null);
  const [feedbackHistory, setFeedbackHistory] = useState<FeedbackThread | null>(null);
  const [feedbackHistoryError, setFeedbackHistoryError] = useState("");
  const [feedbackHistoryLoadingMore, setFeedbackHistoryLoadingMore] = useState(false);
  const [feedbackHistoryOpen, setFeedbackHistoryOpen] = useState(false);
  const [releaseNotes, setReleaseNotes] = useState<ReleaseNoteEntry[] | null>(null);
  const [releaseNotesError, setReleaseNotesError] = useState(false);
  const [releaseNotesAttempt, setReleaseNotesAttempt] = useState(0);
  const canTeach = roles.includes("teacher") || roles.includes("developer");
  const canDevelop = roles.includes("developer");
  // Server-side permissions win when present (custom RBAC roles); legacy guest
  // sessions only carry roles, so fall back to the built-in role packages.
  const canSubmitFeedback = permissions
    ? permissions.includes("learning:feedback:submit")
    : ["student", "teacher", "developer"].some((role) => roles.includes(role));
  useEffect(() => {
    if (!open || section !== "updates" || releaseNotes !== null) return;
    api.listPublishedReleaseNotes()
      .then(({ items }) => { setReleaseNotesError(false); setReleaseNotes(items); })
      .catch(() => setReleaseNotesError(true));
  }, [open, section, releaseNotes, releaseNotesAttempt]);
  useEffect(() => {
    if (!open || section !== "feedback" || !canSubmitFeedback) return;
    void api.getFeedbackDailyState().then(setFeedbackDaily).catch(() => setFeedbackDaily(null));
  }, [open, section, canSubmitFeedback]);
  useEffect(() => {
    if (!open || !canSubmitFeedback) return;
    void api.getOwnFeedback().then((thread) => {
      setFeedbackHistory(thread.thread_id ? thread as FeedbackThread : null);
      setFeedbackHistoryError("");
    }).catch((error) => setFeedbackHistoryError(error instanceof Error ? error.message : String(error)));
  }, [open, canSubmitFeedback]);
  useEffect(() => {
    if (!open) queueMicrotask(() => setFeedbackHistoryOpen(false));
  }, [open]);
  const loadOlderFeedbackHistory = async () => {
    if (!feedbackHistory?.thread_id || !feedbackHistory.message_has_more || feedbackHistoryLoadingMore) return;
    setFeedbackHistoryLoadingMore(true);
    setFeedbackHistoryError("");
    try {
      const page = await api.getOwnFeedback({ limit: feedbackHistory.message_limit ?? 50, offset: feedbackHistory.messages.length });
      setFeedbackHistory((current) => current && current.thread_id === page.thread_id ? {
        ...current,
        messages: [...page.messages, ...current.messages],
        message_total: page.message_total,
        message_offset: 0,
        message_limit: page.message_limit,
        message_has_more: page.message_has_more,
      } : page);
    } catch (error) {
      setFeedbackHistoryError(error instanceof Error ? error.message : String(error));
    } finally {
      setFeedbackHistoryLoadingMore(false);
    }
  };
  const toggleFeedbackHistory = async () => {
    const nextOpen = !feedbackHistoryOpen;
    setFeedbackHistoryOpen(nextOpen);
    if (!nextOpen || !feedbackHistory || (feedbackHistory.student_unread_count ?? 0) === 0) return;
    try {
      await api.markOwnFeedbackRead();
      setFeedbackHistory((current) => current ? { ...current, student_unread_count: 0 } : current);
    } catch (error) {
      setFeedbackHistoryError(error instanceof Error ? error.message : String(error));
    }
  };
  if (!open) return null;
  const updateLearning = (patch: Partial<LearningContext>) => onLearningContextChange({ ...learningContext, ...patch });

  return <>
    <div className="dialog-backdrop settings-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="settings-dialog" role="dialog" aria-modal="true" aria-label="偏好设置" onMouseDown={(event) => event.stopPropagation()}>
        <aside className="settings-nav">
          <div className="settings-nav-brand"><Settings2 size={19} /><span><strong>偏好设置</strong><small>学习空间</small></span></div>
          <nav>{sections.map(({ id, label, icon: Icon }) => <button key={id} type="button" className={section === id ? "active" : ""} disabled={id === "feedback" && !canSubmitFeedback} title={id === "feedback" && !canSubmitFeedback ? FEEDBACK_DISABLED_HINT : undefined} onClick={() => setSection(id)}><Icon size={16} /><span className="settings-nav-label">{label}</span>{id === "feedback" && (feedbackHistory?.student_unread_count ?? 0) > 0 && <span className="settings-nav-unread" aria-label="未读消息">未读</span>}</button>)}</nav>
          <p><CircleHelp size={14} />仅显示学生模式可安全调整的选项。</p>
        </aside>
        <div className="settings-content">
          <header><div><strong>{sections.find((item) => item.id === section)?.label}</strong><p>{section === "data" ? "数据、隐私与开发者配置集中在此管理。" : "修改会立即保存，并在下次打开时恢复。"}</p></div><button className="icon-button" type="button" aria-label="关闭设置" onClick={onClose}><X size={19} /></button></header>
          <div className="settings-scroll">
            {section === "general" && <>
              <SettingGroup title="界面语言" description="语言偏好会同步保存到本地后端，并立即切换学生模式的界面语言。"><label className="settings-field"><span><Globe2 size={15} />阅读语言</span><select value={settings.locale} onChange={(event) => onChange({ locale: event.target.value })}>{supportedLocales.map((locale) => <option key={locale.code} value={locale.code}>{locale.nativeLabel} · {locale.label}</option>)}</select></label></SettingGroup>
              <SettingGroup title="学习空间" description="当前为单一同域学习空间；课程、班级和学生账号将在后续接入。"><div className="settings-note">默认工作空间：<b>{settings.default_workspace_id ?? "default"}</b></div>{canTeach && <button className="settings-link-button" type="button" onClick={onOpenTeacher}>进入教师模式 <ChevronRight size={15} /></button>}</SettingGroup>
            <SettingGroup
  title="偏好管理"
  description="将界面和学习偏好恢复为初始状态，不会删除对话或学习记录。"
>
  <div className="settings-reset-row">
    <span>
      <strong>恢复默认偏好</strong>
      <small>恢复语言、主题、阅读字号、动态效果及其他偏好。</small>
    </span>
    <button
      type="button"
      className="settings-reset-button"
      onClick={() => setResetConfirmOpen(true)}
    >
      恢复默认
    </button>
  </div>
</SettingGroup>
            </>}

{section === "appearance" && <>
  <SettingGroup title="主题" description="跟随系统，或固定为浅色、深色主题。">
    <div className="theme-grid">
      <ThemeButton active={settings.theme === "light"} icon={<Sun size={18} />} label="浅色" onClick={() => onChange({ theme: "light" })} />
      <ThemeButton active={settings.theme === "dark"} icon={<Moon size={18} />} label="深色" onClick={() => onChange({ theme: "dark" })} />
      <ThemeButton active={settings.theme === "system"} icon={<MonitorCog size={18} />} label="跟随系统" onClick={() => onChange({ theme: "system" })} />
    </div>
  </SettingGroup>
  <SettingGroup title="阅读体验" description="调整学习内容字号，并减少非必要的界面动态效果。">
    <label className="settings-field">
      <span>回答内容字号<small>仅调整聊天回答、题目和学习内容。</small></span>
      <select
        value={settings.content_font_size}
        onChange={(event) => onChange({ content_font_size: event.target.value as UserSettings["content_font_size"] })}
      >
        <option value="small">较小</option>
        <option value="medium">标准</option>
        <option value="large">较大</option>
      </select>
    </label>
    <ToggleRow
      title="减少动态效果"
      detail="减少侧栏、弹窗与内容出现时的动画。"
      checked={settings.reduce_motion}
      onChange={(checked) => onChange({ reduce_motion: checked })}
    />
  </SettingGroup>
</>}            {section === "chat" && <><SettingGroup title="回答呈现" description="控制实时回答在页面上的呈现方式。"><ToggleRow title="显示思考过程" detail="显示模型返回的推理流；教学回答本身不受影响。" checked={settings.show_reasoning} onChange={(checked) => onChange({ show_reasoning: checked })} /><label className="settings-field"><span>流式渲染节奏<small>较快更实时，较慢更稳定</small></span><select value={settings.stream_render_interval_ms} onChange={(event) => onChange({ stream_render_interval_ms: Number(event.target.value) })}><option value={0}>即时</option><option value={30}>平衡（30 ms）</option><option value={80}>平滑（80 ms）</option></select></label></SettingGroup><SettingGroup title="快捷操作" description="发送消息后，可以在学习记录中生成练习、标记待复习概念，或导出 Markdown 学习报告。"><div className="settings-note">对话发送：Enter；换行：Shift + Enter</div></SettingGroup></>}
            {section === "learning" && <><SettingGroup title="默认学习上下文" description="主题请在聊天顶部从教师启用的目录中选择；难度和教学方式可在此设置。"><div className="settings-note">当前主题：{learningContext.topic_name || "未选择"}</div><div className="settings-two-fields"><label className="settings-field"><span>难度</span><select value={learningContext.level} onChange={(event) => updateLearning({ level: event.target.value as LearningContext["level"] })}>{Object.entries(levelLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="settings-field"><span>教学方式</span><select value={learningContext.mode} onChange={(event) => updateLearning({ mode: event.target.value as LearningContext["mode"] })}>{Object.entries(modeLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div></SettingGroup><SettingGroup title="学习记录" description="会话标题、概念、待复习标记和摘要仅存储在此浏览器；聊天内容由后端会话持久化。"><div className="settings-note">打开右侧“学习记录”可查看进度并导出报告。</div></SettingGroup></>}
{section === "data" && (
  <>
    <SettingGroup
      title="数据存储"
      description="Pro_NLP 当前采用同域、本地部署。学习数据和会话记录按照当前部署环境进行存储。"
    >
      <div className="settings-note">
        实时事件用于断线恢复；已完成对话可通过学习记录导出。
      </div>
    </SettingGroup>

    <SettingGroup
      title="隐私与安全"
      description="学生界面仅显示教学相关信息，不暴露 API Key、原始 Tool JSON、运行 Trace、Token、Worker、工作区权限等开发与运维数据。"
    />

    <SettingGroup
      title="开发者配置"
      description="模型、Provider、MCP、Skills、工具策略、运行状态和调试数据统一由开发者工作台管理。"
    >
      {canDevelop && (
        <button
          className="settings-primary-button"
          type="button"
          onClick={onOpenDeveloper}
        >
          前往开发者工作台 <ChevronRight size={16} />
        </button>
      )}
    </SettingGroup>
  </>
)}
            {section === "feedback" && (canSubmitFeedback ? <>
              <SettingGroup title="提交你的建议" description="选择分类后提交，开发者可在工作台回复与更新状态。每日最多 3 条（北京时间自然日）。">
                <div className="feedback-form">
                  <label className="settings-field"><span>分类</span><select value={feedbackCategory} onChange={(event) => setFeedbackCategory(event.target.value as FeedbackCategory)} aria-label="反馈分类"><option value="feature">功能建议</option><option value="ux">体验问题</option><option value="bug">Bug 反馈</option><option value="other">其他</option></select></label>
                  <textarea value={feedback} maxLength={2000} disabled={feedbackDaily !== null && feedbackDaily.remaining <= 0} placeholder="例如：我希望在学习记录中增加错题复习计划……" onChange={(event) => { setFeedback(event.target.value); setFeedbackSubmitted(false); setFeedbackError(""); }} />
                  <div><small>{feedback.length}/2000 {feedbackDaily && <span className="feedback-quota">今日剩余 {feedbackDaily.remaining}/{feedbackDaily.limit}</span>}</small><button className="settings-primary-button" type="button" disabled={!feedback.trim() || feedbackSubmitting || (feedbackDaily !== null && feedbackDaily.remaining <= 0)} onClick={() => { const content = feedback.trim(); setFeedbackSubmitting(true); setFeedbackError(""); void api.submitFeedback(content, feedbackCategory).then((result) => { try { saveFeedback(content, userId); } catch { /* Server submission already succeeded. */ } setFeedbackSubmitted(true); setFeedback(""); setFeedbackDaily((previous) => previous ? { ...previous, remaining: result.remaining, used: previous.limit - result.remaining } : previous); return api.getOwnFeedback(); }).then((thread) => { if (thread?.thread_id) setFeedbackHistory(thread as FeedbackThread); }).catch((error) => setFeedbackError(error instanceof Error ? error.message : String(error))).finally(() => setFeedbackSubmitting(false)); }}>{feedbackSubmitting ? "发送中…" : feedbackDaily !== null && feedbackDaily.remaining <= 0 ? "今日已达上限" : "发布意见"}</button></div>
                  {feedbackDaily !== null && feedbackDaily.remaining <= 0 && <p className="settings-note feedback-quota-warning">今日已发送 {feedbackDaily.used}/{feedbackDaily.limit} 条，明天 0 点后可继续提交。</p>}
                  {feedbackSubmitted && <p className="feedback-success">意见已发送到开发者工作台。</p>}{feedbackError && <p className="error-card" role="alert">发送失败：{feedbackError}</p>}
                </div>
              </SettingGroup>
              <SettingGroup title="我的反馈与回复" description={feedbackHistory && feedbackHistory.messages.length > 0 ? `共 ${feedbackHistory.message_total ?? feedbackHistory.messages.length} 条 · 状态：${feedbackStatusLabel[feedbackHistory.status] ?? feedbackHistory.status} · 分类：${feedbackCategoryLabel[feedbackHistory.category] ?? feedbackHistory.category}` : "提交后，开发者的回复与处理状态会在这里显示。"}>
                {feedbackHistoryError && <p className="error-card" role="alert">读取失败：{feedbackHistoryError}</p>}
                {!feedbackHistory || feedbackHistory.messages.length === 0 ? <div className="settings-note">暂无历史反馈，提交后可在此查看时间线。</div> : <div className="feedback-history-collapsible"><button type="button" className="feedback-history-toggle" aria-label={feedbackHistoryOpen ? "收起消息" : "展开消息"} aria-expanded={feedbackHistoryOpen} onClick={() => void toggleFeedbackHistory()}><span><strong>消息记录</strong><small>{feedbackHistory.message_total ?? feedbackHistory.messages.length} 条消息</small></span><span>{(feedbackHistory.student_unread_count ?? 0) > 0 && <b className="feedback-history-unread">未读消息</b>}<em>{feedbackHistoryOpen ? "收起消息" : "展开消息"}</em><ChevronDown size={15} className={feedbackHistoryOpen ? "is-open" : ""} /></span></button>{feedbackHistoryOpen && <div className="feedback-history">{feedbackHistory.message_has_more && <button className="feedback-history-load-more" type="button" disabled={feedbackHistoryLoadingMore} onClick={() => void loadOlderFeedbackHistory()}>{feedbackHistoryLoadingMore ? "正在加载更早消息…" : "加载更早反馈"}</button>}{feedbackHistory.messages.map((message) => <article key={message.id} className={`feedback-history-message ${message.sender_type}`}><div><strong>{message.sender_type === "student" ? "我" : "开发者"}</strong><time><Clock3 size={10} />{new Date(message.created_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</time></div><p>{message.body}</p></article>)}</div>}</div>}
              </SettingGroup>
            </> : <SettingGroup title="意见反馈" description="意见会发送到开发者工作台，并按你的账号归档为一条独立会话。"><div className="settings-note">{FEEDBACK_DISABLED_HINT}</div></SettingGroup>)}
            {section === "updates" && <><SettingGroup title="当前版本" description={`${APP_NAME} v${APP_VERSION}`}><div className="settings-note"><b>版本号随构建自动同步</b><br />来自当前发布构建，无需手动维护。</div></SettingGroup><SettingGroup title="本次更新与修复" description={releaseNotesError ? "无法读取更新说明，请稍后重试。" : releaseNotes && releaseNotes.length > 0 ? "由开发者工作台维护，学生端实时同步。" : "暂无已发布的更新说明。"}>{releaseNotesError ? <button className="settings-link-button" type="button" onClick={() => setReleaseNotesAttempt((current) => current + 1)}>重新加载 <ChevronRight size={15} /></button> : releaseNotes === null ? <div className="settings-note">正在读取…</div> : releaseNotes.length > 0 && <div className="release-notes-list">{releaseNotes.map((note) => <article className="release-note" key={note.id}><h3>v{note.version}<small>{note.released_at.slice(0, 10)}</small></h3><ul className="release-notes">{note.notes.map((item) => <li key={item}>{item}</li>)}</ul></article>)}</div>}</SettingGroup></>}
          </div>
        </div>
      </section>
    </div>
<ConfirmDialog
  open={resetConfirmOpen}
  title="恢复默认偏好？"
  description="将恢复语言、主题、阅读字号、动态效果及其他偏好，不会删除对话或学习记录。"
  confirmLabel="恢复默认"
  onConfirm={() => {
    onReset();
    setResetConfirmOpen(false);
  }}
  onClose={() => setResetConfirmOpen(false)}
/>
</>;
}

function SettingGroup({ title, description, children }: { title: string; description: string; children?: ReactNode }) { return <section className="settings-group"><div><h2>{title}</h2><p>{description}</p></div>{children}</section>; }
function ToggleRow({ title, detail, checked, onChange }: { title: string; detail: string; checked: boolean; onChange: (checked: boolean) => void }) { return <label className="settings-toggle-row"><span><strong>{title}</strong><small>{detail}</small></span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /></label>; }
function ThemeButton({ active, icon, label, onClick }: { active: boolean; icon: ReactNode; label: string; onClick: () => void }) { return <button type="button" className={active ? "active" : ""} onClick={onClick}>{icon}<span>{label}</span></button>; }
