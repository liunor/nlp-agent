# 存储额度计数与错误定位

账户存储额度、服务器数据池、物理磁盘可用空间是三个独立限制。清理旧容器镜像会释放物理磁盘，不会增加某个账户的逻辑存储额度；Credits 对话额度也不等于存储额度。

## 字节计数

Web 的异步结算、Worker 的同步结算以及存储用量页面必须使用相同的字节口径：

- LangGraph checkpoint、blob、write 是不透明二进制，直接对原始列执行 `OCTET_LENGTH`。不能先转换成文本；无效 UTF-8 转换可能被截断或变成 NULL，导致少计。
- 文本和 JSON 使用数据库文本序列化后的字节长度；SQL NULL 计为 0。JSON `null` 与 SQL NULL 不同，仍计入其序列化字节。
- 聊天图片按持久化文件大小计入通用空间；个人文件（含回收站）按既有文件表大小计入文件空间。

读取用量及现有正常结算路径会按该口径重新计算账户台账。修正少计后，用量可能上升，这不代表新创建了这些历史数据。本修复不批量重写台账、不改变角色配额、不删除会话、图片或 checkpoint。

## 错误原因

WebSocket 和应用级 HTTP 错误保留 `storage_quota_exceeded`，新增不含账户信息的 `storage_reason`。文件管理和图片上传的 HTTP 413 保留既有响应形状，改用相同的安全提示文案。

| storage_reason | 含义 |
| --- | --- |
| `account_core_limit` | 账户通用空间不足以保存本次内容 |
| `account_files_limit` | 账户个人文件空间不足 |
| `file_size_limit` | 超出单文件限制 |
| `file_count_limit` | 超出文件数量限制 |
| `global_data_limit` | 服务器账户数据池达到上限 |
| `global_files_limit` | 服务器个人文件池达到上限 |
| `disk_reserve_limit` | 写入后会侵占磁盘预留空间 |
| `unknown` | 未分类的存储限制，不推断账户已满 |

异常内部文本仍供既有诊断使用，公开响应不包含内部账户 ID 或 SQL。服务器级限制应由管理员处理，不能统一要求用户清理个人聊天。

## 回归与部署后核对

`tests/test_storage_accounting_errors.py` 覆盖原始二进制 SQL 表达式、HTTP/WebSocket 分类、原异常导入兼容及额度边界。图片上传回归位于 `tests/test_uploads.py`。

`tests/test_storage_quota_integration.py::test_binary_checkpoint_usage_matches_admission_and_usage_view` 在 CI 的已迁移 MySQL 8.4 中插入独立测试用户、无效 UTF-8/NUL 的 checkpoint 和 SQL NULL，核对 Web、Worker、用量页面与额度边界。整个测试事务回滚。未配置 MySQL 时该测试跳过，不能据此宣称真实数据库回归通过。

部署后应先核对 Web 与 Worker 都包含本修复，再对一个专用测试账号比较页面用量与二进制字节统计，并验证新对话能正常保存。若仍出现拦截，应保留原因码、时间和环境，结合账户额度、预留、数据池及磁盘情况定位，不自动清理数据或扩大配额。旧正式站的台账查询若报结构兼容错误，必须标记为未核实，不能把失败查询后的空结果当成零用量。
