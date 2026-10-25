# -*- coding: utf-8 -*-
"""图片记忆: bot记住见过的图, 之后能"想起"并重发
- 私聊图全记, 群聊图25%采样, 每天最多40张(VLM走免费的glm-4v-flash)
- /想起 <关键词>  — 搜记忆里的图(按描述匹配)
- /发图 <序号>    — 重发那张图
- 对话时自动注入该联系人最近发过的图(让TA能自然提起)
"""
import asyncio
import base64
import hashlib
import json
import os
import random
import re
import time
from collections import deque

import requests

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.event_message_type import EventMessageType
from astrbot.core.provider.entities import ProviderRequest

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng"))
IMG_DIR = os.path.join(DATA_DIR, "img_mem")
IDX_FP = os.path.join(DATA_DIR, "img_memory.json")
MEME_FP = os.path.join(DATA_DIR, "meme_knowledge.json")
DAILY_CAP = 40
GROUP_SAMPLE = 0.25
KEEP_FILES = 200          # 图片文件LRU上限

VLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


def _char_hints() -> str:
    """知识库里的形象词条, 给VLM当识别提示"""
    try:
        d = json.load(open(MEME_FP, encoding="utf-8"))
        chars = [f"{k}({v[1][:18]})" for k, v in d.items() if v[0] == "形象"][:20]
        return "; ".join(chars)
    except Exception:
        return ""


def _tavily_key():
    try:
        def fk(o):
            if isinstance(o, str) and re.match(r"^tvly-[A-Za-z0-9_\-]{10,}$", o):
                return o
            if isinstance(o, dict):
                for v in o.values():
                    r = fk(v)
                    if r:
                        return r
            if isinstance(o, list):
                for v in o:
                    r = fk(v)
                    if r:
                        return r
        return fk(json.load(open(os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "..", "cmd_config.json")), encoding="utf-8"))) or ""
    except Exception:
        return ""


def _load():
    try:
        return json.load(open(IDX_FP, encoding="utf-8"))
    except Exception:
        return []


