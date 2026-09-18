import asyncio
import json
import os
import random
import re
import time
from collections import deque, defaultdict

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain, Image, Reply
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.event_message_type import EventMessageType

EMOJI_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "emojis"))
EMOJI_TAG = re.compile(r"\[\[EMOJI:([^\]]+)\]\]")
QUOTE_NUM = re.compile(r"\[+\s*QUOTE\s*[:：]\s*(\d+)\s*\]+", re.IGNORECASE)
QUOTE_TAG = re.compile(r"\[++\s*QUOTE\s*[:：][^\]]*\]+", re.IGNORECASE)

# 全局发送节奏: 所有回复共用, 两条消息最小间隔
SEND_GAP_MIN, SEND_GAP_MAX = 3.5, 7.0
_last_send = [0.0]


async def _pace():
    now = time.time()
    wait = _last_send[0] + random.uniform(SEND_GAP_MIN, SEND_GAP_MAX) - now
    if wait > 0:
        await asyncio.sleep(wait)
    _last_send[0] = time.time()


@register("persona_humanizer", "your-name", "拟人化：节流/正在输入/连发/表情包/引用", "1.3.0")
class WuzihangDelayPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.win_ids = defaultdict(lambda: deque(maxlen=20))

    # ---------- 群消息ID记录(供真引用) ----------
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_record(self, event: AstrMessageEvent, *args, **kwargs):
        try:
            gid = event.get_group_id()
            if gid:
                self.win_ids[gid].append(str(getattr(event.message_obj, "message_id", "") or ""))
        except Exception:
            pass

    # ---------- 拟人延迟 ----------
    def _delay_for(self, text: str) -> float:
        hour = time.localtime().tm_hour
        d = random.uniform(1.5, 4.0) + min(len(text) / 40.0, 5.0)
        if hour >= 23 or hour <= 7:
            return min(d * 1.7, 20.0)
        return min(d, 12.0)

    # ---------- 表情包检索 ----------
    def _find_sticker(self, kw: str) -> str | None:
        try:
            idx_path = os.path.join(EMOJI_DIR, "index.json")
            if not os.path.exists(idx_path):
                return None
            idx = json.load(open(idx_path, encoding="utf-8"))
            best, best_score = None, 0
            for e in idx:
                score = 0
                for k in e.get("keywords", []) or []:
                    k = str(k)
                    if kw in k or k in kw:
                        score = max(score, 3)
                hay = str(e.get("scene", "")) + str(e.get("note", "")) + \
                      str(e.get("emotion", "")) + str(e.get("desc", "")) + str(e.get("text", ""))
                if kw in hay:
                    score = max(score, 2)
                if score > best_score:
                    f = os.path.join(EMOJI_DIR, str(e.get("file", "")))
                    if os.path.exists(f):
                        best, best_score = f, score
            return best
        except Exception:
            return None

    def _build_chain(self, seg: str) -> list:
        tags = EMOJI_TAG.findall(seg)
        clean = EMOJI_TAG.sub("", seg).strip()
        parts = []
        if clean:
            parts.append(Plain(clean))
        for kw in tags:
            img = self._find_sticker(kw.strip())
            if img:
                parts.append(Image.fromFileSystem(img))
        return parts

    def _segments(self, text: str) -> list:
        # 只按LLM主动的换行分段(=它想连发), 不按长度强拆
        return [s.strip() for s in text.replace("\r\n", "\n").split("\n") if s.strip()][:5]

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        try:
            text = (getattr(resp, "completion_text", "") or "").strip()
        except Exception:
            text = ""

        # 引用: 群里有语义(引用触发消息), 私聊无意义直接剥
        in_group = bool(event.get_group_id())
        qm = QUOTE_NUM.search(text)
        text = QUOTE_TAG.sub("", text).strip()
        if not text:
            return

        segs = self._segments(text)
        has_emoji = any(EMOJI_TAG.search(s) for s in segs)
        is_private = not in_group

        # 群聊非超短回复默认引用触发消息
        reply_to = None
        if in_group and (qm or len(text) > 10):
            mid = str(getattr(event.message_obj, "message_id", "") or "")
            if mid:
                reply_to = mid

        # 单条无表情无引用: 默认发送, 只做节流+正在输入
        if len(segs) <= 1 and not has_emoji and not reply_to:
            if is_private:
                try:
                    await event.send_typing()
                except Exception:
                    pass
            await _pace()
            return

        # 多条/带表情/带引用: 接管发送
        event.stop_event()
        for i, seg in enumerate(segs):
            parts = self._build_chain(seg)
            if reply_to and i == 0:
                parts = [Reply(id=reply_to)] + parts
            if is_private:
                try:
                    await event.send_typing()
                except Exception:
                    pass
            if i == 0:
                await _pace()
            else:
                await asyncio.sleep(random.uniform(0.8, 2.2))
            if not parts:
                continue
            try:
                await event.send(MessageChain(chain=parts))
            except Exception:
                break
