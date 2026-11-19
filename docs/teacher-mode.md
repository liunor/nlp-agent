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

## 班级申请

学生在“偏好设置 → 我的班级”填写班级码、提交申请并查看审核状态；入口按 `classroom:join_request:submit` 权限展示。教师在 `/teacher/classrooms` 管理班级码与审核。

guest 审批通过后，在同一事务中成为 student 并建立班级成员关系；已有 student 也需要申请或加入具体班级。仅拥有 student 角色不等于已加入班级。审批升级可刷新当前有效会话的权限版本；降权、停用、移除成员或密码变更仍走安全撤销路径。


## 学习分析中的提问分析

`GET /api/v1/teacher/questions` 返回分页的提问原文和多人重复提问统计。接口按当前任教班级的有效学生授权；开发者可选择任一班级、全部班级或 `classroom_id=everyone` 的所有有效账号。原文只在这个已授权接口返回，不改变其他汇总接口。

`start_date`、`end_date` 必填，含起止当天，按配置的统计时区换算 UTC 查询，最长 365 天。`search` 筛选问题内容；`student_id`、`group_id` 仅筛选明细，不改变多人高频表。`page` 和 `frequent_page` 分别分页，`page_size` 默认 10、最大 50。

高频问题仅合并大小写、空白与标点差异，至少涉及两名不同用户，按人数、次数降序排列；不推断不同问法的语义相似性。页面使用固定表单与表格，提问日期筛选与原有练习分析时间筛选分别标识。


AI 内容分析同时接受提问证据和练习证据。`question_start_date`、`question_end_date`、`question_search` 对应上方提问筛选；原有课程、知识点和练习时间筛选仍用于练习诊断。无主题提问也参与提问解读，但不能据其频次判断掌握率。

模型只接收最多5组高频问题和12条原文样例，隐藏用户身份字段并遮盖明显邮箱、手机号。每条提问解读必须引用提供的证据编号，界面中的次数、人数和原文由后端绑定；AI服务不可用时展示基础摘要，不冒充模型结论。