def _save(idx):
    json.dump(idx[-500:], open(IDX_FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


@register("twin_imgmemory", "wuzihang", "图片记忆: 记住见过的图, /想起 /发图", "1.0.0")
class TwinImgMemory(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        os.makedirs(IMG_DIR, exist_ok=True)
        self._today, self._n = time.strftime("%Y-%m-%d"), 0
        self._busy = False
        self._pending = deque(maxlen=8)

    # ---------- key/密钥 ----------
    def _key(self, event):
        gid = event.get_group_id()
        return f"g{gid}" if gid else f"p{event.get_sender_id()}"

    def _glm_key(self):
        try:
            def find_key(o):
                if isinstance(o, str) and re.match(r"^[0-9a-f]{32}\.[A-Za-z0-9]{8,}$", o):
                    return o
                if isinstance(o, dict):
                    for v in o.values():
                        r = find_key(v)
                        if r:
                            return r
                if isinstance(o, list):
                    for v in o:
                        r = find_key(v)
                        if r:
                            return r
            return find_key(json.load(open(
                os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "cmd_config.json")),
                encoding="utf-8"))) or ""
        except Exception:
            return ""

    # ---------- 收图 ----------
    @filter.event_message_type(EventMessageType.ALL)
    async def on_msg(self, event: AstrMessageEvent, *args, **kwargs):
        try:
            if getattr(event, "_imgmem_done", False):
                return
            urls = [getattr(c, "url", "") for c in event.message_obj.message
                    if type(c).__name__ == "Image" and getattr(c, "url", "")]
            if not urls:
                return
            event._imgmem_done = True
            gid = event.get_group_id()
            if gid and random.random() > GROUP_SAMPLE:
                return
            if time.strftime("%Y-%m-%d") != self._today:
                self._today, self._n = time.strftime("%Y-%m-%d"), 0
            if self._n >= DAILY_CAP or self._busy:
                return
            sender = str(event.get_sender_id())
            chat_key = self._key(event)
            self._pending.append((urls[0], sender, chat_key))
            asyncio.create_task(self._drain())
        except Exception:
            pass

    async def _drain(self):
        if self._busy:
            return
        self._busy = True
        try:
            while self._pending:
                url, sender, chat_key = self._pending.popleft()
                if self._n >= DAILY_CAP:
                    break
                try:
                    await self._mem_one(url, sender, chat_key)
                except Exception:
                    pass
                await asyncio.sleep(1)
        finally:
            self._busy = False

    async def _mem_one(self, url, sender, chat_key):
        def _dl():
            return requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}).content
        img = await asyncio.get_event_loop().run_in_executor(None, _dl)
        if not img or len(img) < 3000:
            return
        md5 = hashlib.md5(img).hexdigest()
        idx = _load()
        if any(e.get("md5") == md5 for e in idx[-100:]):
            return                      # 重复图
        key = self._glm_key()
        if not key:
            return
        cap = await asyncio.get_event_loop().run_in_executor(None, lambda: self._vlm(key, img))
        if not cap:
            return
        fp = os.path.join(IMG_DIR, md5 + ".jpg")
        with open(fp, "wb") as f:
            f.write(img)
        idx.append({"md5": md5, "cap": cap[:80], "who": sender, "chat": chat_key,
                    "t": time.strftime("%m-%d %H:%M"), "ts": int(time.time())})
        _save(idx)
        self._lru()
        self._n += 1
        print(f"[imgmem] 记住了: {cap[:40]} ({chat_key})")

    def _vlm(self, key, img, extra_hint=True):
        try:
            hint = ""
            if extra_hint:
                ch = _char_hints()
                if ch:
                    hint = f" 常见形象参考: {ch}."
            b64 = base64.b64encode(img).decode()
            r = requests.post(VLM_URL, headers={"Authorization": "Bearer " + key}, timeout=40,
                              json={"model": "glm-4v-flash",
                                    "messages": [{"role": "user", "content": [
                                        {"type": "image_url", "image_url": {
                                            "url": "data:image/jpeg;base64," + b64}},
                                        {"type": "text", "text": "40字内描述这张图(内容+亮点), 若有知名形象/IP角色要说出名字。" + hint}]}]})
            return (r.json()["choices"][0]["message"]["content"] or "").strip()
        except Exception:
            return ""

    def _lru(self):
        try:
            files = [(os.path.getmtime(os.path.join(IMG_DIR, f)), f)
                     for f in os.listdir(IMG_DIR) if f.endswith(".jpg")]
            files.sort()
            for _, f in files[:-KEEP_FILES]:
                os.remove(os.path.join(IMG_DIR, f))
        except Exception:
            pass

    # ---------- 注入最近图 ----------
    @filter.on_llm_request()
    async def inject(self, event: AstrMessageEvent, req: ProviderRequest):
        try:
            ck = self._key(event)
            idx = [e for e in _load() if e.get("chat") == ck][-4:]
            if idx:
                lines = [f"- {e['cap']}({e['t']})" for e in idx]
                req.system_prompt = (req.system_prompt or "") + \
                    "\n[TA最近发过的图(你能想起来)]\n" + "\n".join(lines)
        except Exception:
            pass

    # ---------- /这是谁 (回复一张图, 联网识别形象) ----------
    @filter.command("这是谁")
    async def cmd_whois(self, event: AstrMessageEvent):
        """/这是谁 — 回复一张图片, bot识别图里的形象; 本地不认识就联网搜索"""
        event.stop_event()
        bot = getattr(event, "bot", None)
        url = None
        for comp in event.message_obj.message:
            if type(comp).__name__ == "Image" and getattr(comp, "url", ""):
                url = comp.url
                break
        if url is None:
            # 取被回复消息里的图
            for comp in event.message_obj.message:
                if type(comp).__name__ == "Reply":
                    try:
                        src = await bot.call_action("get_msg", {"message_id": int(getattr(comp, "id", 0))})
                        for seg in (src.get("message") or []):
                            u = seg.get("data", {}).get("url") if isinstance(seg, dict) else None
                            if u:
                                url = u
                                break
                    except Exception:
                        pass
        if url is None:
            await event.send(MessageChain(chain=[Plain("/这是谁 — 直接带图发, 或回复一张图片")]))
            return
        await event.send(MessageChain(chain=[Plain("让我看看这是谁..")]))
        key = self._glm_key()
        if not key:
            await event.send(MessageChain(chain=[Plain("VLM没配置, 识别不了")]))
            return

        def _dl():
            return requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}).content
        try:
            img = await asyncio.get_event_loop().run_in_executor(None, _dl)
        except Exception:
            await event.send(MessageChain(chain=[Plain("图下载失败")]))
            return
        cap = await asyncio.get_event_loop().run_in_executor(None, lambda: self._vlm(key, img))
        if not cap:
            await event.send(MessageChain(chain=[Plain("看不清这张图")]))
            return
        # 本地词典先查
        try:
            memes = json.load(open(MEME_FP, encoding="utf-8"))
            local = [f"{k}: {v[1]}" for k, v in memes.items() if v[0] == "形象" and k in cap]
            if local:
                await event.send(MessageChain(chain=[Plain(
                    "认识! " + "; ".join(local[:3]) + f"\n(看图: {cap})")]))
                return
        except Exception:
            pass
        # 联网搜
        tav = _tavily_key()
        if tav:
            try:
                def _q():
                    return requests.post("https://api.tavily.com/search", timeout=25, json={
                        "api_key": tav, "query": f"{cap[:60]} 是什么 角色 形象 表情包",
                        "max_results": 5}).json()
                r = await asyncio.get_event_loop().run_in_executor(None, _q)
                snips = "\n".join(x.get("content", "")[:150] for x in r.get("results", []))
                provider = self.context.get_using_provider()
                if snips and provider is not None:
                    resp = await provider.text_chat(
                        prompt=f"图里是: {cap}\n搜索结果:\n{snips[:2000]}\n"
                               "判断图里的形象/IP叫什么、是什么。50字内。没把握就说'没查到,我看像个XX'。",
                        system_prompt="你是形象识别员, 直接给结论。")
                    out = (getattr(resp, "completion_text", "") or "").strip()
                    if out:
                        if "没查到" not in out:
                            # 学到新形象: 从回答里抠名字入词典
                            try:
                                nm = re.match(r"^[^,，。:：]{2,8}", out).group().strip()
                                memes = json.load(open(MEME_FP, encoding="utf-8"))
                                memes[nm] = ["形象", out[:90]]
                                json.dump(memes, open(MEME_FP, "w", encoding="utf-8"),
                                          ensure_ascii=False, indent=1)
                            except Exception:
                                pass
                        await event.send(MessageChain(chain=[Plain(
                            f"查到了: {out}\n(看图: {cap})")]))
                        return
            except Exception:
                pass
        await event.send(MessageChain(chain=[Plain(f"没搜到确切信息, 我自己看: {cap}")]))

    # ---------- /想起 ----------
    @filter.command("想起")
    async def cmd_recall(self, event: AstrMessageEvent, *, kw: str = ""):
        event.stop_event()
        kw = kw.strip()
        if not kw:
            await event.send(MessageChain(chain=[Plain("/想起 关键词 — 搜我记住的图, /发图 序号 重发")]))
            return
        idx = _load()
        hits = [(i, e) for i, e in enumerate(idx) if kw in e.get("cap", "")][-5:]
        if not hits:
            await event.send(MessageChain(chain=[Plain(f"没印象有{kw}的图，我记住的图会越来越多的")]))
            return
        self._last_hits = {str(n + 1): i for n, (i, _) in enumerate(hits)}
        txt = "\n".join(f"{n+1}. {e['cap']}({e['t']})" for n, (_, e) in enumerate(hits))
        await event.send(MessageChain(chain=[Plain(f"想起来几张:\n{txt}\n/发图 序号 让我重发")]))

    # ---------- /发图 ----------
    @filter.command("发图")
    async def cmd_send(self, event: AstrMessageEvent, n: str = "1"):
        event.stop_event()
        i = getattr(self, "_last_hits", {}).get(n)
        idx = _load()
        if i is None or i >= len(idx):
            await event.send(MessageChain(chain=[Plain("先 /想起 关键词, 再 /发图 序号")]))
            return
        e = idx[i]
        fp = os.path.join(IMG_DIR, e["md5"] + ".jpg")
        if not os.path.exists(fp):
            await event.send(MessageChain(chain=[Plain("那张图我缓存清掉了，只剩印象: " + e["cap"])]))
            return
        await event.send(MessageChain(chain=[Plain(e["cap"]), Image.fromFileSystem(fp)]))
