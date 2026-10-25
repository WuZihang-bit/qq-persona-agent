import asyncio
import json
import os
import random
import re
import time
from collections import deque, defaultdict

import requests

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain, Image, Reply
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.event_message_type import EventMessageType

# ---- 可调参数 ----
BOT_UIN = "3662430655"     # 小号(bot自己)的QQ号: 用于识别@消息
MASTER_QQ = "1522727368"   # 主人大号: 他的消息高优先级接话
MASTER_COOLDOWN = 45       # 主人发言的接话冷却(秒), 防止连刷屏
CHANCE = 0.13              # 普通群消息roll进打分的概率(读空气评分会再过滤)
GROUP_COOLDOWN = 480       # 普通插话: 同一群最小间隔(秒)
DAILY_CAP = 40             # 每天插话总上限
SILENT_START = (23, 30)    # 静默时段 23:30 ~ 8:00 (主人叫它除外)
SILENT_END = (8, 0)
WINDOW = 14                # 喂给LLM的最近消息条数
REACTION_DELAY = (2.0, 6.0)
SCORE_CAP_H = 20           # 激活度打分每小时上限(控成本)
SCORE_TH = 6               # 激活度>=此分才组织回复
IMP_EVERY = 220            # 每累计多少条群消息做一次群印象总结
IMG_CHANCE = 0.05          # 纯图消息吐槽概率(VLM有成本, 压低)
IMG_CAP_H = 6              # VLM识图每小时上限
EXP_FP = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "expressions.json"))
EXP_KEEP = 80              # 表达库上限
POS_PAT = re.compile(r"哈哈|哈哈哈|笑死|牛|666|\+1|好活|爱了|有意思|可以|妙啊|绷不|对对对|真实|eleven|泪目|排面")
NEG_PAT = re.compile(r"尬|无语|汗|离谱|？？？|\?\?\?|creepy|无聊|机器人|ai吧")

