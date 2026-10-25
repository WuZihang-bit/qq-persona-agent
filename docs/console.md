# Web 控制台 — bot 详情显示面板

> 一个零依赖的只读可视化面板（纯 Python 标准库），单文件部署，30 秒自动刷新。
> 演示/答辩时直接投屏这一页，bot 的"数字人生"一目了然。

## 面板内容

| 区块 | 展示 |
|---|---|
| **系统** | 看门狗状态（online/offline）、容器存活、磁盘/内存 |
| **今日日程** | 骰子生成的全天时段（几点在干嘛+心情）、当前时段高亮、今日备注 |
| **资产** | 表情包数、待提醒、待跟进、今晚主动关怀计划（几点→谁） |
| **LLM 用量** | 各模型调用量/输入输出 token（读 AstrBot 的 SQLite 统计表） |
| **记忆档案** | 每个联系人的：对话轮次、档案条目数、原料数、最近聊天日记、主动消息记录 |
| **群印象** | 每个群的氛围画像/常聊话题/核心成员/黑话词典 |
| **看门狗日志** | 最近掉线/重启/熔断事件 |

## 部署（Linux 服务器）

```bash
# 1. 放置脚本(仓库 tools/console_server.py), 修改脚本顶部 BASE 为你的数据目录
# 2. systemd 常驻(开机自启+崩溃拉起)
sudo tee /etc/systemd/system/eidolon-console.service <<'EOF'
[Unit]
Description=Eidolon Web Console
After=network.target
[Service]
ExecStart=/usr/bin/python3 /root/eidolon/console_server.py 8080
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now eidolon-console

# 3. 云安全组放行对应端口, 浏览器访问
#    http://<YOUR_SERVER_IP>:8080
```

## 注意

- **只读面板**：不提供任何写操作，泄露顶多暴露统计数据；敏感环境建议加 nginx basic auth 或只绑 localhost + SSH 隧道
- 数据来源全部是本地文件（fensheng/ 目录 JSON + AstrBot SQLite 只读打开），bot 重启不影响面板
- 踩坑记录：`nohup ... &` 会被 SSH 会话关闭带走，**服务器常驻进程一律用 systemd**
