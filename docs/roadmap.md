# 路线图（Roadmap）

## v0.1 ✅ 当前版本

- [x] 仓库骨架 + 五篇文档 + GPL-3.0
- [x] 三插件（persona-humanizer / group-lurker / emoji-hub）脱敏发布
- [x] 示例人格 + 配置模板

## v0.2 蒸馏工具链（差异化核心，优先做）

把本地已验证的脚本整理成通用版（去 QQ 号/路径硬编码）：

- [ ] `tools/qqnt_decrypt.py` — 内存找密钥（HMAC 校验法）+ 全库解密重组
- [ ] `tools/extract_messages.py` — 本人消息提取（protobuf walker）
- [ ] `tools/analyze.py` — 风格画像（口头禅/标点/emoji/时段）
- [ ] `tools/persona_forge.py` — 画像 json → 人格提示词草稿（调 LLM）
- [ ] 一条龙入口：`python forge.py --qq 123 --out my_persona.txt`
- [ ] 附演示数据（用示例库，不放真实记录）

素材位置：本地 `yuanfei_skill/work/` 下已有能跑的原型，整理即可。

## v0.3 记忆系统

- [ ] 好友档案：每好友一个 json（亲密度/事实/open_loops 未完结话题）
- [ ] 对话结束用廉价 LLM 抽取新事实入库
- [ ] 主动追问"上次那事咋样了"（温度来源）

## v0.4 主动引擎

- [ ] 久未联系/到期话题/生日触发器（APScheduler）
- [ ] 防打扰硬约束：冷却期翻倍、每日 ≤3 人、"烦人检查"
- [ ] 深夜静默复用 group-lurker 的时段配置

## v0.5 空间发布

- [ ] 验证 NapCat get_cookies → g_tk → emotion_cgi_publish_v6 通道
- [ ] 备选：selenium 扫码 + cookie 文件（参考 QzoneHuhuRobot）
- [ ] 内容生成：人格 + 近24h聊天摘要 + 碎碎念语料；每天 0~1 条
- [ ] 风控：发布间隔 ≥6h、敏感词过滤、异常即停 48h

## 长期想法

- [ ] 插件参数迁移到 AstrBot 配置面板（不改变量）
- [ ] 语音消息支持
- [ ] 微信端适配（Gewechat，风控更严，谨慎）
- [ ] 英文 README
