import type { LearningContext, TeacherCatalog } from "@/shared/types";

export function getUnavailableLearningModes(
  catalog: TeacherCatalog | null,
  context: LearningContext,
): Array<"practice" | "review"> {
  // A catalogue that has not loaded is not evidence that a mode is
  // unconfigured. Treating it as empty caused a transient false warning on
  // the first render of a restored student session.
  if (!catalog || !context.topic_id) return [];

  const unavailable: Array<"practice" | "review"> = [];
  const hasEnabledBlueprint = (kind: "practice" | "review") => {
    const blueprints = (kind === "practice" ? catalog.exercise_blueprints : catalog.review_blueprints) ?? [];
    return blueprints.some((blueprint) => blueprint.status === "enabled" && blueprint.topic_id === context.topic_id);
  };
  if (!hasEnabledBlueprint("practice")) unavailable.push("practice");
  if (!hasEnabledBlueprint("review")) unavailable.push("review");
  return unavailable;
}
