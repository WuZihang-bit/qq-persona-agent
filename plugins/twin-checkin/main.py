# -*- coding: utf-8 -*-
"""签到+好感度游戏化+群早晚安
/签到       — 每日签到: 连续加成, 人格化回应(带当前日程状态)
/好感度     — 查自己的好感度(fensheng情感引擎)+签到羁绊+排行
被动: 每天第一个早晨/深夜窗口的群消息, 概率触发早安/晚安(结合日程)
"""
import asyncio
import json
import os
import random
import re
import time

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain, Reply
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.event_message_type import EventMessageType

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng"))
CK_FP = os.path.join(DATA_DIR, "checkin.json")
SCHED_NOW = os.path.join(DATA_DIR, "schedule_now.txt")
FRIEND_DIR = os.path.join(DATA_DIR, "friends")
ENABLE_GREET = False          # 群早晚安被动(用户要求先关, 改True恢复)

TIERS = [("陌生", 0), ("眼熟", 15), ("熟人", 40), ("好朋友", 80), ("挚友", 150), ("很重要的人", 300)]

CHECKIN_TMPL = [
    "签到了，第{streak}天，勉强表扬你一下",
    "哦？连续{streak}天了，有点东西",
    "又来签到，第{streak}天，记着呢",
    "{streak}天了，比我想的能坚持",
    "签到成功，虽然不知道你在坚持什么，第{streak}天了",
    "来了？第{streak}天，行吧",
]
CHECKIN_BREAK = [
    "断签了哦，从第{streak}天重新数吧",
    "昨天没来，还想连续？重开，第{streak}天",
    "断了一天，没事，今天第{streak}天，继续",
]
LUCK = ["今天运气一般般", "水逆，别乱花钱", "运气还行，适合躺", "旺！适合干饭",
        "适合打游戏的一天", "出门会捡到好吃的程度", "别惹室友，今天容易吵", "宜睡觉，忌学习"]

MORNING_TEMPL = ["早", "早啊，都几点了才有人", "起了起了", "早，我先去买包子", "这么早，卷王"]
NIGHT_TEMPL = ["晚安，我先睡了", "不行了困死了，晚安", "你们接着聊，我去睡了", "晚安，明天早八的要死", "睡了睡了"]


def _tier(score):
    name = TIERS[0][0]
    for t, v in TIERS:
        if score >= v:
            name = t
    return name


def _read_affinity(qq):
    try:
        d = json.load(open(os.path.join(FRIEND_DIR, f"{qq}.json"), encoding="utf-8"))
        return round(float(d.get("affinity", 0)))
    except Exception:
        return None


@register("twin_checkin", "wuzihang", "签到/好感度游戏化+群早晚安", "1.0.0")
class TwinCheckin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._greeted = {}          # gid -> ("M"|"N", date) 当天已问候标记
        try:
            self._data = json.load(open(CK_FP, encoding="utf-8"))
        except Exception:
            self._data = {"users": {}}

    def _save(self):
        json.dump(self._data, open(CK_FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    def _sched(self):
        try:
            return open(SCHED_NOW, encoding="utf-8").read().strip()
        except Exception:
            return ""

    # ---------- /签到 ----------
    @filter.command("签到")
    async def cmd_checkin(self, event: AstrMessageEvent):
        event.stop_event()
        qq = str(event.get_sender_id())
        today = time.strftime("%Y-%m-%d")
        u = self._data["users"].setdefault(qq, {"streak": 0, "last": "", "bond": 0, "total": 0})
        name = event.get_sender_name() or qq
        if u["last"] == today:
            await event.send(MessageChain(chain=[Plain("今天签过了，别贪")]))
            return
        broke = u["last"] and u["last"] != time.strftime(
            "%Y-%m-%d", time.localtime(time.time() - 86400))
        u["streak"] = 1 if broke else u["streak"] + 1
        u["last"] = today
        u["total"] += 1
        gain = random.randint(2, 5) + (3 if u["streak"] >= 7 else 2 if u["streak"] >= 3 else 0)
        u["bond"] += gain
        self._save()
        pool = random.choice(CHECKIN_BREAK if broke else CHECKIN_TMPL)
        aff = _read_affinity(qq)
        lines = [pool.format(streak=u["streak"]),
                 f"羁绊+{gain}(累计{u['bond']})", random.choice(LUCK)]
        cur = self._sched()
        if cur:
            lines.append(f"(我还在{cur}呢)")
        if aff is not None:
            lines.append(f"当前好感度: {aff} [{_tier(aff)}]")
        await event.send(MessageChain(chain=[Plain("\n".join(lines))]))

    # ---------- /好感度 ----------
    @filter.command("好感度")
    async def cmd_affinity(self, event: AstrMessageEvent):
        event.stop_event()
        qq = str(event.get_sender_id())
        aff = _read_affinity(qq)
        u = self._data["users"].get(qq, {})
        # 排行: 扫fensheng好友档案
        rank = []
        try:
            for f in os.listdir(FRIEND_DIR):
                if f.endswith(".json") and f[:-5].isdigit():
                    d = json.load(open(os.path.join(FRIEND_DIR, f), encoding="utf-8"))
                    a = d.get("affinity", 0)
                    if a > 0:
                        rank.append((d.get("name") or f[:-5], round(a)))
            rank.sort(key=lambda x: -x[1])
        except Exception:
            pass
        mine = f"{aff} [{_tier(aff)}]" if aff is not None else "还没聊过, 好感度未知"
        txt = (f"你的好感度: {mine}\n"
               f"签到羁绊: {u.get('bond', 0)}(连续{u.get('streak', 0)}天/累计{u.get('total', 0)}次)")
        if rank:
            txt += "\n好感度排行:\n" + "\n".join(
                f"{i+1}. {n} — {a}" for i, (n, a) in enumerate(rank[:5]))
        await event.send(MessageChain(chain=[Plain(txt)]))

    # ---------- 被动: 群早晚安(当前关闭) ----------
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group(self, event: AstrMessageEvent, *args, **kwargs):
        if not ENABLE_GREET:
            return
        try:
            gid = event.get_group_id()
            text = (event.message_str or "").strip()
            if not text or text.startswith(("/", "!", "＃")):
                return
            hm = time.localtime()
            now_hm = (hm.tm_hour, hm.tm_min)
            today = time.strftime("%Y-%m-%d")
            mark = self._greeted.get(gid)
            # 早安窗口 7:00-9:30
            if (7, 0) <= now_hm <= (9, 29) and (not mark or mark[1] != today or mark[0] != "M"):
                await self._greet(event, gid, "M", MORNING_TEMPL)
            # 晚安窗口 22:00-23:30
            elif (22, 0) <= now_hm <= (23, 29) and (not mark or mark[1] != today or mark[0] != "N"):
                await self._greet(event, gid, "N", NIGHT_TEMPL)
        except Exception:
            pass

    async def _greet(self, event, gid, kind, pool):
        if random.random() > 0.55:            # 不是每天都打招呼
            self._greeted[gid] = (kind, time.strftime("%Y-%m-%d"))
            return
        await asyncio.sleep(random.uniform(3, 10))
        cur = self._sched()
        msg = random.choice(pool)
        if cur and random.random() < 0.5:
            act = cur.split("(")[0]
            base = random.choice(pool)
            msg = f"{base}，我刚从{act}回来" if kind == "M" else f"{base}（明天还要{act}，哭了）"
        self._greeted[gid] = (kind, time.strftime("%Y-%m-%d"))
        try:
            await event.send(MessageChain(chain=[Plain(msg)]))
        except Exception:
            pass
