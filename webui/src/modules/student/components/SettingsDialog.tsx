import { BadgeInfo, BookOpenCheck, ChevronRight, CircleHelp, Database, Gauge, Globe2, MessageSquarePlus, MonitorCog, Moon, Settings2, Sun, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { api } from "@/platform/http/api";
import type { LearningContext, ReleaseNoteEntry, UserSettings } from "@/shared/types";
import { supportedLocales } from "@/shared/i18n/config";
import { saveFeedback } from "@/shared/utils/feedback";
import { APP_NAME, APP_VERSION } from "@/shared/version";

type SettingsSection = "general" | "appearance" | "chat" | "learning" | "data" | "advanced" | "feedback" | "updates";

const sections: Array<{ id: SettingsSection; label: string; icon: typeof Settings2 }> = [
  { id: "general", label: "通用", icon: Settings2 },
  { id: "appearance", label: "外观", icon: Sun },
  { id: "chat", label: "对话与流式", icon: Gauge },
  { id: "learning", label: "学习体验", icon: BookOpenCheck },
  { id: "data", label: "数据与隐私", icon: Database },
  { id: "advanced", label: "高级设置", icon: MonitorCog },
  { id: "feedback", label: "意见反馈", icon: MessageSquarePlus },
  { id: "updates", label: "版本与更新", icon: BadgeInfo },
];

const levelLabel: Record<LearningContext["level"], string> = { beginner: "入门", intermediate: "进阶", advanced: "高阶" };
const modeLabel: Record<LearningContext["mode"], string> = { explain: "讲解", socratic: "苏格拉底追问", practice: "练习", review: "复习" };

export function SettingsDialog({ open, settings, learningContext, roles = [], onClose, onChange, onLearningContextChange, onOpenDeveloper, onOpenTeacher }: {
  open: boolean;
  settings: UserSettings;
  learningContext: LearningContext;
  roles?: string[];
  onClose: () => void;
  onChange: (patch: Partial<UserSettings>) => void;
  onLearningContextChange: (context: LearningContext) => void;
  onOpenDeveloper: () => void;
  onOpenTeacher: () => void;
}) {
  const [section, setSection] = useState<SettingsSection>("general");
  const [feedback, setFeedback] = useState("");
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);
  const [feedbackError, setFeedbackError] = useState("");
  const [releaseNotes, setReleaseNotes] = useState<ReleaseNoteEntry[] | null>(null);
  const [releaseNotesError, setReleaseNotesError] = useState(false);
  const [releaseNotesAttempt, setReleaseNotesAttempt] = useState(0);
  useEffect(() => {
    if (!open || section !== "updates" || releaseNotes !== null) return;
    api.listPublishedReleaseNotes()
      .then(({ items }) => { setReleaseNotesError(false); setReleaseNotes(items); })
      .catch(() => setReleaseNotesError(true));
  }, [open, section, releaseNotes, releaseNotesAttempt]);
  if (!open) return null;
  const canTeach = roles.includes("teacher") || roles.includes("developer") || roles.includes("admin");
  const canDevelop = roles.includes("developer") || roles.includes("admin");
  const updateLearning = (patch: Partial<LearningContext>) => onLearningContextChange({ ...learningContext, ...patch });

  return (
    <div className="dialog-backdrop settings-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="settings-dialog" role="dialog" aria-modal="true" aria-label="偏好设置" onMouseDown={(event) => event.stopPropagation()}>
        <aside className="settings-nav">
          <div className="settings-nav-brand"><Settings2 size={19} /><span><strong>偏好设置</strong><small>学习空间</small></span></div>
          <nav>{sections.map(({ id, label, icon: Icon }) => <button key={id} type="button" className={section === id ? "active" : ""} onClick={() => setSection(id)}><Icon size={16} />{label}</button>)}</nav>
          <p><CircleHelp size={14} />仅显示学生模式可安全调整的选项。</p>
        </aside>
        <div className="settings-content">
          <header><div><strong>{sections.find((item) => item.id === section)?.label}</strong><p>{section === "advanced" ? "模型、工具与运行时配置在开发者工作台统一管理。" : "修改会立即保存，并在下次打开时恢复。"}</p></div><button className="icon-button" type="button" aria-label="关闭设置" onClick={onClose}><X size={19} /></button></header>
          <div className="settings-scroll">
            {section === "general" && <>
              <SettingGroup title="界面语言" description="语言偏好会同步保存到本地后端，并立即切换学生模式的界面语言。"><label className="settings-field"><span><Globe2 size={15} />阅读语言</span><select value={settings.locale} onChange={(event) => onChange({ locale: event.target.value })}>{supportedLocales.map((locale) => <option key={locale.code} value={locale.code}>{locale.nativeLabel} · {locale.label}</option>)}</select></label></SettingGroup>
              <SettingGroup title="学习空间" description="当前为单一同域学习空间；课程、班级和学生账号将在后续接入。"><div className="settings-note">默认工作空间：<b>{settings.default_workspace_id ?? "default"}</b></div>{canTeach && <button className="settings-link-button" type="button" onClick={onOpenTeacher}>进入教师模式 <ChevronRight size={15} /></button>}</SettingGroup>
            </>}
            {section === "appearance" && <SettingGroup title="主题" description="跟随系统，或固定为浅色、深色主题。"><div className="theme-grid"><ThemeButton active={settings.theme === "light"} icon={<Sun size={18} />} label="浅色" onClick={() => onChange({ theme: "light" })} /><ThemeButton active={settings.theme === "dark"} icon={<Moon size={18} />} label="深色" onClick={() => onChange({ theme: "dark" })} /><ThemeButton active={settings.theme === "system"} icon={<MonitorCog size={18} />} label="跟随系统" onClick={() => onChange({ theme: "system" })} /></div></SettingGroup>}
            {section === "chat" && <><SettingGroup title="回答呈现" description="控制实时回答在页面上的呈现方式。"><ToggleRow title="显示思考过程" detail="显示模型返回的推理流；教学回答本身不受影响。" checked={settings.show_reasoning} onChange={(checked) => onChange({ show_reasoning: checked })} /><label className="settings-field"><span>流式渲染节奏<small>较快更实时，较慢更稳定</small></span><select value={settings.stream_render_interval_ms} onChange={(event) => onChange({ stream_render_interval_ms: Number(event.target.value) })}><option value={0}>即时</option><option value={30}>平衡（30 ms）</option><option value={80}>平滑（80 ms）</option></select></label></SettingGroup><SettingGroup title="快捷操作" description="发送消息后，可以在学习记录中生成练习、标记待复习概念，或导出 Markdown 学习报告。"><div className="settings-note">对话发送：Enter；换行：Shift + Enter</div></SettingGroup></>}
            {section === "learning" && <><SettingGroup title="默认学习上下文" description="主题请在聊天顶部从教师启用的目录中选择；难度和教学方式可在此设置。"><div className="settings-note">当前主题：{learningContext.topic_name || "未选择"}</div><div className="settings-two-fields"><label className="settings-field"><span>难度</span><select value={learningContext.level} onChange={(event) => updateLearning({ level: event.target.value as LearningContext["level"] })}>{Object.entries(levelLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="settings-field"><span>教学方式</span><select value={learningContext.mode} onChange={(event) => updateLearning({ mode: event.target.value as LearningContext["mode"] })}>{Object.entries(modeLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div></SettingGroup><SettingGroup title="学习记录" description="会话标题、概念、待复习标记和摘要仅存储在此浏览器；聊天内容由后端会话持久化。"><div className="settings-note">打开右侧“学习记录”可查看进度并导出报告。</div></SettingGroup></>}
            {section === "data" && <><SettingGroup title="数据存储" description="Pro_NLP 当前采用同域、本地部署。不会从学生设置页暴露 Provider 密钥、工具权限或系统 Trace。"><div className="settings-note">实时事件用于断线恢复；已完成对话可通过学习记录导出。</div></SettingGroup><SettingGroup title="隐私说明" description="学生界面只显示教学语义。运行 Trace、Token、Worker 与工具参数仅在独立的开发者监控平台可见。">{canDevelop && <button className="settings-link-button" type="button" onClick={onOpenDeveloper}>打开开发者工作台 <ChevronRight size={15} /></button>}</SettingGroup></>}
            {section === "advanced" && <><SettingGroup title="开发者配置" description="模型、Provider、MCP、Skills、工具策略、运行状态和调试数据由本产品的开发者工作台统一管理。">{canDevelop && <button className="settings-primary-button" type="button" onClick={onOpenDeveloper}>前往开发者工作台 <ChevronRight size={16} /></button>}</SettingGroup><SettingGroup title="为什么不在这里显示？" description="学生模式避免暴露 API Key、原始 Tool JSON、工作区权限和 Agent 运维细节，以保持教学体验清晰、安全。" /></>}
            {section === "feedback" && <SettingGroup title="提交你的建议" description="欢迎反馈功能建议、学习体验问题或内容改进方向。当前意见会持久保存在此浏览器中。"><div className="feedback-form"><textarea value={feedback} maxLength={1000} placeholder="例如：我希望在学习记录中增加错题复习计划……" onChange={(event) => { setFeedback(event.target.value); setFeedbackSubmitted(false); setFeedbackError(""); }} /><div><small>{feedback.length}/1000</small><button className="settings-primary-button" type="button" disabled={!feedback.trim()} onClick={() => { try { saveFeedback(feedback); setFeedbackSubmitted(true); setFeedback(""); } catch (error) { setFeedbackError(error instanceof Error ? error.message : String(error)); } }}>发布意见</button></div>{feedbackSubmitted && <p className="feedback-success">已将本次意见保存在此浏览器。</p>}{feedbackError && <p className="error-card" role="alert">保存失败：{feedbackError}</p>}</div></SettingGroup>}
            {section === "updates" && <><SettingGroup title="当前版本" description={`${APP_NAME} v${APP_VERSION}`}><div className="settings-note"><b>版本号随构建自动同步</b><br />来自当前发布构建，无需手动维护。</div></SettingGroup><SettingGroup title="本次更新与修复" description={releaseNotesError ? "无法读取更新说明，请稍后重试。" : releaseNotes && releaseNotes.length > 0 ? "由开发者工作台维护，学生端实时同步。" : "暂无已发布的更新说明。"}>{releaseNotesError ? <button className="settings-link-button" type="button" onClick={() => setReleaseNotesAttempt((current) => current + 1)}>重新加载 <ChevronRight size={15} /></button> : releaseNotes === null ? <div className="settings-note">正在读取…</div> : releaseNotes.length > 0 && <div className="release-notes-list">{releaseNotes.map((note) => <article className="release-note" key={note.id}><h3>v{note.version}<small>{note.released_at.slice(0, 10)}</small></h3><ul className="release-notes">{note.notes.map((item) => <li key={item}>{item}</li>)}</ul></article>)}</div>}</SettingGroup></>}
          </div>
        </div>
      </section>
    </div>
  );
}

function SettingGroup({ title, description, children }: { title: string; description: string; children?: ReactNode }) { return <section className="settings-group"><div><h2>{title}</h2><p>{description}</p></div>{children}</section>; }
function ToggleRow({ title, detail, checked, onChange }: { title: string; detail: string; checked: boolean; onChange: (checked: boolean) => void }) { return <label className="settings-toggle-row"><span><strong>{title}</strong><small>{detail}</small></span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /></label>; }
function ThemeButton({ active, icon, label, onClick }: { active: boolean; icon: ReactNode; label: string; onClick: () => void }) { return <button type="button" className={active ? "active" : ""} onClick={onClick}>{icon}<span>{label}</span></button>; }
