import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Moon, Sun } from "lucide-react";

import "./app/styles.css";
import { Composer } from "./modules/student/components/Composer";
import { LearningContextBar } from "./modules/student/components/LearningContextBar";
import { ThinkingLevelControl } from "./modules/student/components/ThinkingLevelControl";
import type { CourseTopic, LearningContext, RuntimeModelProfile, ThinkingLevel } from "./shared/types";

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
  const [thinking, setThinking] = useState<ThinkingLevel>("deep");
  const [modelProfile, setModelProfile] = useState("thinking-preview");
  const supportedLevels = modelProfiles[modelProfile].supported_levels ?? [];
  const [context, setContext] = useState<LearningContext>({ topic_id: null, topic_name: "", level: "beginner", mode: "explain" });
  const [dark, setDark] = useState(false);

  return <div className="app-shell student-app-shell sidebar-is-collapsed">
    <main className="thread-shell">
      <header className="thread-header">
        <div className="thread-title" />
        <div className="thread-header-actions">
          <button type="button" aria-label={dark ? "切换浅色模式" : "切换深色模式"} onClick={() => {
            document.documentElement.classList.toggle("dark", !dark);
            setDark(!dark);
          }}>{dark ? <Sun size={18} /> : <Moon size={18} />}</button>
        </div>
      </header>
      <section className="empty-thread-home">
        <div>
          <h1>《自然语言处理》智能体 欢迎您！</h1>
          <p>从一个 NLP 概念、模型原理或练习问题开始。</p>
          <Composer centered disabled={false} running={false} sendDisabled onSend={() => undefined} onCancel={() => undefined}
            thinkingControl={supportedLevels.length > 0 ? <ThinkingLevelControl modelLabel={modelProfiles[modelProfile].label} value={thinking} supportedLevels={supportedLevels} onChange={setThinking} /> : null}
            contextControl={<LearningContextBar value={context} onChange={setContext} topics={topics}
              modelProfiles={modelProfiles} modelProfile={modelProfile} onModelProfileChange={setModelProfile} />} />
        </div>
      </section>
    </main>
  </div>;
}

createRoot(document.getElementById("root")!).render(<Preview />);
