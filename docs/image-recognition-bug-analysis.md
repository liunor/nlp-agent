# 图像识别与多图超时：历史故障记录

本文整理自 2026-09-11 的本地排查记录，相关代码修复已随 [PR #185](https://github.com/liunor/nlp-agent/pull/185) 合并。下面描述的是修复前的问题，不代表当前实现仍存在这些缺陷。

公开版本省略本地绝对路径、进程信息和模型推理过程。原始现场报告保留在本地备份中。

## 现象与原因

一次上传多张图片时，视觉工具调用失败后仍有后续重试，外层回合最终达到 120 秒时限，用户没有收到有效的逐图结果。

已确认的外部阻塞是百炼返回 `403 / AllocationQuota.FreeTierOnly`。本地代码修复不能解除云端账户的免费额度限制；需密钥所有者检查对应模型的额度及计费配置。开启付费调用可能产生费用，应由账户所有者决定。

## 修复前的代码问题及处理

| 问题 | 影响 | PR #185 的处理 |
| --- | --- | --- |
| 文档自动路由没有感知提问 | 返回 OCR 文本而没有回答用户问题 | 传递提问标志，文档问答使用 OCR + VLM |
| VLM 结构契约过严 | 额外字段或缺少像素坐标使结果解析失败 | 容忍额外字段、支持 answer，使用语义表格单元格 |
| 解析异常计入 provider 熔断 | 格式错误累积导致路由停用 | 将结构化解析错误与 provider 可用性故障分开 |
| fusion 缺少降级 | VLM 失败时丢弃已完成 OCR 结果 | 保留可用结果并明确标注降级；本地计费准入错误不降级放行 |
| 多图预算没有充分计算排队批次 | 并发上限为 2 时，多张图片挤占 Worker 与 Gateway 时限 | 共用有界预算，按批次计算并保留交付时间 |
| OpenCV 文字布局假阳性 | 照片或插画被误判为表格 | 增加保守的文字布局判定，弱信号交由 VLM 处理 |
| 每次创建独立 OCR 引擎 | 重复加载模型，增加初始化开销 | 进程级复用引擎并保护并发访问 |
| 云端错误信息过于笼统 | 无法区分额度、鉴权、限流与超时 | 返回可操作的分类错误，避免无效整轮重试 |
| 图片参数说明不清晰 | 将文件名误写成不符合沙箱要求的路径 | 明确使用当前会话上传图片的文件名 |

主要实现位置：

- [视觉路由](../server/tools/vision/router.py)、[契约](../server/tools/vision/contracts.py)、[信号检测](../server/tools/vision/signals.py)
- [图像分析服务](../server/tools/vision/service.py)、[OCR](../server/tools/vision/ocr.py)、[VLM](../server/tools/vision/vlm.py)
- [共享图片预算](../core/vision_execution.py)、[逐图结果收集](../server/tools/vision/batch.py)
- [Gateway 回合执行](../gateway/turn_execution.py)、[Worker 执行](../server/tools/worker_tool.py)

## 对原始推断的修正

- 原始记录中部分图片名称与实际图片内容不一致，不能根据那些名称断言分类结果。
- 四张图片并非全部被判定为表格；复核中有三张被误判。
- 一个 Worker 可以并发调用多个图像工具。仅凭模型计划与最终 Worker 数量不同，不能认定存在执行故障。
- 增加 Worker 数量不会提高共享图像工具的并发上限。需要解决的是预算、降级与结果交付，而不是单纯增加 Worker。
- Gateway 回合超时与模型路由的熔断器是不同机制，不应混用术语。

## 图片识别与图片展示是两条发布链路

PR #185 包含识别、超时与 CSRF 修复，但不包含消息气泡的图片放大和原图预览。服务器使用最新提交时，仍可能显示旧的 60×60 缩略图，因为展示修改当时只存在于本地工作区。

当前后续 PR 补充 [MessageList](../webui/src/modules/student/components/MessageList.tsx)、[ImagePreviewDialog](../webui/src/modules/student/components/ImagePreviewDialog.tsx) 与[样式](../webui/src/app/styles.css)，将缩略图改为保持比例的较大显示，并支持页内原图预览。

## 验证边界

本地回归使用模拟模型响应及隔离数据，覆盖路由、解析、降级、错误分类、超时和逐图结果保留。测试通过不表示云端账户具有可用额度，也不代替真实部署环境中的 MySQL、Redis 与浏览器检查。
