import { describe, expect, it } from "vitest";

import type { LearningContext, TeacherCatalog } from "@/shared/types";

import { getUnavailableLearningModes } from "./learning-config";

const context: LearningContext = {
  topic_id: "transformer",
  topic_name: "Transformer",
  level: "beginner",
  mode: "explain",
};

const catalog = (overrides: Partial<TeacherCatalog> = {}): TeacherCatalog => ({
  workspace_id: "default",
  topics: [{ id: "transformer", name: "Transformer", description: "", status: "enabled", knowledge_points: [] }],
  exercise_blueprints: [],
  review_blueprints: [],
  guided_blueprints: [],
  ...overrides,
});

describe("learning configuration availability", () => {
  it("does not call an unloaded catalogue an unconfigured mode", () => {
    expect(getUnavailableLearningModes(null, context)).toEqual([]);
  });

  it("only treats enabled blueprints for the selected enabled topic as available", () => {
    expect(getUnavailableLearningModes(catalog({
      exercise_blueprints: [{ id: "draft", name: "草稿", topic_id: "transformer", knowledge_point_id: "", instructions: "", question_type: "简答", status: "draft", rubric: [] }],
      review_blueprints: [{ id: "review", name: "复习", topic_id: "transformer", knowledge_point_id: "", instructions: "", exercise_blueprint_id: null, status: "enabled", question_type: "简答", rubric: [] }],
    }), context)).toEqual(["practice"]);
  });
});
