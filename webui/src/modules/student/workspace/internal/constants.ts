import type { UserSettings } from "@/shared/types";

export const DEFAULT_SETTINGS: UserSettings = {
  locale: "zh-CN",
  theme: "light",
  content_font_size: "medium",
  reduce_motion: false,
  show_reasoning: false,
  stream_render_interval_ms: 30,
  model_profile: "deepseek",
};
