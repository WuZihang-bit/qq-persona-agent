import asyncio
import json
import os
import random
import re
import sys
import time
from collections import deque, defaultdict

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain, Image, Reply, Record
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.event_message_type import EventMessageType

# ---- 分身协议共享桥（fensheng_plus提供：VOICE语音/LEARN学梗/MOOD/[SKIP]解析）----
try:
    sys.path.insert(0, os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "astrbot_plugin_fensheng_plus")))
    from fensheng_core import outgoing as fs_out
    from fensheng_core import protocol as fs_protocol
except Exception:
    fs_out = None
    fs_protocol = None

EMOJI_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "emojis"))
EMOJI_TAG = re.compile(r"\[\[EMOJI:([^\]]+)\]\]")
QUOTE_NUM = re.compile(r"\[+\s*QUOTE\s*[:：]\s*(\d+)\s*\]+", re.IGNORECASE)
QUOTE_TAG = re.compile(r"\[++\s*QUOTE\s*[:：][^\]]*\]+", re.IGNORECASE)

# 全局发送节奏: 队列令牌制 —— 先到先发, 严格保序, 不会被"短延迟"插队
SEND_GAP_MIN, SEND_GAP_MAX = 2.0, 4.0
_queue = {"seq": 0, "next_turn": 0, "last_send": 0.0, "lock": asyncio.Lock()}


async def _acquire_token():
    """入队领号: 返回本条回复的发送令牌(全局递增)"""
    async with _queue["lock"]:
        _queue["seq"] += 1
        return _queue["seq"]


async def _pace(token: int, session: str = ""):
    """持有令牌才发送: ① 等自己轮到 ② 与上一条消息保持最小间隔 ③ 同会话额外保序"""
    while True:
        async with _queue["lock"]:
            if _queue["next_turn"] + 1 == token:      # 轮到我了
                now = time.time()
                wait = _queue["last_send"] + random.uniform(SEND_GAP_MIN, SEND_GAP_MAX) - now
                _queue["next_turn"] = token
                _queue["last_send"] = max(now, _queue["last_send"] + SEND_GAP_MIN)
                break
        await asyncio.sleep(0.2)                       # 没轮到, 等前面发完
    if wait > 0:
        await asyncio.sleep(wait)


@register("astrbot_plugin_wuzihang_delay", "wuzihang", "拟人化：节流/正在输入/连发/表情包/引用", "1.3.0")
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
        d = random.uniform(0.8, 2.2) + min(len(text) / 40.0, 4.0)
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
        import re as _rv
        seg = _rv.sub(r'\[\x5b\u3010]{1,2}\s*VOICE\s*[:\uff1a][^\]\u3011]*[\]\u3011]', ' ', seg)
        tags = EMOJI_TAG.findall(seg)
        clean = EMOJI_TAG.sub("", seg).strip()
        parts = []
        if clean:
            parts.append(Plain(clean))
        for kw in tags:
            img = self._find_sticker(kw.strip())
            if img:
                parts.append(Image.fromFileSystem(img))
            elif fs_protocol is not None:
                pool = fs_protocol.KAOMOJI.get(kw.strip(), fs_protocol.KAOMOJI["通用"])
                if pool:
                    parts.append(Plain(random.choice(pool)))
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

        # 分身协议: VOICE(→MOSS语音)/LEARN(→联网学梗)/MOOD/[SKIP] 解析
        parsed = fs_out.parse(text) if fs_out else \
            {"text": text, "voices": [], "learns": []}
        text = parsed["text"]
        if parsed["learns"]:
            fs_out.queue_learn_tasks(event, parsed["learns"])
        if not text and not parsed["voices"]:
            return

        segs = self._segments(text)
        # 长单条拆连发：真人是2-3条短气泡，不是一篇作文
        if fs_out:
            expanded = []
            for s in segs:
                expanded.extend(fs_out.split_bubbles(s))
            segs = expanded[:5]
        has_emoji = any(EMOJI_TAG.search(s) for s in segs)
        is_private = not in_group
        token = await _acquire_token()                 # 进入发送队列(保序)
        session = f"{event.get_group_id() or event.get_sender_id()}"

        # 群聊非超短回复默认引用触发消息
        reply_to = None
        if in_group and (qm or len(text) > 10):
            mid = str(getattr(event.message_obj, "message_id", "") or "")
            if mid:
                reply_to = mid

        # 单条无表情无引用: 默认发送, 只做节流+正在输入
        if len(segs) <= 1 and not has_emoji and not reply_to and not parsed["voices"]:
            if is_private:
                try:
                    await event.send_typing()
                except Exception:
                    pass
            await _pace(token, session)
            return

        # 多条/带表情/带引用: 接管发送
        event.stop_event()
        records = []
        if fs_out and parsed["voices"]:
            try:
                records = await fs_out.voice_record_components(parsed["voices"])
            except Exception:
                records = []
        # 有语音时: 只发语音, 不再发同内容的文字(避免"语音+重复文字"双发)
        if records:
            # 语音合成成功: 只发语音条, 不发重复文字
            await event.send(MessageChain(chain=records))
            return
        # 语音合成失败: 回落发文字版(纯语音响应无正文时用语音文本兜底)
        if not segs and parsed["voices"]:
            segs = self._segments(" ".join(parsed["voices"]))
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
                await _pace(token, session)
            else:
                await asyncio.sleep(random.uniform(0.8, 2.2))
            if not parts:
                continue
            try:
                await event.send(MessageChain(chain=parts))
            except Exception:
                break
