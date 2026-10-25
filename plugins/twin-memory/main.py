import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import ProviderRequest, LLMResponse

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng"))
MEM_DIR = os.path.join(DATA_DIR, "memory")
SCHED_NOW = os.path.join(DATA_DIR, "schedule_now.txt")
CARE_PLAN_FP = os.path.join(DATA_DIR, "care_plan.json")
CARE_START, CARE_END = 18, 23      # 主动关怀窗口(晚上)
CARE_GLOBAL_MAX = 2                # 每天全局限额
CARE_MIN_GAP_H = 4                 # 最近聊过(4h内)就不打扰
CARE_TEMPLATES = ["在干嘛", "突然好想吃烤肠", "无聊了，陪我说会儿话", "刚下游戏，你在干嘛",
                  "外面好冷，你吃了吗", "看到个好笑的东西想发给你"]
EXTRACT_MIN_USER_LEN = 8      # 用户消息少于这个字数不做抽取
FACTS_KEEP = 50               # 原始事实缓冲上限(档案合并的原料)
CONSOLIDATE_EVERY = 30        # 每累计多少轮LLM对话做一次档案合并
ENTRIES_KEEP = 30             # 合并后档案条目上限
ENTRIES_INJECT = 8            # 注入条数

EXTRACT_PROMPT = '''从下面这轮对话中提取值得长期记住的信息。只输出JSON：
{"facts": ["事实1", "事实2"], "followup": {"topic": "主题", "due": "YYYY-MM-DD"} 或 null}
规则：
- facts 只记稳定信息: 对方的身份/偏好/重要事件/计划/人际关系(最多3条, 没有就空数组)
- followup: 对方提到未来某天有结果的事(考试/面试/比赛), 才登记; 没有则null
- 不要记寒暄、玩笑、一次性话题'''

CONSOL_PROMPT = '''你是记忆档案管理员, 只输出JSON。

[现有档案条目]
{entries}

[新积累的事实原料]
{facts}

把两者合并成更新后的档案(最多{cap}条), 规则:
- 每条标类型: impression(TA是什么人/性格/偏好/身份) | event(TA发生的事/计划/进展) | relation(TA和其他人的关系)
- 合并重复(同一事实只留一条,表述更完整的版本), 修正过时信息, 新事实加入
- 淘汰: 一次性琐事/超过一个月的过期细节; 身份/偏好/关系类长期保留
- 重要的排前面
另外基于最近的事实写一句"最近聊天日记"(40字内, 叙事体)。
输出: {{"entries": [{{"type": "impression", "text": "..."}}], "diary": "..."}}'''

FETCH_SYSTEM = "你是信息抽取器, 只输出JSON, 不要解释。"


