import { useEffect, useState, type FormEvent } from "react";
import { AlertCircle, Bot, ChevronLeft, ChevronRight, Clock, MessageCircleQuestion, RefreshCw, Search, User, X } from "lucide-react";
import { api } from "@/platform/http/api";
import type { TeacherQuestionActivity, TeacherStudentActivity } from "@/shared/types";
import { CollapsibleText } from "@/shared/ui/CollapsibleText";

interface StudentQuestionHistoryModalProps {
  student: TeacherStudentActivity;
  workspaceId: string;
  classroomId?: string;
  onClose: () => void;
}

const formatTime = (value: string) => value.replace("T", " ").slice(0, 19);

export function StudentQuestionHistoryModal({
  student,
  workspaceId,
  classroomId,
  onClose,
}: StudentQuestionHistoryModalProps) {
  const getInitialPeriod = () => {
    const end = new Date();
    const start = new Date(end);
    start.setDate(start.getDate() - 90);
    const format = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    return { start: format(start), end: format(end), search: "" };
  };

  const [draft, setDraft] = useState(getInitialPeriod);
  const [period, setPeriod] = useState(draft);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<TeacherQuestionActivity | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const params = new URLSearchParams({
      workspace_id: workspaceId,
      start_date: period.start,
      end_date: period.end,
      search: period.search,
      page: String(page),
      frequent_page: "1",
      student_id: student.user_id,
    });
    if (classroomId) params.set("classroom_id", classroomId);

    queueMicrotask(() => {
      if (!active) return;
      setLoading(true);
      setError("");
      void api.getTeacherQuestionActivity(params, controller.signal)
        .then((result) => {
          if (!active) return;
          const validPage = Math.min(page, Math.max(1, Math.ceil(result.total / 10)));
          if (validPage !== page) {
            setPage(validPage);
            return;
          }
          setData(result);
        })
        .catch((reason: unknown) => {
          if (active) setError(reason instanceof Error ? reason.message : "读取问答明细失败");
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    });

    return () => {
      active = false;
      controller.abort();
    };
  }, [workspaceId, classroomId, student.user_id, period, page, revision]);

  const handleSearch = (e: FormEvent) => {
    e.preventDefault();
    setPeriod({ ...draft });
    setPage(1);
    setRevision((v) => v + 1);
  };

  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / 10));

  return (
    <div className="teacher-modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label={`${student.display_name} 问答历史`}>
      <div className="teacher-student-history-modal" onClick={(e) => e.stopPropagation()}>
        <header className="teacher-student-modal-header">
          <div className="teacher-student-modal-user-info">
            <div className="teacher-student-modal-avatar">
              <User size={22} />
            </div>
            <div>
              <h2>{student.display_name}</h2>
              <p>
                学号 / 用户名：<strong>@{student.username || student.user_id}</strong>
                {student.top_topic && <span> · 主要主题：{student.top_topic}</span>}
              </p>
            </div>
          </div>
          <button type="button" className="teacher-student-modal-close" onClick={onClose} aria-label="关闭弹窗">
            <X size={18} />
          </button>
        </header>

        {/* 关键统计指标条：展示总共问了多少问题、会话、活跃等 */}
        <section className="teacher-student-modal-stats" aria-label="学生提问统计">
          <div className="teacher-student-modal-stat-card primary">
            <MessageCircleQuestion size={18} />
            <div>
              <span>总共提问</span>
              <strong>{student.questions} 个问题</strong>
            </div>
          </div>
          <div className="teacher-student-modal-stat-card">
            <span>参与会话</span>
            <strong>{student.sessions} 次</strong>
          </div>
          <div className="teacher-student-modal-stat-card">
            <span>活跃天数</span>
            <strong>{student.active_days} 天</strong>
          </div>
          <div className="teacher-student-modal-stat-card">
            <span>异常问题</span>
            <strong className={student.error_questions > 0 ? "is-warning" : ""}>{student.error_questions} 次</strong>
          </div>
          <div className="teacher-student-modal-stat-card">
            <span>最近活跃</span>
            <strong>{student.last_active ?? "暂无活跃"}</strong>
          </div>
        </section>

        {/* 搜索与日期过滤栏 */}
        <form className="teacher-student-modal-filter" onSubmit={handleSearch}>
          <div className="teacher-student-modal-dates">
            <label>
              <span>从</span>
              <input
                type="date"
                aria-label="筛选开始日期"
                value={draft.start}
                onChange={(e) => setDraft({ ...draft, start: e.target.value })}
              />
            </label>
            <label>
              <span>至</span>
              <input
                type="date"
                aria-label="筛选结束日期"
                value={draft.end}
                onChange={(e) => setDraft({ ...draft, end: e.target.value })}
              />
            </label>
          </div>
          <div className="teacher-student-modal-search">
            <Search size={14} />
            <input
              type="text"
              placeholder="搜索提问关键词..."
              aria-label="搜索提问关键词"
              value={draft.search}
              onChange={(e) => setDraft({ ...draft, search: e.target.value })}
            />
          </div>
          <button type="submit" disabled={loading}>筛选</button>
        </form>

        {/* 用户视角提问与模型答复记录 */}
        <div className="teacher-student-modal-content">
          <div className="teacher-student-modal-content-head">
            <h3>用户视角真实问答明细</h3>
            {data && <span>当前时间段包含 {data.total} 条提问记录</span>}
          </div>

          {loading ? (
            <div className="teacher-student-modal-loading">
              <RefreshCw className="spin" size={20} />
              <span>正在读取问答记录…</span>
            </div>
          ) : error ? (
            <div className="teacher-student-modal-error">
              <AlertCircle size={20} />
              <span>{error}</span>
              <button type="button" onClick={() => setRevision((v) => v + 1)}>重试</button>
            </div>
          ) : !data?.items.length ? (
            <div className="teacher-student-modal-empty">
              <MessageCircleQuestion size={28} />
              <p>{student.questions === 0 ? "该学生在统计范围内零提问。" : "当前筛选范围内未找到提问记录。"}</p>
            </div>
          ) : (
            <div className="teacher-student-conversation-list" role="feed" aria-label="问答历史列表">
              {data.items.map((item) => (
                <article key={item.id} className="teacher-student-conversation-card">
                  <div className="teacher-student-conversation-time">
                    <Clock size={12} />
                    <span>{formatTime(item.created_at)}</span>
                    {item.status && (
                      <span className={`teacher-student-status-badge ${item.status}`}>
                        {item.status === "completed" ? "已完成" : item.status}
                      </span>
                    )}
                  </div>

                  {/* 用户视角：学生提问 */}
                  <div className="teacher-student-turn student-turn">
                    <div className="turn-avatar user-avatar">
                      <User size={14} />
                    </div>
                    <div className="turn-content">
                      <div className="turn-sender">学生提问：</div>
                      <CollapsibleText text={item.question} maxLines={4} maxLength={120} />
                    </div>
                  </div>

                  {/* 用户视角：AI 模型答复 */}
                  <div className="teacher-student-turn bot-turn">
                    <div className="turn-avatar bot-avatar">
                      <Bot size={14} />
                    </div>
                    <div className="turn-content">
                      <div className="turn-sender">AI 模型答复：</div>
                      {item.answer ? (
                        <CollapsibleText text={item.answer} maxLines={8} maxLength={220} />
                      ) : (
                        <p className="turn-empty-answer">模型未返回内容或暂无答复。</p>
                      )}
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>

        {/* 分页控制 */}
        <footer className="teacher-student-modal-footer">
          <span>共 {data?.total ?? 0} 条提问记录 · 第 {page} / {pages} 页</span>
          <div className="teacher-student-modal-pagination">
            <button
              type="button"
              disabled={loading || page <= 1}
              onClick={() => setPage((p) => p - 1)}
              aria-label="上一页"
            >
              <ChevronLeft size={15} />
              <span>上一页</span>
            </button>
            <button
              type="button"
              disabled={loading || page >= pages}
              onClick={() => setPage((p) => p + 1)}
              aria-label="下一页"
            >
              <span>下一页</span>
              <ChevronRight size={15} />
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
