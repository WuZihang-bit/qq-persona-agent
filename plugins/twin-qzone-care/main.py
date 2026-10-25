# -*- coding: utf-8 -*-
"""空间评论区自动回复: 别人评论bot的说说, bot按人格自动回评
- 每15分钟扫一次自己的说说(msglist_v6), 发现未回评的评论(7天内)→LLM生成回复→re_feeds回评
- 防风控: 每天≤5条, 静默时段(23:30-8:00)不回, 每轮≤3条
- 状态: data/fensheng/qzone_replied.json
"""
import asyncio
import json
import os
import random
import re
import time

import requests

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng"))
STATE_FP = os.path.join(DATA_DIR, "qzone_replied.json")
BOT_UIN = os.environ.get("BOT_UIN", "")

LIST_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
COMMENT_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_re_feeds"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

REPLY_SYS = ("你是电子科大女生Yuan, 有人在你的QQ空间说说下评论了, 你要回一条: "
             "口语、短(15字内)、接地气, 可以开玩笑可以谢谢, 像真人回评论。只输出回复内容。")

DAILY_CAP = 5
SCAN_GAP = 900          # 15分钟
MAX_AGE_DAYS = 7


def _gtk(p_skey: str) -> str:
    h = 5381
    for ch in p_skey:
        h += (h << 5) + ord(ch)
    return str(h & 0x7FFFFFFF)


@register("twin_qzone_care", "wuzihang", "空间评论自动回复(按人格回评好友评论)", "1.0.0")
class TwinQzoneCare(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._bot = None
        asyncio.create_task(self._loop())

    @filter.on_llm_request()
    async def _grab_bot(self, event: AstrMessageEvent, req: ProviderRequest):
        self._bot = getattr(event, "bot", self._bot)

    def _state(self):
        try:
            return json.load(open(STATE_FP, encoding="utf-8"))
        except Exception:
            return {"replied": [], "day": "", "count": 0}

    def _save_state(self, s):
        json.dump(s, open(STATE_FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    async def _loop(self):
        await asyncio.sleep(40)
        while True:
            try:
                await self._scan_once()
            except Exception as e:
                print(f"[qzone_care] scan err: {e}")
            await asyncio.sleep(SCAN_GAP)

    async def _scan_once(self):
        if self._bot is None:
            return
        lt = time.localtime()
        if (lt.tm_hour, lt.tm_min) >= (23, 30) or lt.tm_hour < 8:
            return
        st = self._state()
        today = time.strftime("%Y-%m-%d")
        if st.get("day") != today:
            st = {"replied": st.get("replied", [])[-500:], "day": today, "count": 0}
        if st["count"] >= DAILY_CAP:
            return
        # 拿cookie
        r = await self._bot.call_action("get_cookies", domain="user.qzone.qq.com")
        cookies_str = (r or {}).get("cookies", "") if isinstance(r, dict) else str(r or "")
        ck = {}
        for part in cookies_str.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                ck[k.strip()] = v.strip()
        p_skey = ck.get("p_skey") or ck.get("skey") or ""
        uin_raw = ck.get("uin", "").lstrip("o") or BOT_UIN
        if not p_skey:
            return
        gtk = _gtk(p_skey)
        headers = {"User-Agent": UA, "Referer": f"https://user.qzone.qq.com/{uin_raw}",
                   "Cookie": cookies_str}

        def _list():
            return requests.get(LIST_URL, headers=headers, timeout=20, params={
                "uin": uin_raw, "ftype": "0", "sort": "0", "pos": "0",
                "num": "5", "replynum": "100", "g_tk": gtk,
                "code_version": "1", "format": "json", "need_private_comment": "1"}).json()

        data = await asyncio.get_event_loop().run_in_executor(None, _list)
        msgs = (data.get("data") or {}).get("msglist") or []
        replied_ids = set(st["replied"])
        todo = []
        for m in msgs:
            tid = str(m.get("tid") or "")
            if not tid:
                continue
            for c in (m.get("commentlist") or []):
                cid = str(c.get("tid") or "")
                cuin = str(c.get("uin") or "")
                age = time.time() - int(c.get("createTime2") or c.get("create_time") or time.time())
                if not cid or cuin in (BOT_UIN, uin_raw) or cid in replied_ids:
                    continue
                if age > MAX_AGE_DAYS * 86400 or age < 60:      # 太老/刚发1分钟内的下轮再说
                    continue
                todo.append((tid, cid, c))
        todo = todo[:3]
        for tid, cid, c in todo:
            if st["count"] >= DAILY_CAP:
                break
            name = c.get("name") or c.get("nickname") or "网友"
            content = re.sub(r"\[em\]e\d+\[/em\]", "", str(c.get("content") or ""))[:100]
            post_txt = re.sub(r"\[em\]e\d+\[/em\]", "", str(
                (msgs and next((m.get("content") for m in msgs if str(m.get("tid")) == tid), "")) or ""))[:60]
            reply = await self._gen_reply(name, content, post_txt)
            if not reply:
                continue
            mention = "@{uin:%s,nick:%s,auto:1} " % (c.get("uin"), name)
            payload = {
                "hostUin": uin_raw, "topicId": f"{uin_raw}_{tid}",
                "content": mention + reply, "format": "json",
                "qzreferrer": f"https://user.qzone.qq.com/{uin_raw}/main",
                "commentId": cid, "replyUin": c.get("uin"), "commentUin": c.get("uin")}

            def _post(p=payload):
                return requests.post(COMMENT_URL, headers=headers, timeout=20,
                                     params={"g_tk": gtk}, data=p).text[:200]
            try:
                out = await asyncio.get_event_loop().run_in_executor(None, _post)
                print(f"[qzone_care] 回评 {name}: {reply} -> {out[:80]}")
            except Exception as e:
                print(f"[qzone_care] 回评失败: {e}")
                continue
            st["replied"].append(cid)
            st["count"] += 1
            self._save_state(st)
            await asyncio.sleep(random.uniform(8, 20))

    async def _gen_reply(self, name, content, post_txt):
        try:
            provider = self.context.get_using_provider()
            if provider is None:
                return None
            r = await provider.text_chat(
                prompt=f"你的说说内容: {post_txt}\n{name} 评论了: {content}\n请回复这条评论:",
                system_prompt=REPLY_SYS)
            out = (getattr(r, "completion_text", "") or "").strip().strip('"「」')
            return out[:60] if out else None
        except Exception:
            return None
