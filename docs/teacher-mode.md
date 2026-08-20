# Teacher mode

Teacher mode shares the primary FastAPI/WebUI origin with student mode:

- student: `/`
- teacher: `/teacher/*`
- developer: `/developer/*`

Teacher mode is entered by a database session carrying `teacher` or
`developer` and an allowed workspace ID. The authorization boundary reloads
the role and workspace membership from MySQL on every request.

## Routes

| UI route | Purpose |
|---|---|
| `/teacher` | Overview, teaching goals, question and weakness summary |
| `/teacher/courses` | Teaching-goal editor and reserved course list |
| `/teacher/prompts` | Reserved Prompt/course/report template interfaces |
| `/teacher/questions` | Question statistics and distributions (no raw question rows) |
| `/teacher/reports` | Topic health and knowledge-point mastery evidence |

## API contracts

| Method and path | Purpose |
|---|---|
| `GET /api/v1/teacher/overview` | Goals and all local analytics in one dashboard payload |
| `GET /api/v1/teacher/goals/{workspace_id}` | Read workspace teaching goals |
| `PUT /api/v1/teacher/goals/{workspace_id}` | Update teaching goals; same-origin + CSRF required |
| `GET /api/v1/teacher/analytics` | Question aggregates and distributions (no raw rows) |
| `GET /api/v1/teacher/courses` | Reserved course repository boundary |
| `GET /api/v1/teacher/prompts` | Reserved template repository boundary |
| `GET /api/v1/teacher/reports` | Reserved durable-report repository boundary |

All query APIs accept `workspace_id`; analytics endpoints also accept `days`.
The service validates teacher/developer role and workspace access before querying.

## Evidence-based analysis

The read model aggregates structured learning records and never returns raw
student question text. It reads the structured learning state already stored on
each turn (`learning_state_json`) plus graded exercise evidence from
`nlp_learning_evidence`, `nlp_exercise_attempts`, and `nlp_guided_sessions`:

- reads topic, level, and mode from the turn's structured context (no
  keyword-based topic or question-type classification);
- summarizes questions, active students, sessions, and error turns;
- derives weak-topic risk from exercise pass-rate and average score plus guided
  misconceptions; topics with many questions but no graded evidence are marked
  "needs attention" rather than "low risk";
- aggregates per-knowledge-point exercise counts, average scores, pass rates,
  and lowest-hit rubric criteria;
- returns topic, difficulty, and mode distributions plus a daily question trend.

No additional model call, vector database, RAG pipeline, or cache is required.
The stable service/repository boundary allows a later SQL analytics job to
replace the aggregation without changing the WebUI contract.

Teaching goals currently reuse the Gateway's local versioned settings storage.
They are namespaced by `teacher_goals:{workspace_id}`. A future course database
can migrate this record behind `TeacherService`.
