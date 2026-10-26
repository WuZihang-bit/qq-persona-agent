# -*- coding: utf-8 -*-
"""Jev 判断器: 回复前快速判断该认真答还是闲聊
双引擎自动切换:
1. TypeSafe Jev(System One, 首选): 2个noul并行, 校准概率
2. GLM-4-flash(兜底, 免费): 单次小调用输出同样的两个0-1分数
- 0.7/0.3 阈值决策, 按结果注入回复风格
- 各自独立熔断: 连续3次失败停用10分钟(绝不阻塞回复)
- 配置: data/fensheng/typesafe_key.json  {"api_key","api_base"(CF中转),"glm_key"(缺省读cmd_config)}
- TypeSafe 对大陆 451 + workers.dev 被墙, 国内需 CF Worker + 自定义域名中转; 无中转时自动用GLM引擎
"""
import asyncio
import json
import os
import re
import time
import urllib.request

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng"))
KEY_FP = os.path.join(DATA_DIR, "typesafe_key.json")
ENDPOINT_PATH = "/v1/systemone"
TIMEOUT = 6
FAIL_LIMIT = 3
COOLDOWN = 600

CASUAL_HINT = ("[回复模式:日常闲聊] 这条是闲聊/玩笑/情绪表达: 回复1-2句口语短句, "
               "可以玩梗可以调侃, 不解释不复述不列点")
SERIOUS_HINT = ("[回复模式:认真回答] 这条提出了正经问题/请求/事务: 给出有条理、信息完整的回答, "
                "可以适当展开, 但仍用你自己的口吻说, 别像百科腔")
EMPATHY_HINT = "[回复模式:对方情绪低落] 先共情站队, 陪伴感优先, 别讲道理别给建议清单, 语气放软"

Q_SERIOUS = ("noul", "这条消息提出了问题、请求或正经事务, 需要认真、完整、有条理的回答。")
Q_UPSET = ("noul", "发这条消息的人情绪低落、受挫、难过或需要安慰。")


def _cfg():
    try:
        return json.load(open(KEY_FP, encoding="utf-8"))
    except Exception:
        return {}


def _glm_key():
    k = _cfg().get("glm_key", "")
    if k:
        return k
    try:  # 兜底: 从cmd_config抓智谱key
        def fk(o):
            if isinstance(o, str) and re.match(r"^[0-9a-f]{32}\.[A-Za-z0-9]{8,}$", o):
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
            os.path.dirname(__file__), "..", "..", "cmd_config.json")), encoding="utf-8-sig"))) or ""
    except Exception:
        return ""


def _glm_judge(state):
    """GLM兜底判断: 输出与Jev等价的两个概率; 模型列表轮询(4.5-flash常过载, 5.3-flash主力兜底)"""
    k = _glm_key()
    if not k:
        return None
    last = None
    for model in ("glm-4.5-flash", "glm-5.3-flash"):
        payload = {"model": model, "temperature": 0.1, "max_tokens": 40,
                   "messages": [
                       {"role": "system", "content": "你是意图分类器, 只输出JSON不解释。"},
                       {"role": "user", "content":
                        f"用户消息: {state.get('message', '')[:250]}\n"
                        '判断并只输出: {"serious": 0到1(这条需要认真完整有条理的回答吗), '
                        '"upset": 0到1(发消息的人情绪低落需要安慰吗)}'}]}
        req = urllib.request.Request(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + k})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read().decode())
        except Exception as e:
            last = e
            continue
        raw = data["choices"][0]["message"]["content"] or ""
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            last = Exception("no json: " + raw[:50])
            continue
        d = json.loads(m.group())
        return {"answers": {"serious": {"noul": float(d.get("serious", 0.5))},
                            "upset": {"noul": float(d.get("upset", 0.5))}}}
    raise last or Exception("glm judge failed")


@register("twin_judge", "wuzihang", "Jev判断器: 认真/闲聊模式自动切换(TypeSafe System One)", "1.0.0")
class TwinJudge(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._fails = 0
        self._down_until = 0.0
        self._glm_fails = 0
        self._glm_down_until = 0.0
        self._last_engine = ""

    def _ask_jev(self, state):
        cfg = _cfg()
        key = cfg.get("api_key", "")
        if not key:
            return None
        base = (cfg.get("api_base") or "https://api.typesafe.ai").rstrip("/")
        payload = {"model": cfg.get("model", "jev-latest"), "state": state,
                   "questions": {
                       "serious": {"type": "noul", "instructions": Q_SERIOUS[1]},
                       "upset": {"type": "noul", "instructions": Q_UPSET[1]}}}
        req = urllib.request.Request(
            base + ENDPOINT_PATH, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())

    def _ask(self, state):
        """引擎选择: Jev优先(可用时), 失败回落GLM-4-flash; 各自独立熔断"""
        now = time.time()
        if now >= self._down_until:
            try:
                r = self._ask_jev(state)
                if r:
                    self._fails = 0
                    self._last_engine = "jev"
                    return r
                self._fails += 1
            except Exception:
                self._fails += 1
            if self._fails >= FAIL_LIMIT:
                self._down_until = now + COOLDOWN
                print(f"[twin_judge] jev连续{self._fails}失败, 熔断10分钟(期间用glm)")
        if now >= self._glm_down_until:
            try:
                r = _glm_judge(state)
                if r:
                    self._glm_fails = 0
                    self._last_engine = "glm"
                    return r
                self._glm_fails += 1
            except Exception:
                self._glm_fails += 1
            if self._glm_fails >= FAIL_LIMIT:
                self._glm_down_until = now + COOLDOWN
                print(f"[twin_judge] glm也连续失败, 熔断10分钟")
        return None

    @staticmethod
    def _p(data, name):
        try:
            a = (data.get("answers") or {})[name]
            return float(a.get("noul", a.get("probability", a.get("value", 0.5))))
        except Exception:
            return None

    @filter.on_llm_request()
    async def judge(self, event: AstrMessageEvent, req: ProviderRequest):
        try:
            text = (event.message_str or "").strip()
            if len(text) < 2:
                return                          # "嗯""哦"之类, 默认短回即可
            if time.time() < self._down_until:
                return                          # 熔断中
            state = {
                "message": text[:300],
                "sender_is_master": False,
                "chat_type": "group" if event.get_group_id() else "private",
            }
            try:
                data = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, self._ask, state),
                    timeout=TIMEOUT + 2)
            except Exception:
                return
            if not data:
                return
            serious = self._p(data, "serious")
            upset = self._p(data, "upset")
            print(f"[twin_judge] engine={self._last_engine} serious={serious} upset={upset} | {text[:30]}")
            hints = []
            if serious is not None and serious >= 0.7:
                hints.append(SERIOUS_HINT)
            if upset is not None and upset >= 0.7:
                hints.append(EMPATHY_HINT)
            if not hints and (serious is None or serious <= 0.3):
                hints.append(CASUAL_HINT)
            if hints:
                req.system_prompt = (req.system_prompt or "") + "\n" + "\n".join(hints)
        except Exception:
            pass
