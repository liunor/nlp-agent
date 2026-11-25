# 文件功能实施与验证记录

日期：2026-11-25。分支：`feature/unified-file-workflow`；基线：`origin/develop @ f8ed56aa`。

## 已实现

- 聊天框支持多选、拖入图片及 PDF、DOCX、XLSX、文本/代码文件；文档上传使用两路并发队列、取消与重试。同一次上传有幂等标识，重名文档自动区分，存入“聊天附件”目录。
- 可以从文件空间选择文件，也可以在文件列表点击“提问”添加到当前对话。这些动作引用已有 ID，不复制原件。旧聊天图片上传、下载地址继续兼容。
- PDF 使用浏览器原生页面查看；DOCX 提供按原文顺序排列的段落/表格阅读视图；XLSX 读取工作表单元格值。旧文本/Markdown/代码预览保持样式，超长或编码不支持时可切换有界分段阅读。
- 本地 PDF/DOCX 可在浏览器内阅读，只有用户点击“上传并提问”才上传。这两类本地原件不默认持久保存，刷新后需重新导入；旧本地文本缓存保留兼容，不做静默上传或迁移。
- 新增 `list_user_files`、`read_user_file`、`search_user_file`、`inspect_user_file`、`analyze_user_image`。文件权限使用实时登录和当前账号/workspace；图片分析复用已有 OCR/视觉路由及计费生命周期。
- 文件 ID 经已授权父目录链映射成当前 `/files/...`，继续复用现有 FUSE 数据和权限。读文档不创建用户交互内核，普通 scratch 的原行为不变。
- `generate_user_file` 使用固定格式生成器，经单独挂载 `/files` 的受控 scratch 写入任务暂存文件；确认大小/hash 后才发布为正式文件。支持基础文字/代码、JSON、CSV/TSV、XLSX、DOCX、PDF，以及 PNG/JPEG 图表。不执行传入的代码内容，不覆盖现有用户文件。
- 生成结果使用持久化请求标识，已完成结果的重试先查询原件，满额时仍可返回已有结果；失败暂存按任务 ID 清理，超时遗留由 Manager 的有界回收处理。
- 附件 ID、版本和展示快照复用现有 durable dispatch task 的 `file_refs` 字段，在同一持久化事务内保存；历史卡片不依赖文件名找回文件。旧图片标记保留兼容。
- 预览和下载链接携带已知 hash；文件被 FUSE 修改后，历史预览明确提示版本变化，允许用户选择查看当前版本。
- 原配额保持；仅对存储上传路径将 Nginx 请求体上限设为 26 MiB，覆盖原有 25 MiB 单文件配额和 multipart 开销。课堂发布脚本兼容新增限流位置，原限流和内部头清理继续生效。

## 实现边界

- Word 首期为 DOCX 基础阅读/文字报告，不承诺 Word 原始分页、浮动对象、图表、公式或旧 DOC 转换。
- PDF 文本按页读取。扫描页明确返回需要 OCR，不将空文字当作已完整读取；批量扫描 PDF OCR、PPTX 和完整 Office 排版转换仍属于后续阶段。
- XLSX 不执行公式；生成单元格作为数据保存。PDF 是基础文字报告，图片生成是结构化图表，不是任意 AI 绘画。
- 不新增向量库或永久全文派生副本。文档解析使用独立进程、排队时限、单次输出预算和硬超时；Linux 使用内存/CPU 限制并剥离解析进程的应用密钥环境。
- 文件生成必须具有现有 Docker Manager/FUSE 写能力；不自动打开写开关，也不改变交互内核原有确认要求。

## 验证结果

- 前端完整单元集：97 个文件、751 项通过；真实 FastAPI 集成单独运行后确认本机 MySQL `127.0.0.1` 不可连接。未将该集成测试删除或改为默认跳过。
- TypeScript 检查、ESLint、生产构建通过。构建保留项目既有的大包提示。
- 后端文件/工具/旧图片、Redis/Gateway、FUSE/沙箱、迁移和发布脚本的最终验收集：330 项通过，3 项依赖外部环境的用例跳过。最终日志为 `.artifacts/file-workflow-validation/backend-acceptance-final.log`。
- 课堂发布脚本回归：58 项通过，2 项依赖外部环境的用例跳过。
- Word、Excel、PDF、PNG/JPEG 生成样本使用真实读取库打开，验证内容与格式；同时验证同请求重试、不覆盖、跨账号/workspace 隔离及回收文件拒读。
- 执行过完整后端套件：当时结果为 2278 通过、173 跳过、80 失败、70 错误。随后修复了本次新增字段的测试契约和上传路由对发布脚本的影响，相关模块复测通过。其余失败通过原始 develop 对照确认了 MySQL 不可用及已有基线断言等限制；不据此宣称全量后端已通过。
- 在独立、未修改的 develop 工作区对照了同组模块：23 个失败与改动前一致；另外 13 个模块抽查中，12 个失败/错误同样在原始基线存在，主要为 MySQL 连接拒绝。发布脚本用例在基线能通过，因此作为本次真实兼容回归修复。
- 两名独立审查者检查了权限、FUSE 发布、计费和前端引用链；已修正当前读锁、加锁顺序、暂存发布、排队后重新授权、图片计费和版本固定等问题。

本地原始验证日志位于 `.artifacts/file-workflow-validation/`，不纳入代码提交。`uv lock --check --offline`、Ruff 和 Python 编译检查也已通过。

## 部署与剩余验收

1. 先安装锁定依赖并执行 `alembic upgrade head`。本次迁移仅增加个人文件的上传幂等标识、生成来源标识及唯一约束，不迁移/复制用户原件。
2. 更新 Web、Worker 和 Sandbox Manager 后再使用新增工具。沙箱镜像沿用现有 pandas/openpyxl/Matplotlib/Pillow 和 Noto CJK 字体，不增加 LibreOffice 常驻服务。
3. 沿用已授权的 `SANDBOX_ENABLE_FILES_BRIDGE`、`SANDBOX_FILES_BRIDGE_WRITABLE` 配置；管理员若设置超过 25 MiB 的自定义上传上限，需要同步调整部署层请求体硬上限。
4. 在实际 Linux + MySQL + FUSE + runsc 环境运行 `tests/test_sandbox_files_bridge_linux.py`。已扩展该用例验证受控 DOCX 生成、FUSE 持久化及发布后重新读取。Windows 单元结果不能替代这项验收。

尚未合并、推送或部署。本次不修改主工作区原有的未提交内容。本机 Docker CLI 存在，但 Linux 引擎命名管道不可用，无法在当前环境临时提供 MySQL/FUSE 验收服务。
