import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Moon, Sun } from "lucide-react";

import "./app/styles.css";
import { Composer } from "./modules/student/components/Composer";
import { MessageList } from "./modules/student/components/MessageList";
import { LearningContextBar } from "./modules/student/components/LearningContextBar";
import { ThinkingLevelControl } from "./modules/student/components/ThinkingLevelControl";
import type { ChatMessage, CourseTopic, LearningContext, RuntimeModelProfile, ThinkingLevel } from "./shared/types";

// Independent Vite entry for inspecting real controls without authentication or API calls.
const topics: CourseTopic[] = [
  { id: "preview-basics", name: "自然语言处理基础", description: "", status: "enabled", knowledge_points: [] },
  { id: "preview-transformer", name: "Transformer 与注意力机制", description: "", status: "enabled", knowledge_points: [] },
];
const modelProfiles: Record<string, RuntimeModelProfile> = {
  "thinking-preview": { label: "思考模型（预览）", provider: "preview", available: true, supported_levels: ["quick", "light", "deep", "extreme"] },
  "plain-preview": { label: "普通模型（预览）", provider: "preview", available: true, supported_levels: [] },
};

function Preview() {
  const demoAnswer = Array.from({ length: 8 }, (_, index) => `### ${index + 1}. 注意力机制与信息汇总\n\n在自注意力中，同一组输入向量分别投影为 Query、Key 和 Value。Query 与 Key 的匹配程度决定权重，再用权重对 Value 加权求和。\n\n- Query 表示当前要寻找的信息。\n- Key 用于计算输入之间的匹配程度。\n- Value 是最终参与信息汇总的内容。\n\n这是一段本地模拟回答，可以滚动查看两侧滚动条的样式。`).join("\n\n");
  const [messages, setMessages] = useState<ChatMessage[]>([
    { id: "demo-user", turnId: "demo-turn", role: "user", content: "请介绍注意力机制中 Q、K、V 的含义。", createdAt: new Date().toISOString() },
    { id: "demo-assistant", turnId: "demo-turn", role: "assistant", content: demoAnswer, status: "completed", createdAt: new Date().toISOString() },
  ]);
  const [activeSession, setActiveSession] = useState(1);
  const sendDemo = (content: string) => {
    const turnId = crypto.randomUUID();
    setMessages((current) => [...current,
    { id: crypto.randomUUID(), turnId, role: "user", content, createdAt: new Date().toISOString() },
    { id: crypto.randomUUID(), turnId, role: "assistant", content: "本地模拟回复\n\n" + demoAnswer, status: "completed", createdAt: new Date().toISOString() },
    ]);
  };
  const [thinking, setThinking] = useState<ThinkingLevel>("deep");
  const [modelProfile, setModelProfile] = useState("thinking-preview");
  const supportedLevels = modelProfiles[modelProfile].supported_levels ?? [];
  const [context, setContext] = useState<LearningContext>({ topic_id: null, topic_name: "", level: "beginner", mode: "explain" });
  const [dark, setDark] = useState(false);

  return <div className="app-shell student-app-shell sidebar-is-expanded">
    <aside className="sidebar">
      <div className="sidebar-brand"><strong>Nova · NLP Learning Agent</strong></div>
      <button type="button" className="new-chat" onClick={() => setMessages([])}>＋ 新建模拟对话</button>
      <div className="session-scroll" aria-label="模拟会话列表">
        {Array.from({ length: 50 }, (_, index) => <button key={index} type="button"
          style={{ display: "block", width: "100%", padding: "14px 10px", border: 0, borderRadius: 8, textAlign: "left", cursor: "pointer", color: "var(--fg)", background: activeSession === index + 1 ? "var(--muted-bg)" : "transparent" }}
          onClick={() => setActiveSession(index + 1)}>注意力机制学习 · 会话 {index + 1}</button>)}
      </div>
      <div style={{ padding: 16, color: "var(--muted)", fontSize: 12 }}>本地模拟会话</div>
    </aside>
    <main className="thread-shell">
      <header className="thread-header">
        <div className="thread-title"><strong>注意力机制学习 · 会话 {activeSession}</strong></div>
        <div className="thread-header-actions">
          <button type="button" aria-label={dark ? "切换浅色模式" : "切换深色模式"} onClick={() => {
            document.documentElement.classList.toggle("dark", !dark);
            setDark(!dark);
          }}>{dark ? <Sun size={18} /> : <Moon size={18} />}</button>
        </div>
      </header>
      <section className="thread-scroll" aria-label="模拟会话内容">
        <MessageList messages={messages} loading={false} showReasoning={false} onFollowUp={sendDemo} />
      </section>
      <div style={{ padding: "0 16px 16px" }}>
          <Composer disabled={false} running={false} onSend={sendDemo} onCancel={() => undefined}
            thinkingControl={supportedLevels.length > 0 ? <ThinkingLevelControl modelLabel={modelProfiles[modelProfile].label} value={thinking} supportedLevels={supportedLevels} onChange={setThinking} /> : null}
            contextControl={<LearningContextBar value={context} onChange={setContext} topics={topics}
              modelProfiles={modelProfiles} modelProfile={modelProfile} onModelProfileChange={setModelProfile} />} />
      </div>
    </main>
  </div>;
}

createRoot(document.getElementById("root")!).render(<Preview />);
