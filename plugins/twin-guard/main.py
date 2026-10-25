# -*- coding: utf-8 -*-
"""好友感知: 每5分钟轮询好友列表, 发现新好友 → 通知主人 + 按人格主动打招呼
(好友请求本身OneBot拉不到, 这里感知的是"已通过的新好友", 打招呼让人格闭环)
"""
import asyncio
import json
import os
import random
import time

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng"))
FR_FP = os.path.join(DATA_DIR, "known_friends.json")

GREET_TEMPL = ["加了不说话？那我来，在干嘛", "你好呀，我是Yuan，你是？",
               "通过了通过了，有事说事没事唠嗑", "新朋友？说吧，哪来的"]
NOTIFY_MASTERS = True


def _masters():
    try:
        fp = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "masters.json"))
        return [str(x) for x in json.load(open(fp, encoding="utf-8")).get("masters", [])]
    except Exception:
        return [os.environ.get("MASTER_QQ", "")]


@register("twin_guard", "wuzihang", "好友感知: 新好友通知主人+主动打招呼", "1.0.0")
class TwinGuard(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._bot = None
        self._known = set()
        try:
            self._known = set(json.load(open(FR_FP, encoding="utf-8")))
        except Exception:
            pass
        self._boot = True
        asyncio.create_task(self._loop())

    @filter.on_llm_request()
    async def _grab(self, event: AstrMessageEvent, req: ProviderRequest):
        self._bot = getattr(event, "bot", self._bot)

    def _save(self):
        json.dump(sorted(self._known)[-500:], open(FR_FP, "w", encoding="utf-8"))

    async def _loop(self):
        await asyncio.sleep(60)                 # 等登录稳定
        while True:
            try:
                await self._poll()
            except Exception:
                pass
            await asyncio.sleep(300)

    async def _poll(self):
        if self._bot is None:
            return
        try:
            fl = await self._bot.call_action("get_friend_list")
        except Exception:
            return
        if not isinstance(fl, list):
            return
        now_ids = {str(f.get("user_id")) for f in fl if f.get("user_id")}
        name_of = {str(f.get("user_id")): (f.get("nickname") or f.get("remark") or f.get("user_id"))
                   for f in fl if f.get("user_id")}
        if self._boot:
            # 首轮: 建基线, 不打招呼
            self._known = now_ids
            self._save()
            self._boot = False
            print(f"[guard] 好友基线: {len(now_ids)}人")
            return
        fresh = now_ids - self._known
        gone = self._known - now_ids
        for qq in fresh:
            name = name_of.get(qq, qq)
            print(f"[guard] 新好友: {name}({qq})")
            # 通知主人
            if NOTIFY_MASTERS:
                for m in _masters():
                    try:
                        await self._bot.call_action("send_private_msg", {
                            "user_id": int(m),
                            "message": [{"type": "text", "data": {
                                "text": f"新好友: {name}({qq}) 加了我, 我去打个招呼"}}]})
                    except Exception:
                        pass
            # 随机延迟后主动打招呼
            await asyncio.sleep(random.uniform(40, 180))
            msg = random.choice(GREET_TEMPL)
            try:
                provider = self.context.get_using_provider()
                if provider is not None:
                    r = await provider.text_chat(
                        prompt="你刚通过了一个新好友的好友请求, 对方还没说话。"
                               "你主动发第一条消息破冰: 口语、随意、像大学女生加完好友的随手一招呼(15字内)。只输出消息。",
                        system_prompt="你是电子科大女生Yuan, 只输出一条QQ消息。")
                    out = (getattr(r, "completion_text", "") or "").strip().strip('"「」')
                    if out and len(out) <= 40:
                        msg = out
            except Exception:
                pass
            try:
                await self._bot.call_action("send_private_msg", {
                    "user_id": int(qq),
                    "message": [{"type": "text", "data": {"text": msg}}]})
                print(f"[guard] 已打招呼 -> {name}: {msg}")
            except Exception:
                pass
        if fresh or gone:
            self._known = now_ids
            self._save()
