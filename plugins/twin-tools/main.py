import asyncio
import json
import os
import re
import requests
from datetime import datetime, timedelta
from uuid import uuid4

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Image

TAVILY_KEY = "YOUR_TAVILY_KEY"
EMOJI_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "emojis"))
MEM_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "memory"))
DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng"))


@register("twin_tools", "your-name", "分身自主工具: 搜索/表情/记忆/提醒/跟进", "1.0.0")
class TwinTools(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        os.makedirs(MEM_DIR, exist_ok=True)

    # ---------- 工具1: 联网搜索 ----------
    @filter.llm_tool(name="web_search")
    async def web_search(self, event: AstrMessageEvent, query: str):
        """联网搜索最新信息。当聊到新闻/时事/最新动态/比赛结果/你不确定的事实时主动使用。

        Args:
            query(string): 搜索查询词
        """
        try:
            r = requests.post('https://api.tavily.com/search',
                              headers={'Authorization': 'Bearer ' + TAVILY_KEY},
                              json={'query': query, 'max_results': 5, 'search_depth': 'advanced'}, timeout=30)
            out = []
            for x in r.json().get('results', [])[:5]:
                out.append(f"- {x.get('title','')}: {x.get('content','')[:200]} ({x.get('url','')})")
            return "搜索结果:\n" + "\n".join(out) if out else "没搜到结果"
        except Exception as e:
            return f"搜索出错: {e}"

    # ---------- 工具2: 表情包检索 ----------
    @filter.llm_tool(name="find_sticker")
    async def find_sticker(self, event: AstrMessageEvent, emotion: str):
        """从表情包库检索并发送一张符合情绪的图片。想配表情包时使用。

        Args:
            emotion(string): 情绪或关键词, 如 无语/大笑/阴阳怪气/猫
        """
        try:
            idx = json.load(open(os.path.join(EMOJI_DIR, "index.json"), encoding="utf-8"))
            best, best_score = None, 0
            for e in idx:
                score = 0
                for k in e.get("keywords", []) or []:
                    k = str(k)
                    if emotion in k or k in emotion:
                        score = max(score, 3)
                hay = str(e.get("scene", "")) + str(e.get("emotion", "")) + str(e.get("desc", ""))
                if emotion in hay:
                    score = max(score, 2)
                if score > best_score:
                    f = os.path.join(EMOJI_DIR, str(e.get("file", "")))
                    if os.path.exists(f):
                        best, best_score = f, score
            if best:
                await event.send(Image.fromFileSystem(best))
                return f"已发送表情包(匹配:{emotion})"
            return f"库里没有匹配'{emotion}'的图, 不要再尝试发表情, 用文字回应"
        except Exception as e:
            return f"表情检索出错: {e}"

    # ---------- 工具3: 记忆查询 ----------
    @filter.llm_tool(name="query_memory")
    async def query_memory(self, event: AstrMessageEvent, about: str):
        """查询你记住的关于联系人/话题的长期记忆。想不起来对方说过什么时使用。

        Args:
            about(string): 要查询的人名/昵称/话题关键词
        """
        hits = []
        try:
            from memory_search import search as mem_search
            hits = mem_search(about)
            return "相关记忆:\n" + "\n".join(hits) if hits else f"没有关于'{about}'的记忆"
        except Exception as e:
            return f"记忆查询出错: {e}"

    # ---------- 工具4: 保存记忆 ----------
    @filter.llm_tool(name="save_memory")
    async def save_memory(self, event: AstrMessageEvent, fact: str):
        """保存值得长期记住的事实(对方的偏好/重要事件/计划)。对方明确表达这些时使用。

        Args:
            fact(string): 要记住的事实, 一句话
        """
        try:
            key = f"p{event.get_sender_id()}" if not event.get_group_id() else f"g{event.get_group_id()}"
            fp = os.path.join(MEM_DIR, f"{key}.json")
            d = json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else {"facts": []}
            if fact not in [x.get("text") for x in d["facts"]]:
                d["facts"].append({"text": fact[:150], "time": datetime.now().isoformat(timespec="seconds")})
                d["facts"] = d["facts"][-50:]
                json.dump(d, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            return "已记住"
        except Exception as e:
            return f"保存出错: {e}"

    # ---------- 工具: 知识库检索 ----------
    @filter.llm_tool(name="search_knowledge")
    async def search_knowledge(self, event: AstrMessageEvent, query: str):
        """检索本地知识库(项目文档/技术笔记/蒸馏资料)。被问到与"这个项目/本系统/部署/蒸馏方法"相关的问题时使用。

        Args:
            query(string): 查询关键词
        """
        try:
            import sys
            sys.path.insert(0, os.path.dirname(__file__))
            from kb import search as kb_search
            hits = kb_search(query)
            jn = "\n"
            body = ("\n---\n").join(hits)
            return "知识库检索结果:\n" + body if hits else f"知识库里没有关于'{query}'的内容"
        except Exception as e:
            return f"知识库检索出错: {e}"

    # ---------- 工具5: 定时提醒 ----------
    @filter.llm_tool(name="set_reminder")
    async def set_reminder(self, event: AstrMessageEvent, time_str: str, content: str):
        """设置定时提醒, 到点会主动发消息提醒。用户说'提醒我/到时候叫我'时使用。

        Args:
            time_str(string): 时间, 支持相对("30分钟后"/"2小时后")、中文时段("今晚9点"/"明早8点")、绝对("HH:MM"或"YYYY-MM-DD HH:MM")
            content(string): 提醒内容
        """
        try:
            t = None
            now = datetime.now()
            ts = time_str.strip()
            # 相对时间: "30分钟后" / "2小时后" / "1天半后"
            m = re.search(r'(\d+(?:\.\d+)?)\s*个?(小时|分钟|天|日)(半)?后', ts)
            if m:
                v = float(m.group(1))
                half = 0.5 if m.group(3) else 0
                if m.group(2) == '小时':
                    t = now + timedelta(hours=v + half)
                elif m.group(2) == '分钟':
                    t = now + timedelta(minutes=v + half * 60)
                else:
                    t = now + timedelta(days=v + half)
            if t is None:
                for fmt in ("%Y-%m-%d %H:%M", "%m-%d %H:%M", "%H:%M"):
                    try:
                        t = datetime.strptime(ts, fmt)
                        if fmt != "%Y-%m-%d %H:%M":
                            t = t.replace(year=now.year)
                        if t < now - timedelta(hours=1):
                            t = t.replace(year=now.year + 1) if fmt == "%Y-%m-%d %H:%M" else t + timedelta(days=1)
                        break
                    except ValueError:
                        continue
            if t is None:
                # 中文时段: 今晚/明早/明天晚上
                base = None
                if '今晚' in ts or '今天晚上' in ts: base = now.replace(hour=21, minute=0)
                elif '今晚' in ts or '今天晚上' in ts: base = now.replace(hour=21, minute=0)
                elif '明早' in ts or '明天早上' in ts: base = (now + timedelta(days=1)).replace(hour=8, minute=0)
                elif '明天' in ts: base = (now + timedelta(days=1)).replace(hour=12, minute=0)
                elif '晚上' in ts: base = now.replace(hour=21, minute=0)
                elif '早上' in ts or '早晨' in ts: base = now.replace(hour=8, minute=0)
                elif '中午' in ts: base = now.replace(hour=12, minute=0)
                hm = re.search(r'(\d{1,2})[点:：](\d{1,2}|半)?', ts)
                if base and hm:
                    minute = 30 if hm.group(2) == '半' else int(hm.group(2) or 0)
                    base = base.replace(hour=int(hm.group(1)) % 24, minute=minute)
                t = base
                if t and t < now:
                    t += timedelta(days=1)
            if t is None:
                return f"时间格式没看懂: {time_str}, 试试 '30分钟后' / '今晚9点' / '2026-01-01 14:00'"
            rid = uuid4().hex[:8]
            rem = {"id": rid, "time": t.isoformat(timespec="seconds"), "content": content[:100],
                   "chat_type": "group" if event.get_group_id() else "private",
                   "chat_id": int(event.get_group_id() or event.get_sender_id())}
            fp = os.path.join(DATA_DIR, "reminders.json")
            lst = json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else []
            lst.append(rem)
            json.dump(lst, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            return f"好的, {t.strftime('%m-%d %H:%M')} 我会提醒你: {content}"
        except Exception as e:
            return f"设置提醒出错: {e}"

    # ---------- 工具6: 事项跟进 ----------
    @filter.llm_tool(name="add_followup")
    async def add_followup(self, event: AstrMessageEvent, topic: str, due_date: str):
        """登记一件之后要主动追问的事。对方提到未来的事(面试/考试/比赛)时使用。

        Args:
            topic(string): 事情主题, 如 考研初试
            due_date(string): 预计出结果/该追问的日期, YYYY-MM-DD
        """
        try:
            fp = os.path.join(DATA_DIR, "followups.json")
            lst = json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else []
            lst.append({"topic": topic[:60], "due": due_date.strip(),
                        "chat_type": "group" if event.get_group_id() else "private",
                        "chat_id": int(event.get_group_id() or event.get_sender_id()),
                        "fired": False})
            json.dump(lst, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            return f"记下了, {due_date} 会主动问你结果"
        except Exception as e:
            return f"登记出错: {e}"
