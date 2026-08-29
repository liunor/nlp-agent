import { Maximize2, Minimize2, Moon, PanelRightClose, PanelRightOpen, Sun, Wifi, WifiOff, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/platform/http/api";

import { Composer } from "@/modules/student/components/Composer";
import { AccountDialog } from "@/modules/student/components/AccountDialog";
import { ConfirmDialog } from "@/shared/ui/ConfirmDialog";
import { LearningContextBar } from "@/modules/student/components/LearningContextBar";
import { LearningPanel } from "@/modules/student/components/LearningPanel";
import { KnowledgeBookPanel } from "@/modules/student/components/KnowledgeBookPanel";
import { readKnowledgeBookUrl } from "@/modules/student/components/knowledgeBook";
import { LoginDialog } from "@/modules/student/components/LoginDialog";
import { MessageList } from "@/modules/student/components/MessageList";
import { SettingsDialog } from "@/modules/student/components/SettingsDialog";
import { SchoolLogo } from "@/shared/ui/SchoolLogo";
import { Sidebar, SidebarToggle } from "@/modules/student/components/Sidebar";
import { ToolDock, type ToolDockTabDropPosition, type ToolDockTool } from "@/modules/student/components/ToolDock";
import { useStudentWorkspace } from "@/modules/student/workspace/public";
import { useSessionScrollRestoration } from "@/modules/student/workspace/hooks/useSessionScrollRestoration";
import { useOptionalAuth } from "@/platform/auth/AuthContext";
import type { CourseTopic, TeacherCatalog } from "@/shared/types";

export function StudentWorkspace({ onNavigateTo, onOpenInSandbox }: { onNavigateTo?: (path: string) => void; onOpenInSandbox?: (code: string, language: string) => void } = {}) {
  const workspace = useStudentWorkspace();
  const globalAuth = useOptionalAuth();
  const learningContext = workspace.preferences.context;
  const setLearningContext = workspace.setLearningContext;
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // Student mode starts focused on the learning canvas after every page load.
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true);
  const [toolDockOpen, setToolDockOpen] = useState(() => typeof window !== "undefined" && readKnowledgeBookUrl(window.location.search).tool === "knowledge-book");
  const [toolDockExpanded, setToolDockExpanded] = useState(false);
  const [toolMenuOpen, setToolMenuOpen] = useState(false);
  const [openTools, setOpenTools] = useState<ToolDockTool[]>(() => typeof window !== "undefined" && readKnowledgeBookUrl(window.location.search).tool === "knowledge-book" ? ["book"] : []);
  const [activeTool, setActiveTool] = useState<ToolDockTool | null>(() => typeof window !== "undefined" && readKnowledgeBookUrl(window.location.search).tool === "knowledge-book" ? "book" : null);
  const [sandboxSource, setSandboxSource] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const [courseTopics, setCourseTopics] = useState<CourseTopic[]>([]);
  const [learningCatalog, setLearningCatalog] = useState<TeacherCatalog | null>(null);
  const [modeNotice, setModeNotice] = useState<"practice" | "review" | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{
  kind: "session" | "category";
  id: string;
  label: string;
  onDeleted?: () => void;
} | null>(null);
  const refreshCourseTopics = useCallback(async () => {
    try {
      const { catalog } = await api.getLearningCatalog(workspace.workspaceId);
      const topics = catalog.topics;
      setCourseTopics(topics);
      setLearningCatalog(catalog);
      const context = learningContext;
      if (context.topic_id && !topics.some((topic) => topic.id === context.topic_id)) {
        setLearningContext({ ...context, topic_id: null, topic_name: "" });
      }
    } catch {
      // Keep the last successfully loaded catalogue while offline.
    }
  }, [learningContext, setLearningContext, workspace.workspaceId]);
  useEffect(() => {
    if (workspace.bootStatus !== "ready") return;
    const initialRefresh = window.setTimeout(() => void refreshCourseTopics(), 0);
    window.addEventListener("focus", refreshCourseTopics);
    return () => {
      window.clearTimeout(initialRefresh);
      window.removeEventListener("focus", refreshCourseTopics);
    };
  }, [refreshCourseTopics, workspace.bootStatus]);
  const activeTitle = workspace.activeMeta.title ?? "新的学习对话";
  const statusText = { connected: "已连接", connecting: "正在连接", reconnecting: "正在恢复连接", offline: "离线" }[workspace.socketStatus];
  const statusOnline = workspace.socketStatus === "connected";
  const hasMessages = workspace.loadingMessages || workspace.messages.length > 0;
  const archived = useMemo(() => workspace.sessions.filter((session) => workspace.preferences.sessions[session.session_id]?.archived), [workspace.preferences.sessions, workspace.sessions]);
  const { scrollRef, onScroll } = useSessionScrollRestoration(workspace.activeSessionId, workspace.messages, workspace.loadingMessages);
  const setCollapsed = (collapsed: boolean) => { setSidebarCollapsed(collapsed); };
  const openTool = useCallback((tool: ToolDockTool) => {
    setToolDockOpen(true);
    setOpenTools((current) => current.includes(tool) ? current : [...current, tool]);
    setActiveTool(tool);
  }, []);
  const reorderTools = (draggedTool: ToolDockTool, targetTool: ToolDockTool, position: ToolDockTabDropPosition) => {
    setOpenTools((current) => {
      const draggedIndex = current.indexOf(draggedTool);
      const targetIndex = current.indexOf(targetTool);
      if (draggedIndex < 0 || targetIndex < 0 || draggedTool === targetTool) return current;
      const next = current.filter((tool) => tool !== draggedTool);
      next.splice(next.indexOf(targetTool) + (position === "after" ? 1 : 0), 0, draggedTool);
      return next;
    });
  };
  const openCodeInSandbox = useCallback((code: string, language: string) => {
    if (!/^(?:python|pytorch|py)$/i.test(language)) return;
    onOpenInSandbox?.(code, language);
    setSandboxSource(code);
    setToolDockOpen(true);
    setToolMenuOpen(false);
    setOpenTools((current) => current.includes("sandbox") ? current : [...current, "sandbox"]);
    setActiveTool("sandbox");
  }, [onOpenInSandbox]);
  const closeTool = (tool: ToolDockTool) => {
    const next = openTools.filter((item) => item !== tool);
    setOpenTools(next);
    if (activeTool === tool) setActiveTool(next[next.length - 1] ?? null);
  };
  const toggleToolDock = () => {
    setToolDockOpen((current) => {
      if (current) {
        setToolDockExpanded(false);
        setToolMenuOpen(false);
      }
      return !current;
    });
  };

  if (workspace.bootStatus === "loading") return <div className="boot-screen"><span className="boot-orbit" /><strong>正在进入 NLP 学习空间</strong><p>连接教学 Agent 与学习记录……</p></div>;
  if (workspace.bootStatus === "error") return <div className="boot-screen error"><WifiOff size={28} /><strong>暂时无法连接后端</strong><p>{workspace.error}</p><button type="button" onClick={() => location.reload()}>重新连接</button></div>;
  if (workspace.bootStatus === "unauthenticated") return <div className="app-shell unauthenticated-app-shell">
    <Sidebar sessions={[]} preferences={workspace.preferences} activeId={null} open={sidebarOpen} collapsed={sidebarCollapsed} connected={false} onClose={() => setSidebarOpen(false)} onCollapse={() => setCollapsed(true)} onExpand={() => setCollapsed(false)} onSelect={() => setLoginOpen(true)} onCreate={() => setLoginOpen(true)} onMeta={() => undefined} onAddCategory={() => { setLoginOpen(true); return ""; }} onRenameCategory={() => undefined} onDeleteCategory={() => undefined} onDelete={() => undefined} onAccount={() => setLoginOpen(true)} onSettings={() => setLoginOpen(true)} />
    <main className="thread-shell unauthenticated-student-shell">
      <header className="thread-header">
        <SidebarToggle onClick={() => setCollapsed(false)} />
        <div className="thread-title" />
        <div className="thread-header-actions" />
      </header>
      <section className="empty-thread-home">
        <div>
          <h1>《自然语言处理》智能体 欢迎您！</h1>
          <p>登录后可开始新的学习对话，并安全保存学习记录。</p>
          <Composer centered disabled={false} running={false} onSend={() => setLoginOpen(true)} onCancel={() => undefined} />
        </div>
      </section>
    </main>
    <LoginDialog open={loginOpen} onClose={() => setLoginOpen(false)} onAuthenticate={async (username, password) => {
      if (globalAuth) {
        await globalAuth.login(username, password);
      } else {
        await api.login(username, password);
        workspace.retryAuthentication();
      }
    }} />
  </div>;

  const updateContext = (context: typeof workspace.preferences.context) => {
    if ((context.mode === "practice" || context.mode === "review") && context.topic_id) {
      const blueprints = context.mode === "practice" ? learningCatalog?.exercise_blueprints : learningCatalog?.review_blueprints;
      if (!blueprints?.some((blueprint) => blueprint.topic_id === context.topic_id)) {
        setModeNotice(context.mode);
        return;
      }
    }
    workspace.setLearningContext(context);
    if (workspace.activeSessionId) workspace.updateSessionMeta(workspace.activeSessionId, { topic: context.topic_name });
  };
  const unavailableModes = (["practice", "review"] as const).filter((mode) => !!learningContext.topic_id && !(mode === "practice" ? learningCatalog?.exercise_blueprints : learningCatalog?.review_blueprints)?.some((blueprint) => blueprint.topic_id === learningContext.topic_id));
  const composer = (centered = false) => <Composer key={workspace.composerRevision} sessionId={workspace.activeSessionId} centered={centered} disabled={!statusOnline} running={workspace.isRunning} onSend={(text, attachments) => void workspace.send(text, attachments)} onCancel={workspace.cancel} onEnsureSession={workspace.ensureSession} contextControl={<LearningContextBar value={learningContext} onChange={updateContext} topics={courseTopics} unavailableModes={unavailableModes} onUnavailableMode={setModeNotice} modelProfiles={workspace.modelProfiles} modelProfile={workspace.settings.model_profile} onModelProfileChange={(modelProfile) => void workspace.patchSettings({ model_profile: modelProfile })} modelSelectionDisabled={!statusOnline || workspace.isRunning} />} />;

  return <div className={["app-shell", "student-app-shell", sidebarCollapsed ? "sidebar-is-collapsed" : "sidebar-is-expanded", toolDockOpen && toolDockExpanded && "tool-dock-expanded"].filter(Boolean).join(" ")}>
    {workspace.settingsError && <div className="error-card settings-save-error" role="alert">{workspace.settingsError}</div>}
    {(modeNotice || workspace.requestError) && <section className="learning-config-notice" role="alert"><div><strong>{modeNotice ? `${modeNotice === "practice" ? "练习" : "复习"}模式尚未配置蓝图` : "学习配置不可用"}</strong><p>{modeNotice ? `请先在教师空间创建、启用并保存该主题的${modeNotice === "practice" ? "出题" : "复习"}蓝图。` : workspace.requestError}</p></div><div><button type="button" className="teacher-primary-button" onClick={() => { const path = modeNotice === "review" ? "/teacher/reviews" : "/teacher/exercises"; if (onNavigateTo) onNavigateTo(path); else location.href = path; }}>去配置</button><button type="button" className="learning-notice-close" aria-label="关闭提示" onClick={() => { setModeNotice(null); workspace.clearRequestError(); }}><X size={16} /></button></div></section>}
    <Sidebar sessions={workspace.sessions} preferences={workspace.preferences} activeId={workspace.activeSessionId} open={sidebarOpen} collapsed={sidebarCollapsed} connected={statusOnline} onClose={() => setSidebarOpen(false)} onCollapse={() => setCollapsed(true)} onExpand={() => setCollapsed(false)} onSelect={workspace.selectSession} onCreate={() => void workspace.startNewChat()} onMeta={workspace.updateSessionMeta} onAddCategory={workspace.addCategory} onRenameCategory={workspace.renameCategory} onDeleteCategory={(id, name) => setDeleteTarget({ kind: "category", id, label: name })} onDelete={(id, title, onDeleted) =>
  setDeleteTarget({
    kind: "session",
    id,
    label: title,
    onDeleted,
  })
} onAccount={() => setAccountOpen(true)} onSettings={() => setSettingsOpen(true)} />
    <main className="thread-shell">
      <header className="thread-header">
        <SidebarToggle onClick={() => { setCollapsed(false); setSidebarOpen(true); }} />
        {hasMessages ? <div className="thread-title"><strong>{activeTitle}</strong><span className={statusOnline ? "online" : ""}>{statusOnline ? <Wifi size={12} /> : <WifiOff size={12} />}{statusText}</span></div> : <div className="thread-title" />}
        <div className="thread-header-actions">
          {toolDockOpen && <button className="icon-button tool-dock-expand-toggle" type="button" aria-label={toolDockExpanded ? "还原工具面板" : "展开工具面板"} aria-pressed={toolDockExpanded} onClick={() => setToolDockExpanded((value) => !value)}>{toolDockExpanded ? <Minimize2 size={17} /> : <Maximize2 size={17} />}</button>}
          <button className="icon-button thread-tool-toggle" type="button" aria-label={toolDockOpen ? "关闭工具侧栏" : "打开工具侧栏"} aria-pressed={toolDockOpen} onClick={toggleToolDock}>{toolDockOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}</button>
          <button className="icon-button theme-toggle" type="button" aria-label="切换主题" onClick={() => void workspace.patchSettings({ theme: workspace.settings.theme === "dark" ? "light" : "dark" })}>{workspace.settings.theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}</button>
        </div>
      </header>
      {hasMessages ? <><div className="thread-scroll" ref={scrollRef} onScroll={onScroll}><MessageList messages={workspace.messages} loading={workspace.loadingMessages} showReasoning={workspace.settings.show_reasoning} streamRenderIntervalMs={workspace.settings.stream_render_interval_ms} onFollowUp={(text) => void workspace.send(text)} /></div>{composer()}</> : <div className="empty-thread-home"><div><h1>《自然语言处理》智能体 欢迎您！</h1><p>从一个 NLP 概念、模型原理或练习问题开始。</p>{composer(true)}</div></div>}
    </main>
      <ToolDock
      open={toolDockOpen}
      expanded={toolDockExpanded}
      openTools={openTools}
      activeTool={activeTool}
      toolMenuOpen={toolMenuOpen}
      onToolMenuOpenChange={setToolMenuOpen}
      onOpenTool={openTool}
      onReorderTools={reorderTools}
      onCloseTool={closeTool}
      onActiveToolChange={setActiveTool}
      onExplainCode={(source) => {
        setToolDockOpen(false);
        setToolDockExpanded(false);
        setToolMenuOpen(false);
        void workspace.send("请解释以下 Python 代码：\n\n```python\n" + source + "\n```");
      }}
      learningPanel={<LearningPanel open onClose={() => closeTool("learning")} title={activeTitle} context={workspace.preferences.context} meta={workspace.activeMeta} messages={workspace.messages} onPrompt={(content) => { setToolDockOpen(false); setToolDockExpanded(false); setToolMenuOpen(false); void workspace.send(content); }} onMeta={(patch) => { if (workspace.activeSessionId) workspace.updateSessionMeta(workspace.activeSessionId, patch); }} />}
      knowledgeBookPanel={<KnowledgeBookPanel workspaceId={workspace.workspaceId} onAskNova={statusOnline && !workspace.isRunning ? (prompt) => { setToolDockExpanded(false); setToolMenuOpen(false); void workspace.send(prompt); } : undefined} onOpenInSandbox={openCodeInSandbox} />}
      sandboxSource={sandboxSource}
    />
    <div className="student-school-logo"><SchoolLogo /></div>
    <SettingsDialog open={settingsOpen} settings={workspace.settings} learningContext={workspace.preferences.context} roles={workspace.authSession?.roles} permissions={workspace.authSession?.permissions} userId={workspace.authSession?.user_id} onClose={() => setSettingsOpen(false)} onChange={(patch) => void workspace.patchSettings(patch)} onReset={workspace.resetSettings} onLearningContextChange={workspace.setLearningContext} onOpenDeveloper={() => { if (onNavigateTo) onNavigateTo("/developer"); else location.href = "/developer"; }} onOpenTeacher={() => { if (onNavigateTo) onNavigateTo("/teacher"); else location.href = "/teacher"; }} />
    <AccountDialog open={accountOpen} session={workspace.authSession} onClose={() => setAccountOpen(false)} onLogout={async () => { await workspace.logout(); setAccountOpen(false); }} />
    <ConfirmDialog
  open={!!deleteTarget}
  title={
    deleteTarget?.kind === "session"
      ? `删除“${deleteTarget.label}”对话？`
      : `删除“${deleteTarget?.label ?? ""}”分类？`
  }
  description={
    deleteTarget?.kind === "session"
      ? "删除后将同时清除后端对话记录，此操作无法撤销。"
      : "分类中的对话会保留，并移回“未分类”。"
  }
  onClose={() => setDeleteTarget(null)}
  onConfirm={() => {
    if (!deleteTarget) return;

    const target = deleteTarget;
    setDeleteTarget(null);

    if (target.kind === "session") {
      void workspace.deleteSession(target.id).then(() => {
        target.onDeleted?.();
      });
      return;
    }

    workspace.deleteCategory(target.id);
  }}
  />
{archived.length > 0 && <span className="sr-only">已归档 {archived.length} 个学习对话</span>}
  </div>;
}
