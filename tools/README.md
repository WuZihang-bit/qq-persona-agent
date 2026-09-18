# 蒸馏工具链

从 QQ NT 本地数据库到人格提示词的完整流水线。

| 脚本 | 作用 | 状态 |
|---|---|---|
| qqnt_decrypt.py | 进程内存找密钥 + SQLCipher 解密（技术源自 QQBackup 社区） | 整理中，先参考 [QQBackup](https://github.com/QQBackup) |
| extract_messages.py | 从明文库提取本人消息为 jsonl（含 protobuf 解析） | 整理中 |
| analyze.py | 风格画像：口头禅/标点/emoji/时段统计 | 整理中 |
| persona_forge.py | 画像 → 人格提示词草稿（LLM辅助） | 计划中 |

算法与列名说明见 docs/distill.md。v0.2 发布完整脚本。
