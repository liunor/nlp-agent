import { X } from "lucide-react";

import type { RealtimeRequestError } from "@/shared/types";

interface LearningConfigNoticeProps {
  requestError: RealtimeRequestError | null;
  modeNotice: "practice" | "review" | null;
  canManageTeaching: boolean;
  onNavigate: (path: string) => void;
  onClose: () => void;
}

function configMode(modeNotice: LearningConfigNoticeProps["modeNotice"], requestError: RealtimeRequestError | null): "practice" | "review" {
  if (modeNotice) return modeNotice;
  return requestError?.message.includes("复习") ? "review" : "practice";
}

export function LearningConfigNotice({ requestError, modeNotice, canManageTeaching, onNavigate, onClose }: LearningConfigNoticeProps) {
  if (!modeNotice && !requestError) return null;

  const isTeachingConfigurationError = requestError?.code === "teaching_configuration_error";
  const isConfigurationNotice = modeNotice !== null || isTeachingConfigurationError;
  const mode = configMode(modeNotice, requestError);
  const message = requestError?.code === "turn_conflict"
    ? "上一条请求仍在处理中，请稍候；如果长时间没有响应，请先取消上一条请求后再重试。"
    : modeNotice
      ? `请先在教师空间创建、启用并保存该主题的${mode === "practice" ? "出题" : "复习"}蓝图。`
      : requestError?.message;

  return <section className="learning-config-notice" role="alert">
    <div>
      <strong>{isConfigurationNotice ? "学习配置不可用" : "请求未完成"}</strong>
      <p>{message}</p>
    </div>
    <div>
      {canManageTeaching && isConfigurationNotice && <button type="button" className="teacher-primary-button" onClick={() => onNavigate(mode === "review" ? "/teacher/reviews" : "/teacher/exercises")}>去配置</button>}
      <button type="button" className="learning-notice-close" aria-label="关闭提示" onClick={onClose}><X size={16} /></button>
    </div>
  </section>;
}
