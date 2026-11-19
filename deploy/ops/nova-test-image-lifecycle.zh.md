# 测试站镜像复用和延迟清理

`develop` 发布时，按 `sandbox-runtime` Git 目录的内容和 UTC 月份查找已有沙箱镜像。命中后为同一个摘要补上发布标签并核验，不重新构建。第一次、构建内容变化或进入下个月时构建新镜像。按月刷新是因为 Dockerfile 会安装 Debian 软件包。修改 CI 沙箱构建参数时应同步调整缓存键版本。`main` 的候选镜像流程照旧，正式版发布流程无需改动。

测试站通过 Web、监控、Worker、沙箱管理器和代理的发布验证后，记录使用的应用和沙箱镜像摘要。共享服务器上的定时器每 15 分钟检查一次。只有当前测试部署与最新成功记录一致、发布至少已过 15 分钟、两站均就绪且没有 CI Worker 工作时，才考虑清理。候选必须由新流程明确标记且在成功记录中出现，镜像创建及最后一次成功使用均已过 3 天。各保留最新 3 个不同的应用摘要、沙箱摘要，额外保护容器、生产、配置、备份和手工保留清单所引用的镜像。删除前确认 GHCR 仍能查到原摘要；每轮最多删除 3 个本地镜像。原有夜间任务继续使用，两者用同一把锁。

## 安装与核对

1. 先将此变更合入 `develop` 并检查测试部署的 `Resolve the immutable sandbox runtime digest` 步骤。没有已验证的部署记录时，定时器不删除镜像。
2. 在宿主机确认原有 `/usr/local/lib/nova-image-cleanup/cleanup.py` 已安装。将 `nova-test-image-cleanup.py` 安装为 root 所有、模式 `0644` 的 `/usr/local/lib/nova-image-cleanup/test-deploy-cleanup.py`。仅运行 `sudo /usr/bin/python3 -I -B /usr/local/lib/nova-image-cleanup/test-deploy-cleanup.py` 做预览。
3. 核对预览后，把本目录的 `.service` 和 `.timer` 安装到 `/etc/systemd/system/`。运行 `sudo systemd-analyze verify /etc/systemd/system/nova-test-image-cleanup.{service,timer}`，验证通过后执行 `sudo systemctl daemon-reload` 和 `sudo systemctl enable --now nova-test-image-cleanup.timer`。
4. 用 `sudo systemctl list-timers --all nova-test-image-cleanup.timer` 确认下次触发，用 `sudo journalctl -u nova-test-image-cleanup.service -n 25 --no-pager` 看最近回执。再次发布相同沙箱内容，检查是否复用同一摘要。旧部署没有记录的镜像会保留。

暂停新版任务：`sudo systemctl disable --now nova-test-image-cleanup.timer`。夜间任务独立运行。
