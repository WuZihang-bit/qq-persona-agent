# -*- coding: utf-8 -*-
"""聊天生图: 主人私聊说"画 xxx"即生图发回
- 仅 masters.json 里的主人可用
- 复用 twin-master/qzone_flow.py 的三级生图链(随想gpt-image → CogView → 失败)
- 触发词: 画/画一张/画个/帮我画/来张图... (2~60字描述)
- /画图 <描述> 也可
"""
import asyncio
import os
import random
import sys
import time

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain, Image
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.event_message_type import EventMessageType

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng"))
MASTER_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "masters.json"))
DAILY_CAP = 10
COOLDOWN = 20          # 两次生图最小间隔

TRIGGERS = ("帮我画", "画一张", "画一幅", "画个", "画一", "来张图", "来一张图", "画:", "画：")


def _masters():
    try:
        return [str(x) for x in json_load(MASTER_DIR).get("masters", []) if str(x).isdigit()]
    except Exception:
        m = os.environ.get("MASTER_QQ", "")
        return [m] if m.isdigit() else []


def json_load(fp):
    import json
    return json.load(open(fp, encoding="utf-8"))


def _pick_trigger(text):
    t = text.strip()
    for tr in TRIGGERS:
        if t.startswith(tr):
            rest = t[len(tr):].strip(" ：:，,。")
            if 2 <= len(rest) <= 60:
                return rest
    return None


@register("twin_draw", "wuzihang", "聊天生图(仅主人): 画xxx / /画图 描述", "1.0.0")
class TwinDraw(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._day, self._n = time.strftime("%Y-%m-%d"), 0
        self._last = 0.0
        self._busy = False

    def _cap_ok(self):
        if time.strftime("%Y-%m-%d") != self._day:
            self._day, self._n = time.strftime("%Y-%m-%d"), 0
        return self._n < DAILY_CAP

    async def _gen_and_send(self, event: AstrMessageEvent, desc: str):
        self._busy = True
        try:
            await event.send(MessageChain(chain=[Plain("画着呢，等我几秒..")]))
            t0 = time.time()
            path = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._draw(desc))
            if not path:
                await event.send(MessageChain(chain=[Plain(
                    "画不出来了.. 生图服务今天都不在线(已倒闭三次)，明天再试")]))
                return
            self._n += 1
            self._last = time.time()
            await event.send(MessageChain(chain=[
                Plain(f"{desc}，画好了({time.time() - t0:.0f}秒)"),
                Image.fromFileSystem(path)]))
        except Exception as e:
            try:
                await event.send(MessageChain(chain=[Plain(f"画挂了: {str(e)[:80]}")]))
            except Exception:
                pass
        finally:
            self._busy = False

    def _draw(self, desc: str):
        _dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "twin-master"))
        if _dir not in sys.path:
            sys.path.insert(0, _dir)
        import qzone_flow
        return qzone_flow.gen_image_sync(desc)

    # ---------- 自然语言触发 ----------
    @filter.event_message_type(EventMessageType.PRIVATE_MESSAGE)
    async def on_private(self, event: AstrMessageEvent, *args, **kwargs):
        try:
            if str(event.get_sender_id()) not in _masters():
                return
            if self._busy:
                return
            desc = _pick_trigger(event.message_str or "")
            if not desc:
                return
            if not self._cap_ok():
                event.stop_event()
                await event.send(MessageChain(chain=[Plain("今天画够10张了，手酸，明天再画")]))
                return
            if time.time() - self._last < COOLDOWN:
                return
            event.stop_event()
            asyncio.create_task(self._gen_and_send(event, desc))
        except Exception:
            pass

    # ---------- LLM工具: 自然语言画图(任意说法) ----------
    @filter.llm_tool(name="draw_image")
    async def draw_image(self, event: AstrMessageEvent, desc: str):
        """画一张图并发给用户。当用户想让你画画/生成图片/出图(不论怎么说)时使用。只有主人能让你画。

        Args:
            desc(string): 画面描述, 生成前可自行补充细节(场景/光影/风格)
        """
        if str(event.get_sender_id()) not in _masters():
            return "只有主人能让我画画, 婉拒"
        if not self._cap_ok() or self._busy:
            return "手头正忙或今天额度用完了, 告诉用户稍后再画"
        await self._gen_and_send(event, desc.strip()[:60])
        return "图已画好并发送"

    # ---------- /画图 ----------
    @filter.command("画图")
    async def cmd_draw(self, event: AstrMessageEvent, *, desc: str = ""):
        event.stop_event()
        if str(event.get_sender_id()) not in _masters():
            await event.send(MessageChain(chain=[Plain("只有主人能让我画画")]))
            return
        desc = desc.strip()
        if not desc:
            await event.send(MessageChain(chain=[Plain("/画图 描述 — 例如 /画图 夕阳下的图书馆")]))
            return
        if not self._cap_ok() or self._busy:
            await event.send(MessageChain(chain=[Plain("手头正忙或今天额度用完了")]))
            return
        await self._gen_and_send(event, desc)
