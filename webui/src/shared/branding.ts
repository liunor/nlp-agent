// Temporary competition presentation: set to true after the competition to
// restore the original branding in the student-facing locations, including About team affiliations.
export const SHOW_STUDENT_SCHOOL_BRANDING = false;

export const STUDENT_AGENT_LABEL = SHOW_STUDENT_SCHOOL_BRANDING
  ? "LSNU NLP Learning Agent"
  : "NLP Learning Agent";

export const STUDENT_COPYRIGHT = SHOW_STUDENT_SCHOOL_BRANDING
  ? "© 2026 乐山师范学院自然语言处理教学平台"
  : "© 2026 自然语言处理教学平台";
