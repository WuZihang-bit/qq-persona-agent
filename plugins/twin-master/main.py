import os
import re
# -*- coding: utf-8 -*-
"""主人指挥系统: 以指令直接调度bot(仅主人可用)
/群列表                    — 查看bot所在的群
/看消息 <群号> [条数]       — 拉取群最近消息原文
/总结聊天 <群号> [条数]     — AI总结群最近聊天
/总结合并转发              — 总结最近的合并转发(私聊发给bot后用)
/发言 <群号> <内容>         — 让bot在指定群主动发言(说你想让它说的)
/私发 <QQ号> <内容>        — 让bot私聊某人发内容
/打包发言 <群号>           — 把本群最近消息打包成合并转发私发给你
权限: 仅 MASTER_QQ。所有指令带审计日志。
"""
import asyncio
import json

def _load_masters():
    try:
        with open(os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "masters.json")), encoding="utf-8") as f:
            return json.load(f).get("masters", ["1522727368"])
    except Exception:
        return ["1522727368"]

def _is_master_qq(qq):
    return str(qq) in _load_masters()

async def ca(bot, action, payload):
    """aiocqhttp call_action 只收kwargs, 此包装接受dict"""
    return await bot.call_action(action, **payload)

import sys
import time
from datetime import datetime

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain, Node
from astrbot.core.message.message_event_result import MessageChain

MASTER_QQ = "1522727368"
BOT_UIN = "3662430655"
AUDIT_FP = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "logs", "master_commands.log"))
DAILY_SEND_CAP = 20            # 主动发言每日上限(防滥用/防风控)
CAP_FILE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "master_send_counter.json"))


def _audit(action, detail=""):
    os.makedirs(os.path.dirname(AUDIT_FP), exist_ok=True)
    with open(AUDIT_FP, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%m-%d %H:%M:%S}] {action} {detail}\n")


def _cap_ok() -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        d = json.load(open(CAP_FILE, encoding="utf-8"))
    except Exception:
        d = {"day": today, "n": 0}
    if d.get("day") != today:
        d = {"day": today, "n": 0}
    return d["n"] < DAILY_SEND_CAP, d


def _cap_inc():
    ok, d = _cap_ok()
    d["n"] += 1
    json.dump(d, open(CAP_FILE, "w", encoding="utf-8"))