PROMPT_FILE = os.path.join(os.path.dirname(__file__), "group_prompt.txt")
EMOJI_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "emojis"))
SCHED_NOW = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "schedule_now.txt"))
IMP_FP = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "group_impressions.json"))
EMOJI_TAG = re.compile(r"\[\[EMOJI:([^\]]+)\]\]")
QUOTE_TAG = re.compile(r"\[++\s*QUOTE\s*[:：][^\]]*\]+", re.IGNORECASE)
NAME_PAT = re.compile(r"yuan", re.IGNORECASE)   # 有人提bot名字: 高把握接话


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
        self.raw_log = defaultdict(list)      # 群印象累计原始日志(内存)
        self._summing = set()                 # 正在做群印象的群
        self._score_times = []                # 激活度打分时间戳(小时窗)
        self._vlm_times = []                  # VLM识图时间戳(小时窗)
        self._vlm_key = None                  # GLM key(懒加载自cmd_config)
        self._last_own = {}                   # gid -> 我最近一条发言(表达学习观察窗)

    # ---------- 表达自学习: 群友对我发言的反应 -> 强化/弱化说法 ----------
    def _exp_load(self):
        try:
            return json.load(open(EXP_FP, encoding="utf-8"))
        except Exception:
            return {}

    def _exp_save(self, d):
        keep = sorted(d.items(), key=lambda x: -x[1])[:EXP_KEEP]
        json.dump(dict(keep), open(EXP_FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    def _credit_own(self, gid, text):
        self._last_own[gid] = {"text": text[:60], "ts": time.time()}

    def _observe_reaction(self, gid, sender, text):
        lo = self._last_own.get(gid)
        if not lo or time.time() - lo["ts"] > 150 or len(lo["text"]) < 4:
            self._last_own.pop(gid, None)
            return
        d = self._exp_load()
        changed = False
        if POS_PAT.search(text):
            d[lo["text"]] = d.get(lo["text"], 0) + 2
            changed = True
            print(f"[group] 表达+2: {lo['text'][:30]}")
        elif NEG_PAT.search(text):
            d[lo["text"]] = d.get(lo["text"], 0) - 1
            changed = True
        self._last_own.pop(gid, None)
        if changed:
            self._exp_save({k: v for k, v in d.items() if v > 0})

    def _exp_inject(self) -> str:
        try:
            d = self._exp_load()
            tops = [t for t, s in sorted(d.items(), key=lambda x: -x[1])[:5]
                    if s >= 2 and 4 <= len(t) <= 50]
            if tops:
                return "\n[你以前反应很好的说法(风格参考, 别原句复读)]\n" + "\n".join(f"- {t}" for t in tops)
        except Exception:
            pass
        return ""
        try:
            self.prompt = open(PROMPT_FILE, encoding="utf-8").read()
        except Exception:
            self.prompt = ""

    # ---------- 群印象 ----------
    def _impress_text(self, gid) -> str:
        try:
            d = json.load(open(IMP_FP, encoding="utf-8")).get(str(gid))
            if not d:
                return ""
            out = f"[群印象] {d.get('vibe', '')} 常聊:{','.join(d.get('topics', [])[:4])}"
            if d.get("members"):
                out += f" 核心:{d['members']}"
            sl = d.get("slang") or {}
            if sl:
                out += "\n[群黑话] " + "; ".join(f"{k}={v}" for k, v in list(sl.items())[:5])
            al = d.get("aliases") or {}
            pairs = "; ".join(f"{k}也叫{','.join(map(str, v[:2]))}" for k, v in list(al.items())[:6] if v)
            if pairs:
                out += f"\n[称呼] {pairs}"
            return out
        except Exception:
            return ""

    async def _summarize_group(self, gid):
        """累计消息够量后总结群印象(氛围/话题/成员/别名), 存盘供所有插件注入"""
        if gid in self._summing:
            return
        self._summing.add(gid)
        try:
            provider = self.context.get_using_provider()
            if provider is not None and len(self.raw_log[gid]) >= 100:
                r = await provider.text_chat(
                    prompt="以下是QQ群最近的聊天记录:\n" + "\n".join(self.raw_log[gid][-260:])[-5200:] +
                           '\n\n总结这个群, 只输出JSON: {"vibe":"群氛围30字内","topics":["常聊话题,最多4个"],'
                           '"members":"最活跃的几个人20字内","aliases":{"某昵称":["他也被叫的外号"]},'
                           '"slang":{"群里黑话/梗":"含义解释"}}  '
                           'slang最多5个, 只收这个群特有的(缩写/外号/inside joke), 没有就空对象',
                    system_prompt="你是群聊分析器, 只输出JSON, 不要解释。")
                raw = (getattr(r, "completion_text", "") or "").strip()
                m = re.search(r'\{.*\}', raw, re.S)
                if m:
                    d = json.loads(m.group())
                    d["updated"] = time.strftime("%Y-%m-%d")
                    try:
                        imp = json.load(open(IMP_FP, encoding="utf-8"))
                    except Exception:
                        imp = {}
                    imp[str(gid)] = d
                    json.dump(imp, open(IMP_FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                    print(f"[group] 群{gid}印象已更新")
        except Exception:
            pass
        finally:
            self.raw_log[gid] = []
            self._summing.discard(gid)

    # ---------- 读空气: 激活度打分 ----------
    async def _activation(self, gid):
        """0-10 你此刻想插话的冲动; 失败/超限返回None走原逻辑(保证稳定)"""
        try:
            now = time.time()
            self._score_times = [t for t in self._score_times if now - t < 3600]
            if len(self._score_times) >= SCORE_CAP_H:
                return None
            provider = self.context.get_using_provider()
            if provider is None:
                return None
            self._score_times.append(now)
            r = await provider.text_chat(
                prompt="你是QQ群里的群友Yuan(电子科大信软大一女生,爱打王者主玩弈星,工科生,懒但接梗快)。\n"
                       "下面是群聊记录, 你此刻想插话的冲动是多少分(0-10)?\n"
                       "打分依据: 话题你是否感兴趣(游戏/校园/梗/吃的/技术) / 有没有能接的梗 / 情绪共鸣。\n"
                       "只输出一个数字。\n\n" + self._window_text(gid),
                system_prompt="你是激活度评估器, 只输出0-10之间一个数字。")
            raw = (getattr(r, "completion_text", "") or "").strip()
            m = re.search(r'\d+', raw)
            return int(m.group()) if m else None
        except Exception:
            return None

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
        import re as _rv2
        text = _rv2.sub(r'\[[【]{1,2}\s*VOICE\s*[:：][^\]】]*[\]】]', ' ', text)
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

    # ---------- 群聊图片吐槽(VLM识图+低概率) ----------
    def _glm_key(self):
        if self._vlm_key:
            return self._vlm_key
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
            import json as _json
            fp = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "cmd_config.json"))
            self._vlm_key = find_key(_json.load(open(fp, encoding="utf-8"))) or ""
        except Exception:
            self._vlm_key = ""
        return self._vlm_key

    def _vlm_caption(self, img_bytes) -> str:
        """GLM-4V-Flash识图(免费模型, 不占聊天额度), 失败返回空"""
        key = self._glm_key()
        if not key:
            return ""
        try:
            import base64
            b64 = base64.b64encode(img_bytes).decode()
            r = requests.post(
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={"Authorization": "Bearer " + key}, timeout=40,
                json={"model": "glm-4v-flash",
                      "messages": [{"role": "user", "content": [
                          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                          {"type": "text", "text": "用40字内描述这张图的内容和笑点/亮点"}]}]})
            return (r.json()["choices"][0]["message"]["content"] or "").strip()[:80]
        except Exception:
            return ""

    async def _react_image(self, event, gid, url):
        """看到群里的图, 偶尔吐槽一句"""
        now = time.time()
        self._vlm_times = [t for t in self._vlm_times if now - t < 3600]
        if len(self._vlm_times) >= IMG_CAP_H:
            return
        await asyncio.sleep(random.uniform(3.0, 8.0))

        def _dl():
            return requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}).content
        try:
            img = await asyncio.get_event_loop().run_in_executor(None, _dl)
        except Exception:
            return
        if not img or len(img) < 2000:
            return
        self._vlm_times.append(time.time())
        cap = await asyncio.get_event_loop().run_in_executor(None, lambda: self._vlm_caption(img))
        if not cap:
            return
        provider = self.context.get_using_provider()
        if provider is None:
            return
        sys_prompt = (self.prompt or "你是一个QQ群里的闲聊分身。") + \
            f"\n[当前状态] " + self._read_sched() + "\n" + self._impress_text(gid)
        try:
            resp = await provider.text_chat(
                prompt="群友在群里发了一张图, 图的内容: " + cap + "\n"
                       "你看到了, 随口吐槽/评论一句(15字内, 接地气, 像刷群时看到的反应)。没意思就只输出 [SKIP]",
                system_prompt=sys_prompt)
            reply = (getattr(resp, "completion_text", "") or "").strip()
            if not reply or "[SKIP]" in reply:
                return
            self.last_speak[gid] = time.time()
            self.today_count += 1
            tid = str(getattr(event.message_obj, "message_id", "") or "")
            parts = self._build_chain(reply)
            if tid:
                parts = [Reply(id=tid)] + parts
            if parts:
                await event.send(MessageChain(chain=parts))
                self._credit_own(gid, reply[:60])
                print(f"[group] 图片吐槽: {cap[:30]} -> {reply[:30]}")
        except Exception:
            pass

    def _read_sched(self) -> str:
        try:
            return open(SCHED_NOW, encoding="utf-8").read().strip() or "闲着"
        except Exception:
            return "闲着"

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
            # 群印象: 累计原始日志, 够量后台总结
            self.raw_log[gid].append(f"{sender}: {text[:80]}")
            if len(self.raw_log[gid]) >= IMP_EVERY:
                asyncio.create_task(self._summarize_group(gid))

            # 表达学习: 观察群友对我上一条发言的反应
            if str(event.get_sender_id()) != BOT_UIN:
                self._observe_reaction(gid, sender, text)

            # @自己的消息由主管道处理, 围观插件让路 (否则会双发)
            self_id = str((getattr(event, "get_self_id", lambda: "")() or ""))
            img_urls = []
            for comp in event.message_obj.message:
                qq = str(getattr(comp, "qq", "") or "")
                if type(comp).__name__ == "At" and qq in (BOT_UIN, self_id):
                    return
                if type(comp).__name__ == "Image":
                    u = getattr(comp, "url", None) or ""
                    if u:
                        img_urls.append(u)

            # 纯图消息: 低概率VLM吐槽(独立小通道, 不占文字插话额度判定)
            if img_urls and not text:
                if self._quota_ok() and not self._silent_now() \
                        and time.time() - self.last_speak.get(gid, 0) >= GROUP_COOLDOWN \
                        and random.random() < IMG_CHANCE:
                    asyncio.create_task(self._react_image(event, gid, img_urls[0]))
                return

            # 同群同内容90秒去重保险
            last = self.last_reply.get(gid)
            if last and time.time() - last[1] < 90:
                return

            if is_master:
                fire, reason = self._master_fire(gid), "master"
            elif NAME_PAT.search(text):
                fire, reason = self._master_fire(gid), "name"    # 有人提Yuan: 短冷却高把握
            else:
                fire, reason = self._roll(gid), "roll"
            if not fire:
                return

            # 反应延迟, 像刷群时偶然看到
            await asyncio.sleep(random.uniform(*REACTION_DELAY))
            if not self.windows[gid]:
                return

            # 读空气: roll路径先过激活度打分, 低分沉默; 名字/主人路径直接进
            if reason == "roll":
                sc = await self._activation(gid)
                if sc is not None and sc < SCORE_TH:
                    return

            provider = self.context.get_using_provider()
            if provider is None:
                return

            sys_prompt = self.prompt or "你是一个QQ群里的闲聊分身。没话说就输出[SKIP]。"
            # 注入当前日程状态 + 群印象(twin_schedule/本插件写的, 松耦合读文件)
            try:
                cur = open(SCHED_NOW, encoding="utf-8").read().strip()
                if cur:
                    sys_prompt += f"\n[当前状态] 你现在在{cur}"
            except Exception:
                pass
            imp = self._impress_text(gid)
            if imp:
                sys_prompt += "\n" + imp
            sys_prompt += self._exp_inject()
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
            self._credit_own(gid, reply[:60])
        except Exception:
            pass  # 监听器绝不影响主流程
