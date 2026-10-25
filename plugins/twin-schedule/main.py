# -*- coding: utf-8 -*-
"""日程系统(拟人核心): 每天自动生成女大学生的一天, 回复时注入当前时段状态
- 懒生成: 当天首次LLM请求触发后台生成(失败回落内置模板, 绝不阻塞请求)
- 注入: on_llm_request 给 system_prompt 追加一行 [你现在正在:xx,状态:xx]
- 松耦合: 当前状态写 schedule_now.txt, 群围观插件自己读(互不依赖)
- /日程 查看   /日程 新 重新生成(仅主人)
"""
import asyncio
import json
import os
import re
import time

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import ProviderRequest

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng"))
SCHED_FP = os.path.join(DATA_DIR, "schedule.json")
NOW_FP = os.path.join(DATA_DIR, "schedule_now.txt")

GEN_PROMPT = """今天是{date}(星期{week})。你是电子科大信软学院的大一女生Yuan, 住校。
生成你今天的日程, 只输出JSON:
{{"slots": [{{"start": "HH:MM", "end": "HH:MM", "act": "干什么(10字内,口语)", "mood": "状态一到两字"}}],
 "note": "今天的一句话备注(15字内,如'周五了好耶'/'明天考试有点慌')"}}
要求:
- 6~10个时段, 覆盖 8:00~24:00, 时段间可空隙(空隙=自由刷手机)
- 工作日要有课(高数/线代/C语言/英语/体育等, 你自己编排), 周末睡懒觉/打游戏/外出
- act 必须口语具体("早八高数课""食堂干饭""图书馆赶作业""宿舍开黑打王者""洗漱躺床刷手机")
- mood 贴合时段("困""干饭魂""烦躁""上头""瘫着")
- 晚上最后一个时段必须是睡觉相关"""

# 兜底模板(LLM生成失败时用, 保证注入永不缺席)
FALLBACK = {
    "weekday": {"slots": [
        {"start": "08:00", "end": "09:40", "act": "早八的课", "mood": "困"},
        {"start": "09:50", "end": "11:30", "act": "接着上课", "mood": "神游"},
        {"start": "11:40", "end": "13:30", "act": "食堂干饭+午休", "mood": "干饭魂"},
        {"start": "14:00", "end": "17:00", "act": "下午的课或自习", "mood": "烦躁"},
        {"start": "17:30", "end": "19:00", "act": "晚饭+校园里晃晃", "mood": "放松"},
        {"start": "19:30", "end": "22:30", "act": "宿舍写作业/开黑", "mood": "上头"},
        {"start": "23:00", "end": "24:00", "act": "躺床刷手机", "mood": "瘫着"}], "note": "平平无奇的一天"},
    "weekend": {"slots": [
        {"start": "10:00", "end": "11:30", "act": "睡懒觉赖床", "mood": "瘫"},
        {"start": "12:00", "end": "13:30", "act": "爬起来吃brunch", "mood": "迷糊"},
        {"start": "14:00", "end": "18:00", "act": "打游戏/刷剧", "mood": "上头"},
        {"start": "18:30", "end": "20:00", "act": "点外卖干饭", "mood": "满足"},
        {"start": "20:00", "end": "23:30", "act": "继续玩/赶作业", "mood": "随意"},
        {"start": "23:30", "end": "24:00", "act": "躺床刷手机", "mood": "困"}], "note": "周末躺尸"},
}

WEEK_CN = "一二三四五六日"


def _hm(s):
    try:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def _today_sched(today):
    try:
        d = json.load(open(SCHED_FP, encoding="utf-8"))
        if d.get("date") == today and d.get("slots"):
            return d
    except Exception:
        pass
    return None


def _fallback(today):
    import datetime
    wd = datetime.datetime.strptime(today, "%Y-%m-%d").weekday()
    d = dict(FALLBACK["weekend"] if wd >= 5 else FALLBACK["weekday"])
    d["date"] = today
    return d