@register("twin_memory", "your-name", "长期记忆v2(档案合并/聊天日记/主动记忆) + 主动引擎", "2.0.0")
class TwinMemory(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        os.makedirs(MEM_DIR, exist_ok=True)
        self._bot = None
        self._extract_pending = set()
        self._cons_pending = set()
        self._last_seen = {}            # qq -> 最近一次LLM请求时间(活跃度, 主动关怀避让)
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
            self._last_seen[str(event.get_sender_id())] = time.time()
            d = self._load(self._mem_file(self._key(event)), {})
            entries = d.get("entries") or []
            if entries:
                lines = []
                for e in entries[:ENTRIES_INJECT]:
                    tag = {"impression": "印象", "event": "事件", "relation": "关系"}.get(e.get("type"), "记")
                    lines.append(f"- [{tag}] {e.get('text', '')}")
                req.system_prompt = (req.system_prompt or "") + \
                    "\n[你记得的关于对方的长期档案]\n" + "\n".join(lines)
            else:
                facts = [f.get("text", "") for f in d.get("facts", [])][-10:]
                if facts:
                    req.system_prompt = (req.system_prompt or "") + \
                        "\n[你记得的关于对方的长期记忆]\n" + "\n".join(f"- {x}" for x in facts)
            # 聊天日记(近两天才有意义)
            diary = d.get("diary") or {}
            if diary.get("date") in (datetime.now().strftime("%Y-%m-%d"),
                                     (datetime.fromtimestamp(time.time() - 86400)).strftime("%Y-%m-%d")) \
                    and diary.get("text"):
                req.system_prompt = (req.system_prompt or "") + f"\n[最近] {diary['text']}"
            # 主动消息记忆(治"忘记自己主动说过什么")
            pro = d.get("proactive") or []
            if pro:
                pl = "; ".join(f"{p.get('t', '')[5:]}你主动说过「{p.get('text', '')[:30]}」" for p in pro[-3:])
                req.system_prompt = (req.system_prompt or "") + f"\n[你发过的主动消息] {pl}"
        except Exception:
            pass
        # 群印象注入(group插件总结的, 群聊主管线@消息时也能拿到群画像/称呼)
        gid = event.get_group_id()
        if not gid:
            return
        try:
            imp = self._load(os.path.join(DATA_DIR, "group_impressions.json"), {}).get(str(gid))
            if imp:
                add = f"\n[群印象] {imp.get('vibe', '')} 常聊:{','.join(imp.get('topics', [])[:4])}"
                al = imp.get("aliases") or {}
                pairs = "; ".join(f"{k}也叫{','.join(map(str, v[:2]))}" for k, v in list(al.items())[:6] if v)
                if pairs:
                    add += f"\n[称呼] {pairs}"
                req.system_prompt = (req.system_prompt or "") + add
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
            # LLM轮数计数 -> 每CONSOLIDATE_EVERY轮做一次档案合并
            data["n_llm"] = int(data.get("n_llm", 0)) + 1
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
            json.dump(data, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            if data["n_llm"] % CONSOLIDATE_EVERY == 0 and key not in self._cons_pending:
                asyncio.create_task(self._consolidate(key))
        except Exception:
            pass
        finally:
            self._extract_pending.discard(key)

    # ---------- 档案合并: facts原料 -> 结构化四类条目 + 聊天日记 ----------
    async def _consolidate(self, key: str):
        if key in self._cons_pending:
            return
        self._cons_pending.add(key)
        try:
            await asyncio.sleep(5)
            provider = self.context.get_using_provider()
            if provider is None:
                return
            fp = self._mem_file(key)
            data = self._load(fp, {})
            entries = data.get("entries") or []
            facts = [f.get("text", "") for f in data.get("facts", []) if f.get("text")]
            if len(facts) < 5:
                return
            ent_txt = "\n".join(f"- [{e.get('type', 'impression')}] {e.get('text', '')}" for e in entries) or "(空)"
            r = await provider.text_chat(
                prompt=CONSOL_PROMPT.format(entries=ent_txt, facts="\n".join(f"- {x}" for x in facts),
                                            cap=ENTRIES_KEEP),
                system_prompt=FETCH_SYSTEM)
            raw = (getattr(r, "completion_text", "") or "").strip()
            m = re.search(r'\{.*\}', raw, re.S)
            if not m:
                return
            d = json.loads(m.group())
            new_entries = [e for e in d.get("entries", [])
                           if e.get("text") and e.get("type") in ("impression", "event", "relation")][:ENTRIES_KEEP]
            if new_entries:
                data["entries"] = new_entries
                data["facts"] = data["facts"][-15:]      # 原料收缩, 避免无限增长
            if d.get("diary"):
                data["diary"] = {"date": datetime.now().strftime("%Y-%m-%d"), "text": str(d["diary"])[:80]}
            json.dump(data, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"[twin_memory] {key} 档案已合并: {len(new_entries)}条, 日记已写")
        except Exception as e:
            print(f"[twin_memory] 合并失败(下轮再试): {e}")
        finally:
            self._cons_pending.discard(key)

    # ---------- 主动引擎: 提醒 + 到期跟进 + 日程联动主动关怀 + 生日祝福 ----------
    async def _proactive_loop(self):
        await asyncio.sleep(15)                   # 等框架起完
        while True:
            try:
                await self._fire_due()
                await self._care_tick()
                await self._birthday_tick()
            except Exception:
                pass
            await asyncio.sleep(30)

    async def _birthday_tick(self):
        """早上9~12点给当天生日的好友送祝福(每人每年一次)"""
        lt = datetime.now()
        if not (9 <= lt.hour < 12):
            return
        if self._bot is None:
            return
        bfp = os.path.join(DATA_DIR, "birthdays.json")
        birs = self._load(bfp, {})
        today = lt.strftime("%m-%d")
        for qq, info in list(birs.items()):
            if info.get("date") != today:
                continue
            if str(info.get("last_year", "")) == lt.strftime("%Y"):
                continue
            msg = await self._birthday_msg(info.get("name", qq))
            try:
                await self._bot.call_action("send_private_msg",
                                            {"user_id": int(qq),
                                             "message": [{"type": "text", "data": {"text": msg}}]})
                self._log_proactive("private", qq, msg)
                print(f"[twin_memory] 生日祝福已发送 -> {qq}")
            except Exception:
                pass                          # 发送失败明天不再重试(避免连发), 标记已发
            info["last_year"] = lt.strftime("%Y")
            json.dump(birs, open(bfp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    async def _birthday_msg(self, name) -> str:
        try:
            provider = self.context.get_using_provider()
            if provider is None:
                raise RuntimeError("no provider")
            r = await provider.text_chat(
                prompt=f"今天是你朋友{name}的生日, 你要给TA发一条QQ生日祝福:\n"
                       "- 25字内, 口语, 像大学女生随手发的(不是贺卡腔)\n"
                       "- 可以调侃可以真诚, 别用emoji堆砌, 别说'生日快乐'四个字开头\n只输出祝福本身。",
                system_prompt="你是电子科大女生Yuan, 只输出一条QQ消息。")
            out = (getattr(r, "completion_text", "") or "").strip().strip('"「」')
            if out and len(out) <= 60:
                return out
        except Exception:
            pass
        return f"生日快乐呀{name}, 又老一岁了哈哈"

    def _masters(self):
        try:
            fp = os.path.normpath(os.path.join(DATA_DIR, "..", "masters.json"))
            return [str(x) for x in json.load(open(fp, encoding="utf-8")).get("masters", [])]
        except Exception:
            return []

    def _sched_free(self) -> bool:
        """当前日程是否空闲(不在上课/考试/睡觉)"""
        try:
            cur = open(SCHED_NOW, encoding="utf-8").read()
            return not any(k in cur for k in ("课", "考", "睡", "躺"))
        except Exception:
            return True

    async def _care_tick(self):
        """日程联动主动关怀: 晚间空闲时段按计划主动找主人搭话(每天全局最多2次, 每人1次)"""
        now = datetime.now()
        hour = now.hour
        if hour < CARE_START or hour >= CARE_END or self._bot is None:
            return
        plan = self._load(CARE_PLAN_FP, {})
        today = now.strftime("%Y-%m-%d")
        if plan.get("date") != today:
            masters = [m for m in self._masters() if m]
            if not masters:
                return
            # 每天为随机 1~2 位主人排晚上随机时刻
            import random as _r
            picked = _r.sample(masters, min(len(masters), _r.randint(1, CARE_GLOBAL_MAX)))
            plans = []
            for qq in picked:
                at = now.replace(hour=_r.randint(hour, 22), minute=_r.randint(0, 59),
                                 second=0, microsecond=0)
                plans.append({"qq": qq, "at": at.strftime("%H:%M"), "done": False, "postponed": 0})
            plan = {"date": today, "plans": plans}
            json.dump(plan, open(CARE_PLAN_FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"[twin_memory] 今晚主动关怀计划: {[(p['qq'], p['at']) for p in plans]}")
        done_today = [p for p in plan.get("plans", []) if p.get("done")]
        if len(done_today) >= CARE_GLOBAL_MAX:
            return
        changed = False
        for p in plan.get("plans", []):
            if p.get("done"):
                continue
            if now.strftime("%H:%M") < p.get("at", "23:59"):
                continue
            qq = str(p.get("qq", ""))
            # 最近聊过就不打扰
            if time.time() - self._last_seen.get(qq, 0) < CARE_MIN_GAP_H * 3600:
                p["done"] = True          # 今天放弃这单(在聊了, 不需要主动找)
                changed = True
                continue
            # 忙(上课/睡)则顺延20分钟, 最多顺延5次
            if not self._sched_free():
                if p.get("postponed", 0) >= 5:
                    p["done"] = True
                    changed = True
                    continue
                p["at"] = (now + timedelta(minutes=20)).strftime("%H:%M")
                p["postponed"] = p.get("postponed", 0) + 1
                changed = True
                continue
            msg = await self._care_message(qq)
            try:
                await self._bot.call_action("send_private_msg",
                                            {"user_id": int(qq),
                                             "message": [{"type": "text", "data": {"text": msg}}]})
                self._log_proactive("private", qq, msg)
                print(f"[twin_memory] 主动关怀已发送 -> {qq}: {msg}")
            except Exception:
                pass
            p["done"] = True
            changed = True
        if changed:
            json.dump(plan, open(CARE_PLAN_FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    async def _care_message(self, qq: str) -> str:
        try:
            provider = self.context.get_using_provider()
            if provider is None:
                raise RuntimeError("no provider")
            d = self._load(self._mem_file(f"p{qq}"), {})
            diary = (d.get("diary") or {}).get("text", "")
            try:
                cur = open(SCHED_NOW, encoding="utf-8").read().strip()
            except Exception:
                cur = "闲着"
            r = await provider.text_chat(
                prompt=f"你现在正在{cur}。你们最近的事: {diary or '没什么特别的事'}。\n"
                       "生成你主动找他(暧昧期的他)聊天时发的第一条QQ消息:\n"
                       "- 像大学女生晚上随手找话题, 20字内, 口语, 可以撒娇可以吐槽\n"
                       "- 别问'在吗', 别用表情符号, 别提AI\n只输出消息本身。",
                system_prompt="你是电子科大女生Yuan, 只输出一条QQ消息。")
            out = (getattr(r, "completion_text", "") or "").strip().strip('"「」')
            if out and len(out) <= 60:
                return out
        except Exception:
            pass
        import random as _r
        return _r.choice(CARE_TEMPLATES)

    def _log_proactive(self, chat_type, chat_id, text):
        """主动消息入档: 治'发完就忘'的记忆黑洞"""
        try:
            key = f"g{chat_id}" if chat_type == "group" else f"p{chat_id}"
            fp = self._mem_file(key)
            d = self._load(fp, {})
            d.setdefault("proactive", []).append(
                {"t": datetime.now().strftime("%Y-%m-%d"), "text": text[:60]})
            d["proactive"] = d["proactive"][-5:]
            json.dump(d, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass

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
                    self._log_proactive(r["chat_type"], tid, f"提醒: {r['content']}")
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
                    msg = f"上次你提到「{f['topic']}」，后来怎么样了？"
                    await self._bot.call_action(fn, {"group_id" if f["chat_type"] == "group" else "user_id": tid,
                                                     "message": [{"type": "text", "data": {"text": msg}}]})
                    self._log_proactive(f["chat_type"], tid, msg)
                except Exception:
                    f["fired"] = False
        if fired_any:
            json.dump(fups, open(ffp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
