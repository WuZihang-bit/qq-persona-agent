import asyncio
import json
import os
import re
import time
from datetime import datetime

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import ProviderRequest, LLMResponse

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng"))
MEM_DIR = os.path.join(DATA_DIR, "memory")
EXTRACT_MIN_USER_LEN = 8      # 用户消息少于这个字数不做抽取
FACTS_KEEP = 50               # 每个联系人最多记忆条数

EXTRACT_PROMPT = '''从下面这轮对话中提取值得长期记住的信息。只输出JSON：
{"facts": ["事实1", "事实2"], "followup": {"topic": "主题", "due": "YYYY-MM-DD"} 或 null}
规则：
- facts 只记稳定信息: 对方的身份/偏好/重要事件/计划/人际关系(最多3条, 没有就空数组)
- followup: 对方提到未来某天有结果的事(考试/面试/比赛), 才登记; 没有则null
- 不要记寒暄、玩笑、一次性话题'''

FETCH_SYSTEM = "你是信息抽取器, 只输出JSON, 不要解释。"


@register("twin_memory", "your-name", "长期记忆 + 主动引擎(提醒/跟进)", "1.0.0")
class TwinMemory(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        os.makedirs(MEM_DIR, exist_ok=True)
        self._bot = None
        self._extract_pending = set()
        asyncio.create_task(self._proactive_loop())

    # ---------- 存储 ----------
    def _key(self, event: AstrMessageEvent) -> str:
        gid = event.get_group_id()
        return f"g{gid}" if gid else f"p{event.get_sender_id()}"

    def _mem_file(self, key: str) -> str:
        return os.path.join(MEM_DIR, f"{key}.json")

    def _load(self, fp: str, default):
        try:
            return json.load(open(fp, encoding="utf-8"))
        except Exception:
            return default

    # ---------- 记忆注入(每次LLM请求前) ----------
    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req: ProviderRequest):
        try:
            self._bot = getattr(event, "bot", self._bot)
            d = self._load(self._mem_file(self._key(event)), {"facts": []})
            facts = [f.get("text", "") for f in d.get("facts", [])][-10:]
            if facts:
                req.system_prompt = (req.system_prompt or "") + \
                    "\n[你记得的关于对方的长期记忆]\n" + "\n".join(f"- {x}" for x in facts)
        except Exception:
            pass

    # ---------- 记忆抽取(每次LLM回复后, 异步不阻塞) ----------
    @filter.on_llm_response()
    async def extract_memory(self, event: AstrMessageEvent, resp: LLMResponse):
        try:
            user_text = (event.message_str or "").strip()
            reply = (getattr(resp, "completion_text", "") or "").strip()
            if len(user_text) < EXTRACT_MIN_USER_LEN or not reply:
                return
            key = self._key(event)
            if key in self._extract_pending:      # 并发去重
                return
            self._extract_pending.add(key)
            asyncio.create_task(self._do_extract(event, key, user_text, reply))
        except Exception:
            pass

    async def _do_extract(self, event, key, user_text, reply):
        try:
            await asyncio.sleep(2)                # 让主回复先走
            provider = self.context.get_using_provider()
            if provider is None:
                return
            r = await provider.text_chat(
                prompt=f"用户: {user_text[:400]}\n分身: {reply[:400]}",
                system_prompt=EXTRACT_PROMPT)
            raw = (getattr(r, "completion_text", "") or "").strip()
            m = re.search(r'\{.*\}', raw, re.S)
            if not m:
                return
            d = json.loads(m.group())
            fp = self._mem_file(key)
            data = self._load(fp, {"facts": []})
            new = [f[:150] for f in d.get("facts", []) if f][:3]
            for f in new:
                if f not in [x.get("text") for x in data["facts"]]:
                    data["facts"].append({"text": f, "time": datetime.now().isoformat(timespec="seconds")})
            data["facts"] = data["facts"][-FACTS_KEEP:]
            changed = bool(new)
            fu = d.get("followup")
            if isinstance(fu, dict) and fu.get("topic") and fu.get("due"):
                fp2 = os.path.join(DATA_DIR, "followups.json")
                lst = self._load(fp2, [])
                cid = int(event.get_group_id() or event.get_sender_id())
                if not any(x.get("topic") == fu["topic"] and not x.get("fired") for x in lst):
                    lst.append({"topic": str(fu["topic"])[:60], "due": str(fu.get("due", ""))[:10],
                                "chat_type": "group" if event.get_group_id() else "private",
                                "chat_id": cid, "fired": False})
                    changed = True
                json.dump(lst, open(fp2, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            if changed:
                json.dump(data, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass
        finally:
            self._extract_pending.discard(key)

    # ---------- 主动引擎: 提醒 + 到期跟进 ----------
    async def _proactive_loop(self):
        await asyncio.sleep(15)                   # 等框架起完
        while True:
            try:
                await self._fire_due()
            except Exception:
                pass
            await asyncio.sleep(30)

    def _re(self):
        import re
        return re

    async def _fire_due(self):
        if self._bot is None:
            return
        now = datetime.now().isoformat(timespec="seconds")
        today = datetime.now().strftime("%Y-%m-%d")

        # 提醒
        rfp = os.path.join(DATA_DIR, "reminders.json")
        rems = self._load(rfp, [])
        due = [r for r in rems if not r.get("done") and r.get("time", "9999") <= now]
        if due:
            for r in due:
                r["done"] = True
                try:
                    fn = "send_group_msg" if r["chat_type"] == "group" else "send_private_msg"
                    tid = int(r["chat_id"])
                    await self._bot.call_action(fn, {"group_id" if r["chat_type"] == "group" else "user_id": tid,
                                                     "message": [{"type": "text", "data": {"text": f"提醒: {r['content']}"}}]})
                except Exception:
                    r["done"] = False
            json.dump(rems, open(rfp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

        # 到期跟进
        ffp = os.path.join(DATA_DIR, "followups.json")
        fups = self._load(ffp, [])
        fired_any = False
        for f in fups:
            if not f.get("fired") and f.get("due", "9999") <= today:
                f["fired"] = True
                fired_any = True
                try:
                    fn = "send_group_msg" if f["chat_type"] == "group" else "send_private_msg"
                    tid = int(f["chat_id"])
                    await self._bot.call_action(fn, {"group_id" if f["chat_type"] == "group" else "user_id": tid,
                                                     "message": [{"type": "text", "data": {"text": f"上次你提到「{f['topic']}」，后来怎么样了？"}}]})
                except Exception:
                    f["fired"] = False
        if fired_any:
            json.dump(fups, open(ffp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