def _current(d):
    """返回 (act, mood, note) 或 None"""
    now = time.strftime("%H:%M")
    cur = _hm(now)
    for s in d.get("slots", []):
        if _hm(s.get("start", "0:0")) <= cur < _hm(s.get("end", "0:0")):
            return s.get("act", ""), s.get("mood", ""), d.get("note", "")
    return None


@register("twin_schedule", "wuzihang", "日程系统: 每天自动生成日程+当前时段状态注入(拟人)", "1.0.0")
class TwinSchedule(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._generating = False

    # ---------- 生成 ----------
    async def _gen_today(self):
        today = time.strftime("%Y-%m-%d")
        if self._generating or _today_sched(today):
            return
        self._generating = True
        try:
            provider = self.context.get_using_provider()
            if provider is None:
                return
            week = WEEK_CN[time.localtime().tm_wday]
            r = await provider.text_chat(
                prompt=GEN_PROMPT.format(date=today, week=week),
                system_prompt="你是日程生成器, 只输出JSON。")
            raw = (getattr(r, "completion_text", "") or "").strip()
            m = re.search(r'\{.*\}', raw, re.S)
            if not m:
                return
            d = json.loads(m.group())
            slots = [s for s in d.get("slots", [])
                     if s.get("start") and s.get("end") and s.get("act")]
            if len(slots) >= 4:
                d = {"date": today, "slots": slots[:12], "note": str(d.get("note", ""))[:30]}
                json.dump(d, open(SCHED_FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                print(f"[twin_schedule] 已生成{today}日程: {len(slots)}个时段")
        except Exception as e:
            print(f"[twin_schedule] 生成失败(将用兜底模板): {e}")
        finally:
            self._generating = False

    # ---------- 注入 ----------
    @filter.on_llm_request()
    async def inject_schedule(self, event: AstrMessageEvent, req: ProviderRequest):
        try:
            today = time.strftime("%Y-%m-%d")
            d = _today_sched(today)
            if d is None:
                asyncio.create_task(self._gen_today())   # 后台生成, 本次先用兜底
                d = _fallback(today)
            c = _current(d)
            if not c:
                return
            act, mood, note = c
            line = f"[当前状态] 现在你在: {act}({mood})"
            if note:
                line += f" | 今天: {note}"
            line += "。忙(上课/考试)时回复更短更慢还能自然提一句在忙, 没事就正常聊, 别每条都报日程"
            req.system_prompt = (req.system_prompt or "") + "\n" + line
            try:
                open(NOW_FP, "w", encoding="utf-8").write(f"{act}({mood})")
            except Exception:
                pass
        except Exception:
            pass

    # ---------- /日程 ----------
    @filter.command("日程")
    async def cmd_sched(self, event: AstrMessageEvent, arg: str = ""):
        event.stop_event()
        try:
            masters = json.load(open(os.path.normpath(os.path.join(
                os.path.dirname(__file__), "..", "..", "masters.json")), encoding="utf-8")).get("masters", [])
        except Exception:
            masters = ["1522727368"]
        today = time.strftime("%Y-%m-%d")
        if arg.strip() in ("新", "new", "刷新"):
            if str(event.get_sender_id()) not in masters:
                await event.send(MessageChain(chain=[Plain("只有主人能重新生成日程")]))
                return
            try:
                os.remove(SCHED_FP)
            except Exception:
                pass
            await self._gen_today()
        d = _today_sched(today) or _fallback(today)
        lines = [f"{s['start']}-{s['end']} {s['act']}({s.get('mood','')})" for s in d.get("slots", [])]
        c = _current(d)
        cur = f"\n现在: {c[0]}({c[1]})" if c else ""
        await event.send(MessageChain(chain=[Plain(
            f"今天日程({d.get('date', today)}): {d.get('note', '')}\n" + "\n".join(lines) + cur)]))
