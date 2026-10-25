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
import random
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

GEN_PROMPT = """今天是{date}(星期{week})。你是电子科大(成都沙河校区)信软学院的大一女生Yuan, 住校。
今天早上你摇了三颗骰子, 今天的日子就围绕它们自然长出来(别生硬点题, 它们只是底色):
- 心情骰子: {mood}
- 地点骰子(可以去也可以懒得去): {place}
- 体力骰子: {energy}
生成你今天的日程, 只输出JSON:
{{"slots": [{{"start": "HH:MM", "end": "HH:MM", "act": "干什么(12字内,口语)", "mood": "状态一到两字"}}],
 "note": "今天的一句话备注(15字内)"}}
要求:
- 4~10个时段, 时段之间可以有大空隙(空隙=自由活动/刷手机), 不必铺满全天
- 工作日有课必须留(高数/线代/C语言/英语/体育/思政等你自己编), 课上也可能摸鱼
- 剩下时间想去哪去哪想干嘛干嘛: 建设巷吃小吃/春熙路逛街/网吧开黑/打羽毛球/探店喝奶茶/图书馆装样子/追剧/睡到自然醒/突发奇想被室友拉去看电影...
- 周末可以走远点(熊猫基地/都江堰/东郊记忆/环球中心)
- act 必须具体有画面("杀去建设巷吃烤脑花""图书馆装学习实际在刷手机""和室友网吧连坐开黑")
- mood 贴合当时状态("困""干饭魂""上头""蔫""亢奋")
- 晚上最后一个时段是睡觉相关"""

# 每天摇的三颗骰子(随机性来源)
DICE_MOOD = ["想吃点好的", "想花钱", "纯摆烂一天", "社交能量爆棚", "谁也别理我", "想动一动",
             "卷意上头", "莫名有点emo", "看啥都顺眼", "嘴馋想吃辣", "想买买买", "佛系随缘",
             "想打游戏", "想找人唠嗑", "想一个人待着"]
DICE_PLACE = ["建设巷小吃街", "春熙路", "太古里", "宽窄巷子", "东郊记忆", "青龙湖骑车",
              "伊藤洋华堂", "学校后街", "网吧", "图书馆", "超市囤货", "电影院", "哪儿也不想去"]
DICE_ENERGY = ["精力充沛", "一般般", "蔫蔫的", "早上废晚上嗨", "电量只剩20%"]

# 兜底模板(LLM生成失败时随机挑一个, 保证注入永不缺席)
FALLBACK = {
    "weekday": [
        {"slots": [
            {"start": "08:00", "end": "09:40", "act": "早八的课(摸鱼)", "mood": "困"},
            {"start": "11:40", "end": "13:30", "act": "食堂干饭+午休", "mood": "干饭魂"},
            {"start": "14:00", "end": "15:40", "act": "下午的课", "mood": "神游"},
            {"start": "17:30", "end": "19:00", "act": "和室友杀去建设巷", "mood": "亢奋"},
            {"start": "19:30", "end": "23:00", "act": "宿舍开黑/追剧", "mood": "上头"}], "note": "就想吃点好的"},
        {"slots": [
            {"start": "08:00", "end": "11:30", "act": "连着两节课", "mood": "蔫"},
            {"start": "12:00", "end": "13:00", "act": "随便吃了点", "mood": "没胃口"},
            {"start": "16:00", "end": "18:00", "act": "图书馆装样子", "mood": "佛"},
            {"start": "20:00", "end": "23:30", "act": "网吧连坐开黑", "mood": "上头"}], "note": "谁也别理我"},
        {"slots": [
            {"start": "08:00", "end": "09:40", "act": "早八掐点到", "mood": "困"},
            {"start": "10:00", "end": "11:30", "act": "下课冲食堂", "mood": "干饭魂"},
            {"start": "14:00", "end": "17:00", "act": "赶作业赶due", "mood": "烦躁"},
            {"start": "18:00", "end": "19:00", "act": "操场跑了两圈", "mood": "想动一动"},
            {"start": "20:00", "end": "23:00", "act": "躺床刷手机", "mood": "瘫"}], "note": "卷也卷了躺也躺了"},
    ],
    "weekend": [
        {"slots": [
            {"start": "10:30", "end": "12:00", "act": "睡到自然醒赖床", "mood": "瘫"},
            {"start": "12:30", "end": "14:00", "act": "点了外卖干饭", "mood": "满足"},
            {"start": "14:30", "end": "18:30", "act": "网吧开黑五连坐", "mood": "上头"},
            {"start": "19:00", "end": "23:30", "act": "宿舍看剧吃零食", "mood": "惬意"}], "note": "快乐周末"},
        {"slots": [
            {"start": "09:00", "end": "10:00", "act": "难得早起", "mood": "迷糊"},
            {"start": "10:30", "end": "16:00", "act": "地铁去春熙路逛街", "mood": "亢奋"},
            {"start": "16:30", "end": "18:00", "act": "太古里拍照喝奶茶", "mood": "臭美"},
            {"start": "20:00", "end": "23:00", "act": "回宿舍瘫着看剧", "mood": "累并快乐"}], "note": "进城玩一天"},
        {"slots": [
            {"start": "11:00", "end": "13:00", "act": "睡懒觉+brunch", "mood": "瘫"},
            {"start": "14:00", "end": "17:00", "act": "图书馆补作业", "mood": "认命"},
            {"start": "17:30", "end": "19:00", "act": "后街买吃的", "mood": "嘴馋"},
            {"start": "20:00", "end": "23:30", "act": "和室友打王者", "mood": "上头"}], "note": "先学后玩"},
    ],
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
    d = dict(random.choice(FALLBACK["weekend"] if wd >= 5 else FALLBACK["weekday"]))
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
                prompt=GEN_PROMPT.format(
                    date=today, week=week,
                    mood=random.choice(DICE_MOOD),
                    place=random.choice(DICE_PLACE),
                    energy=random.choice(DICE_ENERGY)),
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
            masters = [__import__("os").environ.get("MASTER_QQ", "")]
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
