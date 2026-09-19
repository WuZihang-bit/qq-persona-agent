# Eidolon — QQ Persona Agent | QQ 数字分身·社交智能体

> **Eidolon**（希腊语"灵魂幻影"，作者称之为「纸鸢」——风筝飞得再远，线在主人手里）。
>
> 把你自己**蒸馏**成一个 QQ 机器人 / social agent：从聊天记录提取说话风格 → 生成人格提示词 →
> 接入 QQ，得到一个会像你一样说话和做事的 AI——短句、连发、玩梗、发表情包、
> 群里围观插话、回复带"对方正在输入…"、引用回复、联网搜索、长期记忆、多步 Agent 任务。
>
> 关键词: QQ机器人 · 数字分身 · 人格蒸馏 · Persona Agent · NapCat · AstrBot · LLM Agent · AI Companion

**仅限蒸馏你自己。** 未经他人同意蒸馏他人人格可能违反《个人信息保护法》，详见 [docs/ethics.md](docs/ethics.md)。

![status](https://img.shields.io/badge/status-v0.1_alpha-orange) ![platform](https://img.shields.io/badge/platform-QQ_NT-blue) ![license](https://img.shields.io/badge/license-GPL--3.0-green)

## 它会做什么

| 能力 | 说明 |
|---|---|
| 🎭 人格扮演 | 基于你的聊天记录蒸馏的人格提示词，学你的口头禅、句式、情绪反应 |
| 💬 拟人化发送 | 随机延迟、私聊"对方正在输入…"、多段回复连发、全局发送节流 |
| 👀 群聊围观 | 不@也按低概率插话（有梗才接，没话说保持沉默），防打扰硬约束 |
| 👑 主人捧场 | 你的发言会被高优先级接话 |
| 😂 表情包 | 自动收集群表情包（GLM 视觉打标签入库），对话中按情绪/关键词检索发送 |
| 🔗 引用回复 | 长回复自动引用触发消息，支持 `[QUOTE:序号]` 引用群内任意消息 |
| 🔍 联网搜索 | Tavily 接入，路由器自动判断何时搜索，无需指令 |
| 🧠 长期记忆 | 对话后自动抽取事实持久化，下次对话自动注入 |
| ⏰ 主动行为 | 定时提醒、事项到期跟进、群聊围观插话 |
| 🤖 主人专属Agent | 多步任务引擎：群分析/调研/长进程，断点续跑 |

## 架构

```
QQ小号 ◄── OneBot v11 ──► NapCatQQ        # 协议端（第三方开源，非本项目）
                             │
                             ▼
                         AstrBot            # bot框架（第三方开源，非本项目）
                             │
                     persona-humanizer     # 本项目：拟人化发送
                     group-lurker          # 本项目：围观插话+主人捧场
                     emoji-hub             # 本项目：表情包收集/检索
                             │
                        LLM API            # DeepSeek / GLM / ...
                             │
                    tools/ 蒸馏流水线        # 本项目：聊天记录→人格
```

## 快速开始

0. 准备：一个小号 QQ（**不要用大号**）、一个 LLM API key、（可选）GLM key + Tavily key
1. 按 [docs/deploy.md](docs/deploy.md) 部署 NapCat + AstrBot（~20 分钟）
2. 把 `plugins/` 下三个插件拷进 AstrBot 的 `data/plugins/`
3. 复制 `config.example.yaml` 为 `config.yaml`，填入你的 QQ 号和 key
4. 用 [tools/](tools/) 蒸馏你自己（见 [docs/distill.md](docs/distill.md)），或先用 `personas/example/` 手写人格跑通
5. 拉群测试：@它说话、发表情包让它收集、看它围观插话

## Agent 能力

本项目不只是聊天机器人——路由决策、12个自主工具、长期记忆、主动行为、长任务断点续跑。
完整架构与能力对照见 [docs/AGENT.md](docs/AGENT.md)。

## 文档

- [Agent架构白皮书](docs/AGENT.md)
- [部署指南](docs/deploy.md)
- [人格蒸馏流水线](docs/distill.md) ★ 核心玩法
- [插件说明](docs/plugins.md)
- [路线图](docs/roadmap.md)
- [发布操作手册](docs/publish.md)
- [风控与封号](docs/risk.md)
- [伦理与法律边界](docs/ethics.md)

## 已知限制

- 新号风控严格，频繁登录切换会被踢（见 risk.md 的养号建议）
- 表情包自动收集需要视觉模型（GLM-4V 系免费额度即可）
- 人格模拟是提示词工程，不是微调——形似容易，遇到没聊过的话题会露馅

## 致谢

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) / [AstrBot](https://github.com/AstrBotDevs/AstrBot) — 站在巨人的肩膀上
- [QQBackup 社区](https://github.com/QQBackup) — QQNT 数据库解密技术来源
- [immortal-skill](https://github.com/agenmod/immortal-skill) / [forge-skill](https://github.com/YIKUAIBANZI/forge-skill) — 人格蒸馏框架参考

## License

GPL-3.0，附加使用限制见 [LICENSE](LICENSE) 末尾 Additional Terms。
