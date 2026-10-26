# -*- coding: utf-8 -*-
"""Jev 判断器(TypeSafe System One): 回复前快速判断该认真答还是闲聊
- 每次真实回复前问 Jev 两个判断题(一次请求并行): 需要认真回答吗 / 对方情绪低落吗
- 0.7/0.3 阈值决策(高于0.7是, 低于0.3否, 中间不干预), 按结果注入回复风格
- 失败熔断: 连续3次失败停用10分钟(绝不阻塞回复)
- key/中转地址: data/fensheng/typesafe_key.json  {"api_key": "...", "api_base": "可选中转"}
- TypeSafe 对中国大陆 451, 国内服务器需配 Cloudflare Worker 中转(api_base 填 worker 地址)
"""
import asyncio
import json
import os
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


@register("twin_judge", "wuzihang", "Jev判断器: 认真/闲聊模式自动切换(TypeSafe System One)", "1.0.0")
class TwinJudge(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._fails = 0
        self._down_until = 0.0

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
                    asyncio.get_event_loop().run_in_executor(None, self._ask_jev, state),
                    timeout=TIMEOUT + 2)
            except Exception as e:
                self._fails += 1
                if self._fails >= FAIL_LIMIT:
                    self._down_until = time.time() + COOLDOWN
                    print(f"[twin_judge] 连续{self._fails}次失败, 熔断10分钟: {str(e)[:80]}")
                return
            if not data:
                self._fails += 1
                return
            self._fails = 0
            serious = self._p(data, "serious")
            upset = self._p(data, "upset")
            print(f"[twin_judge] serious={serious} upset={upset} | {text[:30]}")
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
