# Eidolon 数字分身 - 项目交接文档

> 最后更新: 2026-10-20 | 本文档是 ZCode 会话的完整交接，新会话读这份就能接上全部上下文。

## 一、项目全景

**这是什么**：QQ 数字分身系统——把主人（吴子航，QQ 1522727368，网名鸢飛）蒸馏成一个跑在云服务器上的 AI 分身（小号 Yuan，QQ 3662430655）。女生人格、暧昧关系设定。

**三个组成部分**：
1. **云端运行实例**：华为云 Flexus 2核2G（IP: 1.95.144.92），Ubuntu 24.04 + Docker（NapCat + AstrBot 双容器）
2. **开源仓库**：github.com/WuZihang-bit/qq-persona-agent（GPL-3.0，9 篇文档 + 6 插件 + 评测框架）
3. **本地开发环境**：F:\yuanfei_skill\bot（调试用）+ F:\qq-digital-twin（开源仓库）

## 二、服务器关键信息

| 项 | 值 |
|---|---|
| SSH | root@1.95.144.92 密码 REDACTED_PASSWORD（**已在聊天中暴露，建议改**） |
| 容器 | napcat / astrbot / portainer，均 restart:always，host 网络 |
| 数据目录 | /root/eidolon/data（AstrBot 挂载为 /AstrBot/data） |
| 人格配置 | /root/eidolon/masters.json（主人名单）+ data/plugins/*/group_prompt.txt |
| 面板 | AstrBot:6185（astrbot/wuzihang2026）、Portainer:9000、NapCat:6099 |

## 三、已修复的坑（全部验证过，别再踩）

1. **QQ 备份 .bak 解密**：密钥不在文件里，在登录中 QQ 进程内存里（16 字节 ASCII），用 HMAC 校验命中
2. **NapCat 容器缺 DLL**：Windows 版缺 crypto.dll/ssl.dll，从本机 QQNT 复制（云上 Linux 版无此问题）
3. **Docker Hub 拉取超时**：配了镜像加速器（dockerproxy.net 最快）
4. **LLM 全走死代理 7897**：httpx trust_env 问题。已打三重补丁：容器 network_utils.py `trust_env=False`、io.py 同款、sitecustomize.py 清代理变量
5. **图片下载失败（7897）**：同根因，已修
6. **音色列表拉取失败**：tts.py 里残留的代理注入已删
7. **VOICE 文字漏出**：插件执行顺序导致标记原样发出。已在 delay/group 两个插件的 `_build_chain` 加强制剥离
8. **"乱认主人"**：迁移时 group_prompt.txt 丢失，用 fallback 提示词（无人格）。已重建女生版+QQ 号身份对齐规则
9. **CogView 生图 429**：账号企业套餐失效（code 1314），需用户去 bigmodel.cn 处理，代码无法绕过
10. **GLM 5 小时限额（429 1308）**：每 5 小时一个窗口，到点自动重置（如 00:47）
11. **SSH 限流**：频繁连接触发华为云保护，EOFError 就是限流信号，等 2 分钟再连

## 四、当前插件清单（云上 8 个，全部在线）

| 插件 | 功能 |
|---|---|
| twin_brain | Jev 式路由（搜索/认真/玩梗/Agent 判定）+ 主人专属 Agent 引擎（群分析/长任务断点续跑） |
| twin_tools | 6 个自主工具（web_search/find_sticker/query_memory/save_memory/set_reminder/add_followup） |
| twin_memory | 长期记忆（自动抽取/注入）+ 主动引擎（提醒/到期跟进） |
| astrbot_plugin_wuzihang_delay | 拟人化发送（节流/延迟/正在输入/连发/引用/表情包） |
| astrbot_plugin_wuzihang_group | 群聊围观（不@随机插话）+ 主人捧场 + 多主人支持 |
| astrbot_plugin_fensheng_plus | 情感层/TTS 语音/梗知识库（MOSS-TTS，key 已换新） |
| emoji_hub | 表情包自动收集+打标 |
| poke_react | 戳一戳反应 |

**注意**：云上插件的 `masters` 名单统一从 `/root/eidolon/masters.json` 读取（可加多个主人 QQ）。

## 五、当前状态与已知问题

**正常**：聊天（女生人格+暧昧主人）、识图、记忆、提醒、主人指令、纯文字发空间、合并转发双向、表情包、群围观

**待修/待办**：
- [ ] 语音 TTS：服务器容器刚装好 edge-tts 并验证生成成功，但完整链路（LLM→VOICE 标记→语音条）还没实机验收
- [ ] 发空间带图：CogView 生图 429（企业套餐失效），需用户去 bigmodel.cn 处理；已做降级（生图失败自动转纯文字发空间）
- [ ] 账号风控：新号每 4-6 小时可能被踢，重新扫码即可，频率会随养号下降

## 六、关键文件位置

| 内容 | 位置 |
|---|---|
| 云端 AstrBot 数据 | 服务器 /root/eidolon/data/ |
| 人格源文件（本地） | F:\yuanfei_skill\bot\persona\wuzihang_persona.txt |
| 开源仓库 | F:\qq-digital-twin\ = github.com/WuZihang-bit/qq-persona-agent |
| 本地调试环境 | F:\yuanfei_skill\bot\（启动Eidolon.bat 一键启动） |
| 蒸馏流水线原型 | F:\yuanfei_skill\work\（v0.2 待整理进 tools/） |
| 竞赛规划 | 仓库 docs/competition-plan.md（4C 主攻，挑战杯 2027） |

## 七、常用操作命令

```bash
# SSH 连服务器
ssh root@1.95.144.92          # 密码 REDACTED_PASSWORD（建议改）

# 重启容器
docker restart napcat astrbot

# 推送开源仓库（本地 F:\qq-digital-twin）
git add -A && git commit -m "xxx" && git push
```

## 八、性格调校记录（人格演进来龙去脉）

- 反骨已删（说话太冲）、串子软化为开玩笑抖机灵
- 被问"我是谁"→ 自然回避（叫我 Yuan 就行/问这干嘛哈哈），**绝不猜主人**
- 主人识别：QQ:1522727368 + 昵称鸢飛 双特征，对外不暴露关系
- 弈星被动触发才聊、emoji 含义精确匹配才发、短句连发不写作文

## 九、v0.4 升级批次（2026-10-25，对标 MaiBot 拟人化 + 全链路运维）

**新增能力**：
- **twin_schedule 日程系统**：每天自动生成女大学生日程（LLM失败回落内置模板），回复注入"现在在干嘛+心情"；`/日程` 查看、`/日程 新` 重生成
- **读空气（group-lurker）**：普通消息 13% roll → LLM 激活度打分(0-10) ≥6 才插话；提"yuan"高把握接话；打分每小时≤20次控成本
- **群印象+称呼**：每群累计 220 条消息自动总结画像（氛围/话题/核心成员/别名）注入所有回复，根治"认错人"
- **记忆 v2（twin-memory）**：四类档案（印象/事件/关系）每 30 轮 LLM 合并去重淘汰 + 每日聊天日记 + 主动消息入档（治"发完就忘"记忆黑洞）
- **/群日报**：话题榜/活跃人物/今日金句/氛围总结
- **空间互动（astrbot_plugin_qzone）**：每天 8 点自动逛好友说说点赞评论（NapCat get_cookies 免扫码）；自动发说说已关（保留手动 /发空间）
- **点歌（astrbot_plugin_music）**：AI 点歌/卡片/语音/热评

