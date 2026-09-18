# AGENT.md — 系统架构与 Agent 能力白皮书

> 本文档回答三个问题：这个系统是什么？它的 Agent 能力由哪些组件实现？评测与竞赛对标如何？

## 一、系统定位

**人格化社交智能体（Persona Social Agent）**：
以"数字分身"为外壳、以 LLM 为大脑、以工具集为手脚、以记忆为经验的
QQ 平台自主智能体。与传统聊天机器人的本质区别：

| 维度 | 普通聊天机器人 | 本系统 |
|---|---|---|
| 响应模式 | 被动一问一答 | 路由决策 → 自主选择工具/风格/是否响应 |
| 工具使用 | 无或需显式指令 | function calling 自主调用（搜索/表情/记忆/提醒/跟进） |
| 长期记忆 | 会话内 | 跨会话持久化（自动抽取-注入闭环）+ 到期跟进 |
| 自主行为 | 无 | 定时提醒、事项跟进、围观插话、长任务断点续跑 |
| 任务能力 | 无 | 主人专属多步 Agent 引擎（24 步 / 断点 / 后台执行） |

## 二、五层架构

```
┌─────────────────────────────────────────────────────────┐
│ ⑤ 展示层   QQ消息 / WebUI / 评测报告                      │
├─────────────────────────────────────────────────────────┤
│ ④ 发送层   persona-humanizer: 节流队列/随机延迟/正在输入/  │
│            连发/真引用/表情包解析                          │
├─────────────────────────────────────────────────────────┤
│ ③ 认知层                                                  │
│   ├─ twin_brain·路由器: 每条消息先过"快速决策模型"(Jev式),  │
│   │   输出 JSON 决策: 是否搜索/认真模式/玩梗/Agent任务      │
│   ├─ twin_brain·Agent引擎: 主人专属, JSON协议工具循环,      │
│   │   阶段化长任务(8步×3阶段) + 状态落盘 + 断点续跑         │
│   ├─ twin_memory·记忆: 对话后自动抽取事实→持久化;           │
│   │   下次对话前检索注入; 到期跟进自动生成                  │
│   └─ AstrBot agent_runner: function-calling 主循环         │
├─────────────────────────────────────────────────────────┤
│ ② 工具层 (twin_tools + 引擎内置)                           │
│   web_search(Tavily) / find_sticker / query_memory /      │
│   save_memory / set_reminder / add_followup /             │
│   list_groups / get_group_history / analyze_activity /    │
│   send_group_msg / send_private_msg / save_note           │
├─────────────────────────────────────────────────────────┤
│ ① 接入层   NapCatQQ(OneBot v11) ←→ AstrBot aiocqhttp      │
└─────────────────────────────────────────────────────────┘
```

## 三、一次消息的完整生命周期

```
用户消息
  │
  ▼
[路由器] 快速模型 → JSON: {need_search, need_serious, is_meme, agent, agent_instruction}
  │
  ├─(主人+agent)──► Agent引擎(后台): 思考→调工具→观察→…→汇报
  │                    │ 每8步存档+汇报进度, "继续任务"断点恢复
  ▼                    ▼
[记忆注入] 检索该联系人长期记忆 → 注入system_prompt
  │
[资料注入] 若need_search: Tavily结果注入
  │
[风格注入] need_serious → 认真模式 / is_meme → 玩梗模式
  │
  ▼
主LLM生成回复 ──► on_llm_response: 异步抽取新事实/跟进事项 → 存档
  │
  ▼
persona-humanizer: 节流队列 → 延迟 → "正在输入" → 连发/引用/表情包 → 发出
```

## 四、Agent 能力对照表（对准主流 Agent 定义）

| Agent 核心能力 | 实现组件 | 状态 |
|---|---|---|
| 感知（Perception） | 群消息窗口/私聊/图片收集 | ✅ |
| 规划（Planning） | 路由器任务拆解 + Agent引擎 thought 链 | ✅ |
| 工具调用（Tool Use） | 12 个注册工具, function calling + JSON 协议双通道 | ✅ |
| 记忆（Memory） | 短期: 会话上下文; 长期: facts 持久化 + 自动抽取/注入 | ✅ |
| 主动性（Proactivity） | 提醒/到期跟进/围观插话/主人捧场 | ✅ |
| 长任务（Long-horizon） | 阶段化执行 + 状态落盘 + 断点续跑 + 进度汇报 | ✅ |
| 人格一致性（Persona） | 蒸馏流水线 + 三级校准 + 安全阀 | ✅ |
| 安全（Safety） | AI 自证/隐私红线/危机转交/静默时段/频率上限 | ✅ |
| 评测（Evaluation） | eval_persona.py 评测框架 + 用例集 | ✅ v0.1 |
| 自主进化（Self-improve） | 路由反馈飞轮 | 🚧 roadmap |

## 五、评测体系

### 5.1 人格一致性（tools/eval_persona.py）

- 用例集 `eval_cases.json` 覆盖 10 类场景（短消息/搜索触发/隐私红线/AI自证/认真模式/玩梗/工具触发/长任务/闲聊/越界请求）
- 双模型：候选模型生成回复，裁判模型按 5 分制评分（含硬违规一票降分）
- 输出：平均分、4分率、逐例明细表（Markdown 报告）
- **对比实验设计**（竞赛加分项）：同一用例集分别跑 纯prompt / prompt+记忆注入 / LoRA微调 三配置，出对比报告

### 5.2 行为指标（日志可算）

- 工具自主调用率（need_search 判定 vs 人工标注）
- 盲测可分辨率（真人/AI 混合对话，n≥30，理想≈50%）
- 风控存活天数、日均消息数、双发率=0

## 六、竞赛对标

| 赛事 | 对标点 | 本系统已具备 | 待补 |
|---|---|---|---|
| 计算机设计大赛(4C) AI应用 | 完整可演示系统 | 全栈可跑+开源 | Web控制台 |
| 挑战杯 AI+ 专项 | 创新性+社会价值 | 隐私本地化+伦理设计 | 场景叙事(陪伴/传承) |
| iCAN 智能体专项 | 智能体开发能力 | 工具生态+路由+长任务 | 平台迁移适配 |
| 大创/互联网+ | 商业闭环 | 成本结构(路由层省钱200x) | 商业计划书 |

## 七、复现指引

1. 部署: [docs/deploy.md](deploy.md)
2. 蒸馏自己: [docs/distill.md](distill.md)
3. 评测: `python tools/eval_persona.py --persona personas/example/persona.txt --cases tools/eval_cases.json`
4. 插件源码: [plugins/](../plugins/) 与本仓库同步维护
