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

# ---- 可调参数 ----
BOT_UIN = os.environ.get("TWIN_BOT_UIN", "")     # 小号(bot自己)的QQ号: 用于识别@消息
MASTER_QQ = os.environ.get("TWIN_MASTER_UIN", "")   # 主人大号: 他的消息高优先级接话
MASTER_COOLDOWN = 45       # 主人发言的接话冷却(秒), 防止连刷屏
CHANCE = 0.08              # 普通群消息触发插话意愿的概率
GROUP_COOLDOWN = 480       # 普通插话: 同一群最小间隔(秒)
DAILY_CAP = 40             # 每天插话总上限
SILENT_START = (23, 30)    # 静默时段 23:30 ~ 8:00 (主人叫它除外)
SILENT_END = (8, 0)
WINDOW = 14                # 喂给LLM的最近消息条数
REACTION_DELAY = (2.0, 6.0)

PROMPT_FILE = os.path.join(os.path.dirname(__file__), "group_prompt.txt")
EMOJI_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "emojis"))
EMOJI_TAG = re.compile(r"\[\[EMOJI:([^\]]+)\]\]")
QUOTE_TAG = re.compile(r"\[++\s*QUOTE\s*[:：][^\]]*\]+", re.IGNORECASE)


@register("astrbot_plugin_wuzihang_group", "wuzihang", "群聊围观+主人发言捧场", "1.1.0")
class WuzihangGroupPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.windows = defaultdict(lambda: deque(maxlen=WINDOW))
        self.last_speak = {}
        self.today = time.strftime("%Y-%m-%d")
        self.today_count = 0
        self.last_reply = {}
        self.prompt = ""
        try:
            self.prompt = open(PROMPT_FILE, encoding="utf-8").read()
        except Exception:
            self.prompt = ""

    # ---------- 工具 ----------
    def _silent_now(self) -> bool:
        t = time.localtime()
        hm = (t.tm_hour, t.tm_min)
        return hm >= SILENT_START or hm < SILENT_END

    def _quota_ok(self) -> bool:
        if time.strftime("%Y-%m-%d") != self.today:
            self.today, self.today_count = time.strftime("%Y-%m-%d"), 0
        return self.today_count < DAILY_CAP

    def _roll(self, group_id) -> bool:
        if not self._quota_ok() or self._silent_now():
            return False
        if time.time() - self.last_speak.get(group_id, 0) < GROUP_COOLDOWN:
            return False
        return random.random() < CHANCE

    def _master_fire(self, group_id) -> bool:
        if not self._quota_ok():
            return False
        # 主人发言: 仅受短冷却约束, 静默时段也捧场(主人优先)
        return time.time() - self.last_speak.get(group_id, 0) >= MASTER_COOLDOWN

    def _window_text(self, group_id, mark_master=True) -> str:
        lines = []
        for i, m in enumerate(self.windows[group_id]):
            tag = "[主人]" if (mark_master and m.get("master")) else ""
            lines.append(f"[{i}]{tag} {m['name']}({m['time']}): {m['text']}")
        return "\n".join(lines)

    def _find_sticker(self, kw: str) -> str | None:
        try:
            base = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "emojis"))
            idx_path = os.path.join(base, "index.json")
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
                      str(e.get("emotion", "")) + str(e.get("desc", ""))
                if kw in hay:
                    score = max(score, 2)
                if score > best_score:
                    f = os.path.join(base, str(e.get("file", "")))
                    if os.path.exists(f):
                        best, best_score = f, score
            return best
        except Exception:
            return None

    def _build_chain(self, text: str) -> list:
        """文本里的[[EMOJI:kw]]替换成真表情包; 没匹配到的标记吞掉"""
        parts = []
        tags = EMOJI_TAG.findall(text)
        clean = EMOJI_TAG.sub("", text).strip()
        if clean:
            parts.append(Plain(clean[:100]))
        for kw in tags:
            img = self._find_sticker(kw.strip())
            if img:
                parts.append(Image.fromFileSystem(img))
        return parts

    # ---------- 群消息监听 ----------
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent, *args, **kwargs):
        try:
            gid = event.get_group_id()
            if not gid:
                return
            text = (event.message_str or "").strip()
            if not text or text.startswith(("/", "!", "＃")):
                return
            sender = event.get_sender_name() or str(event.get_sender_id())
            is_master = str(event.get_sender_id()) == MASTER_QQ
            t = time.strftime("%H:%M")
            self.windows[gid].append(
                {"name": sender, "time": t, "text": text[:120], "master": is_master,
                 "id": str(getattr(event.message_obj, "message_id", "") or "")}
            )

            # @自己的消息由主管道处理, 围观插件让路 (否则会双发)
            self_id = str((getattr(event, "get_self_id", lambda: "")() or ""))
            for comp in event.message_obj.message:
                qq = str(getattr(comp, "qq", "") or "")
                if type(comp).__name__ == "At" and qq in (BOT_UIN, self_id):
                    return

            # 同群同内容90秒去重保险
            last = self.last_reply.get(gid)
            if last and time.time() - last[1] < 90:
                return

            if is_master:
                fire = self._master_fire(gid)
            else:
                fire = self._roll(gid)
            if not fire:
                return

            # 反应延迟, 像刷群时偶然看到
            await asyncio.sleep(random.uniform(*REACTION_DELAY))
            if not self.windows[gid]:
                return

            provider = self.context.get_using_provider()
            if provider is None:
                return

            sys_prompt = self.prompt or "你是一个QQ群里的闲聊分身。没话说就输出[SKIP]。"
            extra = ""
            if is_master:
                extra = ("\n\n# 本轮任务：主人发言捧场\n"
                         "标记[主人]的最新一条消息就是你的主人说的话，你要接话捧场：\n"
                         "- 像好兄弟一样接梗/附和/调侃他/帮他补充，可以损他（傲娇），但别抢风头、别复述他的话\n"
                         "- 大部分时候都值得接，拿不准就短句附和（确实/好活/然后呢）\n"
                         "- 输出一句即可，不要输出[SKIP]，除非完全无从接起")
            else:
                extra = "\n\n# 本轮任务：围观插话\n没话说就只输出 [SKIP]。"

            resp = await provider.text_chat(
                prompt="以下是最近的群聊记录，请你决定要不要插一句话：\n"
                       + self._window_text(gid) + extra,
                system_prompt=sys_prompt,
            )
            reply = (getattr(resp, "completion_text", "") or "").strip()
            if not reply or "[SKIP]" in reply:
                return
            self.last_speak[gid] = time.time()
            self.today_count += 1
            self.last_reply[gid] = (reply[:50], time.time())

            # 真引用: [QUOTE:序号] / [[QUOTE:序号]] -> Reply组件
            parts = self._build_chain(QUOTE_TAG.sub("", reply).strip())
            qm = re.search(r"\[+\s*QUOTE\s*[:：]\s*(\d+)\s*\]+", reply, re.IGNORECASE)
            if qm:
                ids = [m.get("id") for m in self.windows[gid]]
                n = int(qm.group(1))
                if 0 <= n < len(ids) and ids[n]:
                    parts = [Reply(id=ids[n])] + parts
            elif len(reply) > 10:
                # 非超短回复默认引用触发消息; "确实""？"这种短句不引用
                tid = str(getattr(event.message_obj, "message_id", "") or "")
                if tid:
                    parts = [Reply(id=tid)] + parts
            if not parts:
                return
            await asyncio.sleep(random.uniform(0.5, 1.5))
            await event.send(MessageChain(chain=parts))
        except Exception:
            pass  # 监听器绝不影响主流程