**运维**：
- 看门狗：`tools/watchdog.sh` + cron 每 2 分钟——掉线自动重启/1小时3次熔断/凌晨2-7静默/ServerChan 微信推送
- 每日备份：`tools/backup.sh`（astrbot 数据+QQ 登录卷，留 7 天）
- 自定义镜像：`tools/Dockerfile.astrbot`（官方镜像+edge-tts/json5/apilmoji 固化，容器重建不再丢依赖）
- NapCat WebUI 状态探测：sha256(token+".napcat") 换 Credential → CheckLoginStatus

**踩坑**：人格真身在 SQLite `data_v4.db` personas 表（data/persona/*.txt 是旧副本）；qzone 插件 publish_cron 在 `trigger.` 嵌套下；服务器连不上 GitHub（插件走本机中转 tar 上传）

## 十、v0.4.1 增量（2026-10-25 晚）

- **日程联动打字延迟**：delay 插件真实应用延迟（原 _delay_for 未接线）——上课/考试 16~45s、赶作业 9~26s、躺床 14~38s、空闲 2.5~9s，另有 18% 概率"正好在看手机"秒回
- **黑话自动挖掘**：群印象总结新增 slang 字段（群特有缩写/梗/inside joke ≤5 条），随 [群黑话] 注入群聊上下文
- **主动关怀引擎**：twin_memory 每晚 18~23 点按日程在空闲时段主动找主人搭话（每天全局≤2 次、每人≤1 次、4h 内聊过则不打扰、LLM 结合日程+日记生成开场白，失败回落模板池）；发出后自动入主动记忆

## 十一、v0.4.2 增量（2026-10-25 晚二）

- **Web 控制台**：`tools/console_server.py` 服务器 8080 端口 systemd 常驻（eidolon-console 服务）——系统/日程/记忆档案/群印象/提醒跟进/主动关怀/LLM用量/看门狗日志一页看全，30s 自刷新；File Browser(免登录安全隐患)已停服让位
- **空间评论自动回复**（twin-qzone-care 新插件）：每 15 分钟扫自己说说的评论，未回评的（7 天内）按人格自动回评；每天≤5 条、静默时段不回、每轮≤3 条
- **生日祝福**：`/生日 QQ号 MM-DD [称呼]` 登记，当天 9~12 点自动送 LLM 生成的口语化祝福（每人每年一次）
- **群聊图片吐槽**：纯图消息 5% 概率 → GLM-4V-Flash（免费）识图 → 按人格吐槽一句；每小时 VLM≤6 次

## 十二、v0.4.3 日程骰子系统

- 每天生成日程前摇三颗骰子(心情15种/地点13种/体力5种)注入 prompt，当天日程围绕骰子自然生长——想干嘛干嘛、有情绪弧线(如"emo半天被糖炒栗子救回来")、允许临时起意和突发活动
- 时段 4~10 个、可不铺满全天(大空隙=自由活动)；成都本地地点池(建设巷/春熙路/太古里/东郊记忆/青龙湖/网吧/伊藤…)，周末可走远(熊猫基地/都江堰)
- 兜底模板从各1套增至工作日3套+周末3套随机挑

## 十三、v0.5 游戏化+图片记忆+守卫+表达学习（2026-10-25 晚三）

- **签到游戏化**（twin-checkin）：/签到 连续加成+羁绊分+每日运势+人格化回应（带当前日程状态）；/好感度 展示 fensheng 情感引擎好感度+等级（陌生→很重要的人）+排行；群早晚安被动（早晚窗口首个消息概率触发，日程联动话术）
- **图片记忆**（twin-imgmemory）：私聊图全收/群图25%采样，GLM-4V-Flash（免费）生成描述存档（图文件LRU 200张）；对话注入"TA最近发过的图"；/想起 关键词 搜图 /发图 序号 重发——"再发一次上次那只猫"成为可能
- **好友感知**（twin-guard）：5分钟轮询好友列表，新好友→通知主人+LLM人格化破冰打招呼（40~180s随机延迟）
- **/自检**：QQ在线+LLM连通+记忆/图片记忆/表情包/日程资产一览（QQ里一键体检）
- **/记忆导出**：全量记忆打包成JSON文件私发主人（记忆可迁移可展示）
- **表达自学习**（group）：bot发言后150秒内群友的正向反应（哈哈/牛/好活…）给该说法+2分、负向（尬/无语…）-1分；高分说法注入prompt作风格参考（不原句复读）——说话越来越对群友胃口