@register("twin_master", "your-name", "主人指挥系统: 指令化调度bot发言/总结/查看", "1.0.0")
class TwinMaster(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    def _is_master(self, event: AstrMessageEvent) -> bool:
        if not _is_master_qq(str(event.get_sender_id())):
            return False
        return True

    async def _deny(self, event: AstrMessageEvent):
        event.stop_event()
        await event.send(MessageChain(chain=[Plain("这个指令只有主人能用")]))

    # ---------- /群列表 ----------
    @filter.command("群列表")
    async def cmd_groups(self, event: AstrMessageEvent):
        if not self._is_master(event):
            await self._deny(event); return
        event.stop_event()
        _audit("群列表")
        bot = getattr(event, "bot", None)
        r = await ca(bot, "get_group_list")
        lines = [f"{g.get('group_name', '?')} — {g.get('group_id')}" for g in r]
        await event.send(MessageChain(chain=[Plain("Bot所在群:\n" + "\n".join(lines))]))

    # ---------- /看消息 ----------
    @filter.command("看消息")
    async def cmd_read(self, event: AstrMessageEvent, group_id: int, count: int = 30):
        if not self._is_master(event):
            await self._deny(event); return
        event.stop_event()
        count = min(count, 100)
        _audit("看消息", f"group={group_id} n={count}")
        bot = getattr(event, "bot", None)
        try:
            r = await ca(bot, "get_group_msg_history", {"group_id": group_id, "count": count})
        except Exception:
            r = await ca(bot, "get_chat_history", {"group_id": group_id, "count": count})
        msgs = r.get("messages") if isinstance(r, dict) else (r or [])
        lines = []
        for m in msgs[-count:]:
            name = (m.get("sender") or {}).get("nickname", "?")
            txt = str(m.get("raw_message", "") or "")[:80]
            try:
                ts = datetime.fromtimestamp(int(m.get("time", 0))).strftime("%H:%M")
            except Exception:
                ts = ""
            lines.append(f"[{ts}]{name}: {txt}")
        out = "\n".join(lines)[-3500:] or "该群没有消息"
        await event.send(MessageChain(chain=[Plain(out)]))

    # ---------- /总结聊天 ----------
    @filter.command("总结聊天")
    async def cmd_summarize(self, event: AstrMessageEvent, group_id: int, count: int = 100):
        if not self._is_master(event):
            await self._deny(event); return
        event.stop_event()
        count = min(count, 300)
        _audit("总结聊天", f"group={group_id} n={count}")
        yield event.plain_result("正在读群消息并总结..")
        bot = getattr(event, "bot", None)
        try:
            r = await ca(bot, "get_group_msg_history", {"group_id": group_id, "count": count})
        except Exception:
            r = await ca(bot, "get_chat_history", {"group_id": group_id, "count": count})
        msgs = r.get("messages") if isinstance(r, dict) else (r or [])
        lines = []
        for m in msgs[-count:]:
            name = (m.get("sender") or {}).get("nickname", "?")
            lines.append(f"{name}: {str(m.get('raw_message', '') or '')[:80]}")
        provider = self.context.get_using_provider()
        resp = await provider.text_chat(
            prompt=f"以下是群聊记录, 请总结: 1)聊了什么话题 2)谁最活跃 3)有梗/重要的内容\n\n" + "\n".join(lines)[-6000:],
            system_prompt="你是群聊分析助手。输出简明中文总结, 分点, 不超过200字。")
        text = (getattr(resp, "completion_text", "") or "").strip() or "总结失败"
        await event.send(MessageChain(chain=[Plain(text[:800])]))

    # ---------- /群日报 ----------
    @filter.command("群日报")
    async def cmd_daily(self, event: AstrMessageEvent, group_id: int, count: int = 200):
        """/群日报 <群号> [条数] — 话题榜/活跃人物/今日金句/氛围"""
        if not self._is_master(event):
            await self._deny(event); return
        event.stop_event()
        count = min(count, 300)
        _audit("群日报", f"group={group_id} n={count}")
        yield event.plain_result("正在生成群日报..")
        bot = getattr(event, "bot", None)
        try:
            r = await ca(bot, "get_group_msg_history", {"group_id": group_id, "count": count})
        except Exception:
            r = await ca(bot, "get_chat_history", {"group_id": group_id, "count": count})
        msgs = r.get("messages") if isinstance(r, dict) else (r or [])
        lines = []
        for m in msgs[-count:]:
            name = (m.get("sender") or {}).get("nickname", "?")
            lines.append(f"{name}: {str(m.get('raw_message', '') or '')[:90]}")
        provider = self.context.get_using_provider()
        resp = await provider.text_chat(
            prompt="以下是群聊记录, 生成一份群日报(轻松口吻,像朋友写的):\n"
                   "1) 今日话题榜(2-4个话题,各配一句毒舌点评)\n"
                   "2) 活跃人物TOP3(各一句话画像)\n"
                   "3) 今日金句/群聊圣经(挑1-3条原文,注明是谁说的)\n"
                   "4) 一句话氛围总结\n不超过350字。\n\n" + "\n".join(lines)[-7000:],
            system_prompt="你是群日报小编, 输出轻快有梗的中文日报。")
        text = (getattr(resp, "completion_text", "") or "").strip() or "日报生成失败"
        await event.send(MessageChain(chain=[Plain(text[:1200])]))

    # ---------- /生日 ----------
    @filter.command("生日")
    async def cmd_birthday(self, event: AstrMessageEvent, qq: int, date: str, *, name: str = ""):
        """/生日 <QQ号> <MM-DD> [称呼] — 登记, 当天早上bot主动送祝福"""
        if not self._is_master(event):
            await self._deny(event); return
        event.stop_event()
        if not re.match(r"^\d{2}-\d{2}$", date):
            await event.send(MessageChain(chain=[Plain("日期格式: MM-DD, 例如 03-15")]))
            return
        fp = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "birthdays.json"))
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            d = {}
        d[str(qq)] = {"date": date, "name": name.strip() or str(qq)}
        json.dump(d, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        _audit("生日", f"qq={qq} {date} {name}")
        await event.send(MessageChain(chain=[Plain(f"已登记: {name or qq}({qq}) 生日 {date}, 当天早上会收到祝福")]))

    # ---------- /发言 ----------
    @filter.command("发言")
    async def cmd_speak(self, event: AstrMessageEvent, group_id: int, *, content: str = ""):
        if not self._is_master(event):
            await self._deny(event); return
        content = (content or "").strip()
        if not content:
            event.stop_event()
            await event.send(MessageChain(chain=[Plain("格式: /发言 群号 内容")]))
            return
        ok, d = _cap_ok()
        if not ok:
            event.stop_event()
            await event.send(MessageChain(chain=[Plain(f"今日主动发言已达上限({DAILY_SEND_CAP}条), 防风控明天再来")]))
            return
        event.stop_event()
        _audit("发言", f"group={group_id} content={content[:50]}")
        _cap_inc()
        bot = getattr(event, "bot", None)
        await ca(bot, "send_group_msg", {"group_id": group_id,
                                                 "message": [{"type": "text", "data": {"text": content[:600]}}]})
        await event.send(MessageChain(chain=[Plain(f"已在群{group_id}发出:\n{content[:100]}")]))

    # ---------- /私发 ----------
    @filter.command("私发")
    async def cmd_whisper(self, event: AstrMessageEvent, qq: int, *, content: str = ""):
        if not self._is_master(event):
            await self._deny(event); return
        content = (content or "").strip()
        if not content:
            event.stop_event()
            await event.send(MessageChain(chain=[Plain("格式: /私发 QQ号 内容")]))
            return
        ok, d = _cap_ok()
        if not ok:
            event.stop_event()
            await event.send(MessageChain(chain=[Plain(f"今日发送已达上限({DAILY_SEND_CAP}条)")]))
            return
        event.stop_event()
        _audit("私发", f"qq={qq} content={content[:50]}")
        _cap_inc()
        bot = getattr(event, "bot", None)
        await ca(bot, "send_private_msg", {"user_id": qq,
                                                   "message": [{"type": "text", "data": {"text": content[:600]}}]})
        await event.send(MessageChain(chain=[Plain(f"已私聊{qq}发出")]))

    # ---------- /打包发言 ----------
    @filter.command("打包发言")
    async def cmd_pack(self, event: AstrMessageEvent, group_id: int, count: int = 20):
        if not self._is_master(event):
            await self._deny(event); return
        event.stop_event()
        count = min(count, 50)
        _audit("打包发言", f"group={group_id} n={count}")
        bot = getattr(event, "bot", None)
        try:
            r = await ca(bot, "get_group_msg_history", {"group_id": group_id, "count": count})
        except Exception:
            r = await ca(bot, "get_chat_history", {"group_id": group_id, "count": count})
        msgs = r.get("messages") if isinstance(r, dict) else (r or [])
        nodes = []
        for m in msgs[-count:]:
            name = (m.get("sender") or {}).get("nickname", "?")
            txt = str(m.get("raw_message", "") or "")[:500]
            if not txt.strip():
                continue
            nodes.append({"type": "node", "data": {"uin": BOT_UIN, "name": name,
                                                   "content": [{"type": "text", "data": {"text": txt}}]}})
        if not nodes:
            await event.send(MessageChain(chain=[Plain("该群没有可打包的消息")]))
            return
        try:
            await ca(bot, "send_private_forward_msg",
                                  {"user_id": int(_load_masters()[0]), "messages": nodes})
            await event.send(MessageChain(chain=[Plain(f"已打包{len(nodes)}条合并转发私发给你")]))
        except Exception as e:
            # 降级: 纯文本
            lines = [f"{(m.get('sender') or {}).get('nickname','?')}: {str(m.get('raw_message',''))[:60]}"
                     for m in msgs[-count:]]
            await event.send(MessageChain(chain=[Plain("合并接口异常, 文本版:\n" + "\n".join(lines)[-3000:])]))

    # ---------- /发空间 ----------
    @filter.command("发空间")
    async def cmd_qzone(self, event: AstrMessageEvent, kind: str = "诗", *, topic: str = ""):
        """发空间 [诗/看法/知识] [主题] — 后台创作并公开发布"""
        if not self._is_master(event):
            await self._deny(event); return
        event.stop_event()
        if kind not in ("诗", "看法", "知识"):
            topic = (kind + " " + topic).strip()
            kind = "诗"
        topic = topic.strip() or {"诗": "深夜的城市与风", "看法": "大学里最该学会的一件事", "知识": "时间管理"}[kind]
        _audit("发空间", f"kind={kind} topic={topic[:40]}")
        bot = getattr(event, "bot", None)
        ctx_type = "group" if event.get_group_id() else "private"
        ctx_id = int(event.get_group_id() or event.get_sender_id())
        import asyncio as _aio
        _aio.get_event_loop().create_task(self._qzone_task(bot, ctx_type, ctx_id, kind, topic))
        await event.send(MessageChain(chain=[Plain(f"收到 点火: {kind} - {topic} 后台创作中(约1分钟) 完成后自动发空间")]))

    async def _qzone_task(self, bot, ctx_type, ctx_id, kind, topic):
        try:
            import sys as _sys
            _dir = os.path.dirname(os.path.abspath(__file__))
            if _dir not in _sys.path:
                _sys.path.insert(0, _dir)
            import qzone_flow
            provider = self.context.get_using_provider()
            ok, report = await qzone_flow.run_flow(bot, provider, ctx_type, ctx_id, kind, topic)
            _audit("发空间完成", f"ok={ok} {report[:60]}")
        except Exception as e:
            _audit("发空间异常", f"{type(e).__name__}: {e}")
            try:
                fn = 'send_group_msg' if ctx_type == 'group' else 'send_private_msg'
                key = 'group_id' if ctx_type == 'group' else 'user_id'
                await ca(bot, fn, {key: int(ctx_id),
                    "message": [{"type": "text", "data": {"text": f"任务异常: {e}"}}]})
            except Exception:
                pass

    # ---------- /总结合并转发 ----------
    @filter.command("总结合并转发")
    async def cmd_sum_fwd(self, event: AstrMessageEvent):
        if not self._is_master(event):
            await self._deny(event); return
        event.stop_event()
        yield event.plain_result("把要总结的合并转发私聊发给bot, 然后说'总结上面的'——bot会自动展开内容(该能力在twin_brain中, 对私聊同样生效)。")
