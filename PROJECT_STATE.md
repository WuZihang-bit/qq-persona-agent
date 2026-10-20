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
