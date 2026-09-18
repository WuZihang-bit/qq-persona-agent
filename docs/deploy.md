# 部署指南

从零到 bot 上线，约 20-30 分钟。

## 0. 准备清单

| 项目 | 说明 |
|---|---|
| 小号 QQ | **务必新注册小号，不要用大号**。新号有风控期，见 [risk.md](risk.md) |
| LLM API | DeepSeek / 智谱GLM / 任意 OpenAI 兼容 API（充 10 元可用很久） |
| 视觉 API（可选） | 智谱 GLM key，用于表情包自动打标签（有免费额度） |
| Tavily key（可选） | [tavily.com](https://tavily.com) 免费注册，联网搜索用 |
| 一台电脑或云服务器 | 2核2G 起步；云服务器可 7×24 在线（Linux 优先） |

## 1. 部署 NapCat（协议端）

1. 从 [NapCatQQ Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载 `NapCat.Shell.Windows.Node.zip`（Windows 整合包，含独立 Node，不依赖本机 QQ）
2. 解压到任意目录，例如 `F:\bot\napcat\`
3. **已知坑**：部分版本整合包缺少 `crypto.dll` 和 `ssl.dll`，启动报 `The specified module could not be found` 时，从本机安装的 QQNT（`C:\Program Files\Tencent\QQNT\versions\<版本>\resources\app\`）复制这两个 DLL 到 napcat 目录
4. 启动：`node.exe ./index.js`，首次出现二维码，用**小号**手机 QQ 扫码
5. 之后快速登录：`node.exe ./index.js -q <小号QQ号>`

> Linux/云服务器用 Docker 部署更省心，见 NapCat 官方文档。

## 2. 部署 AstrBot（bot 框架）

```bash
python -m venv astrbot-env          # 要求 Python >= 3.12
astrbot-env\Scripts\pip install astrbot
astrbot-env\Scripts\astrbot run     # 启动，WebUI: http://localhost:6185
```

首次启动会打印默认账号密码（后续可用 `astrbot password` 重置）。

## 3. 打通 NapCat ↔ AstrBot

1. AstrBot WebUI → 平台适配 → 新增 → **aiocqhttp (OneBot v11)**，记下反向 WS 地址（默认 `ws://localhost:6199/ws`）
2. NapCat WebUI（默认 `http://localhost:6099`）→ 网络配置 → 新建 → **反向 WebSocket** → 填入上面的地址 → 启用
3. AstrBot 日志出现 `aiocqhttp(OneBot v11) 适配器已连接` 即成功

## 4. 配置大模型

AstrBot WebUI → 服务提供商 → 新建 → OpenAI 兼容：
- DeepSeek: `https://api.deepseek.com/v1`，模型 `deepseek-chat`
- 智谱GLM: `https://open.bigmodel.cn/api/paas/v4`，模型 `glm-5.3-flash`

保存并设为默认。建议 temperature 0.7~0.95（越高越跳脱）。

## 5. 安装本项目的插件

把 `plugins/` 下三个目录拷进 AstrBot 的 `data/plugins/`，重启 AstrBot。

QQ 号等身份配置通过环境变量或直接编辑插件顶部的常量：
- `persona-humanizer`：无需配置即可用
- `group-lurker`：设置 `BOT_UIN`（小号）和 `MASTER_QQ`（你的大号）
- `emoji-hub`：如需自动收集，在 `data/fensheng/config.json` 配置视觉模型

## 6. 人格

AstrBot WebUI → 人格场景 → 新建，粘贴 `personas/example/persona.txt`（替换括号内容），启用。

## 7. 验收测试

| 测试 | 期待 |
|---|---|
| 私聊发"在吗" | 短句回复，带延迟，有"正在输入" |
| 群里 @它 问问题 | 正常回复，长回复自动带引用 |
| 群里发表情包 | emoji_hub 自动收集入库（看日志"入库"） |
| "@它 来张无语的表情包" | 发出库里的真图 |
| 不@它 聊群话题 | 低概率插话（8%） |
| 问"你是AI吗" | 大方承认 |

## 8. 联网搜索（可选）

AstrBot WebUI → 配置 → 网页搜索：启用，提供商 Tavily，填入 key。

## 常见问题

| 症状 | 处理 |
|---|---|
| NapCat 报 wrapper.node 找不到 | 见上文第1步第3点（缺DLL） |
| 账号被踢下线 | 新号风控，重新扫码；降低消息频率，见 risk.md |
| 回复重复两条 | 检查是否同时启用了会响应同一条消息的多个插件 |
| 表情包发不出/发的是文字 | 表情库为空；先让群友发几张图收集，或手动入库 |
| WebUI 密码忘了 | `astrbot password` 重置 |
