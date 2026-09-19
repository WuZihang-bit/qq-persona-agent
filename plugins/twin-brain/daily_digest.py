# -*- coding: utf-8 -*-
"""群聊日报生成器
用法: python daily_digest.py <群号> [--days 1]
流程: NapCat拉历史 → 按天分组 → LLM逐天总结 → 存 data/fensheng/digests/
设计为twin_brain的Agent工具后台调用, 也可独立运行
"""
import asyncio, json, os, sys, re
from datetime import datetime, timedelta
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))

NAPCAT_HTTP = None  # 通过参数注入bot对象
DATA_DIR = r'F:\yuanfei_skill\bot\data\fensheng'
DIGEST_DIR = os.path.join(DATA_DIR, 'digests')
os.makedirs(DIGEST_DIR, exist_ok=True)

SUMMARY_PROMPT = '''你是群聊日报编辑。把群聊记录浓缩成日报，格式：
【今日群报】
🔥 热门话题: 1-3个, 每个一句话
👑 活跃之星: 前3名(昵称+条数)
💎 金句摘录: 1-2条最有梗的原话(标注是谁说的)
📊 一句话总结
要求口语化、有梗、像群友自己写的，不要AI腔。'''


async def generate(bot, group_id: int, days: int = 1, llm_call=None) -> str:
    """llm_call: async fn(prompt, system) -> str"""
    # 1. 拉历史
    r = await bot.call_action("get_group_msg_history", {"group_id": group_id, "count": 800})
    msgs = r.get("messages") if isinstance(r, dict) else (r or [])
    # 2. 按天分组
    by_day = {}
    for m in msgs:
        try:
            dt = datetime.fromtimestamp(int(m.get("time", 0)))
        except Exception:
            continue
        day = dt.strftime("%Y-%m-%d")
        by_day.setdefault(day, []).append(m)
    days_sorted = sorted(by_day)[-days:]
    # 3. 逐天总结
    reports = []
    for day in days_sorted:
        day_msgs = by_day[day]
        lines, senders = [], Counter()
        for m in day_msgs:
            name = (m.get("sender") or {}).get("nickname", "?")
            txt = str(m.get("raw_message", "") or "")[:100]
            if txt.strip():
                lines.append(f"{name}: {txt}")
                senders[name] += 1
        if not lines:
            continue
        top = "、".join(f"{k}({v})" for k, v in senders.most_common(5))
        corpus = "\n".join(lines)[-6000:]
        if llm_call:
            summary = await llm_call(f"群聊记录({day}, 共{len(lines)}条):\n{corpus}\n\n活跃: {top}", SUMMARY_PROMPT)
        else:
            summary = f"活跃: {top}\n消息数: {len(lines)}"
        reports.append(f"====== {day} ======\n{summary}")
    out = "\n\n".join(reports) or "当天没有群消息"
    # 4. 存档
    fp = os.path.join(DIGEST_DIR, f"{group_id}_{datetime.now():%Y%m%d_%H%M}.md")
    open(fp, "w", encoding="utf-8").write(out)
    return out


if __name__ == "__main__":
    print("用法: 由 twin_brain 的 Agent 引擎调用(bot对象注入)；独立运行需自行接入NapCat HTTP API")
