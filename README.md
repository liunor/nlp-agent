# Nova

<p align="center">
  <img src="webui/logo/nova.png" alt="Nova" width="160" />
</p>

<p align="center">
  <strong>Nova</strong> 是一个面向自然语言处理（NLP）教学与学习场景的智能助手——从一个问题开始，让提问、理解、练习与复盘更简单。
</p>

<p align="center">
  <img src="docs/assets/demo-flow.gif" alt="Nova 功能联动演示" width="82%" />
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="README.en.md">English</a> ·
  <a href="CONTRIBUTING.md">贡献</a> ·
  <a href="LICENSE">License</a>
</p>

## 四个端

| 学习者 | 教师 |
| --- | --- |
| <img src="docs/assets/screenshot-learner.png" alt="学习者界面" width="520"> | <img src="docs/assets/screenshot-teacher.png" alt="教师界面" width="520"> |

| 开发者 | 运行监控 |
| --- | --- |
| <img src="docs/assets/screenshot-developer.png" alt="开发者界面" width="520"> | <img src="docs/assets/screenshot-monitor.png" alt="运行监控界面" width="520"> |

---

## 快速开始

### 安装与运行

需要 Python 3.11 或更高版本，以及 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync                                   # 安装依赖
Copy-Item .env-example .env               # 准备配置，填写模型服务密钥与数据库连接
uv run python main.py bootstrap-db        # 初始化数据库
uv run python main.py bootstrap-developer # 创建第一个账号
uv run python main.py serve               # 启动服务
```

启动成功后，在浏览器打开 <http://127.0.0.1:8765>。

<details>
<summary>macOS / Linux</summary>

```shell
uv sync
cp .env-example .env
uv run python main.py bootstrap-db
uv run python main.py bootstrap-developer
uv run python main.py serve
```

</details>

### 登录与使用

用 `bootstrap-developer` 创建的账号登录后即可开始：

- 学习者：<http://127.0.0.1:8765/>
- 教师：<http://127.0.0.1:8765/teacher>
- 开发者：<http://127.0.0.1:8765/developer>

也可以直接在命令行里对话：

```powershell
uv run python main.py chat
```

需要运行监控时，先执行 `uv run python main.py monitor`，再打开 <http://127.0.0.1:8766/>。

## 文档

- [贡献指南](./CONTRIBUTING.md)
- [配置说明](./docs/)
- [许可证](./LICENSE)

---

本仓库采用 [MIT](./LICENSE) 许可证开源。
