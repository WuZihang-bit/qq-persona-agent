# -*- coding: utf-8 -*-
"""梗与形象知识库: 让bot懂热梗/新词/虚拟形象
- 种子词典(热梗+虚拟形象/AI形象)装机即懂, 对话自动注入命中词条
- /梗 <词> 查询 | /学梗 <词> <含义> 教学(仅主人) 
- "什么是X/X是什么" -> 本地词典没有则Tavily搜索学习并回答
- 每天自动上网挖新梗入库
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
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.filter.event_message_type import EventMessageType

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng"))
MEME_FP = os.path.join(DATA_DIR, "meme_knowledge.json")
MAX_INJECT = 5

# ---------------- 种子词典(装机即懂) ----------------
SEED = {
    # ===== 网络热梗 =====
    "哈基米": ("梗", "源自《赛马娘》歌词空耳的猫meme词, 泛指可爱猫猫; 家族还有那托/吉亚西/小圆/咕噜等"),
    "曼波": ("梗", "猫meme之一, 出自猫咪视频空耳, 慵懒迷茫时用"),
    "尊嘟假嘟": ("梗", "'真的假的'可爱化谐音(2023)"),
    "泰裤辣": ("梗", "'太酷啦'口音梗(2023)"),
    "搭子": ("梗", "特定场景的轻社交伙伴: 饭搭子/游戏搭子(2023)"),
    "显眼包": ("梗", "爱出风头又搞笑的人(2023)"),
    "泼天的富贵": ("梗", "突如其来的巨大流量/好运(2023)"),
    "遥遥领先": ("梗", "余承东华为发布会口头禅, 泛指大幅领先(2023)"),
    "科目三": ("梗", "社会摇舞蹈, 海底捞员工跳火(2023)"),
    "恐龙抗狼": ("梗", "云南博主bgm空耳(2023)"),
    "命运的齿轮": ("梗", "'命运的齿轮开始转动', 回顾人生转折点叙事梗(2023)"),
    "i人": ("梗", "MBTI里内向人格, 社交耗电型; 对应e人外向"),
    "e人": ("梗", "MBTI里外向人格, 社交充电型"),
    "淡人": ("梗", "情绪浓度低、淡淡的(2024); 对应浓人"),
    "班味": ("梗", "上班后挥之不去的疲惫气质(2024)"),
    "偷感": ("梗", "'偷感很重', 做事偷偷摸摸怕被注意(2024)"),
    "那咋了": ("梗", "松弛感万能回怼(2024)"),
    "古希腊掌管": ("梗", "'古希腊掌管XX的神', 夸某领域极强(2024)"),
    "硬控": ("梗", "被吸引得挪不开眼, 源自游戏控制技能术语(2024)"),
    "红温": ("梗", "急了/破防脸红的状态(2024)"),
    "水灵灵地": ("梗", "韩娱采访翻译梗, 活泼俏皮地(2024)"),
    "city不city": ("梗", "外国博主中国游爆火梗(2024)"),
    "已读乱回": ("梗", "答非所问的搞笑回复(2024)"),
    "已老实": ("梗", "'已老实求放过', 认怂梗(2024)"),
    "麦门": ("梗", "麦当劳忠实信徒的自称"),
    "疯四": ("梗", "肯德基疯狂星期四文学, 借钱段子"),
    "电子榨菜": ("梗", "下饭视频"),
    "电子木鱼": ("梗", "线上敲木鱼攒功德"),
    "抽象": ("梗", "难以理解又好笑的文化风格; '抽象话'"),
    "典": ("梗", "'太经典了'的讽刺缩写; 典孝急乐绷麻赢并称抽象六字"),
    "绷不住": ("梗", "忍不住笑"),
    "蚌埠住了": ("梗", "'绷不住了', 安徽蚌埠谐音"),
    "乐": ("梗", "好笑/看戏(抽象圈高频)"),
    "寄": ("梗", "完蛋了, GG演变"),
    "摆烂": ("梗", "破罐破摔不努力"),
    "躺平": ("梗", "佛系不奋斗"),
    "内卷": ("梗", "过度竞争"),
    "打工人": ("梗", "上班族自嘲"),
    "麻了": ("梗", "无语无奈到麻木"),
    "破防": ("梗", "心理防线被击破, 感动或被气到"),
    "泪目": ("梗", "感动落泪"),
    "666": ("梗", "厉害(游戏'溜'演变)"),
    "yyds": ("梗", "永远的神"),
    "栓Q": ("梗", "thank you谐音(2022)"),
    "我真的会谢": ("梗", "无语式感谢(2022)"),
    "夺笋": ("梗", "多损"),
    "集美": ("梗", "姐妹"),
    "吗喽": ("梗", "猴子自嘲, '吗喽的命也是命'打工人共鸣"),
    "鼠鼠": ("梗", "'鼠鼠我啊', 蝼蚁人自嘲(贴吧文化)"),
    "润": ("梗", "run, 跑路"),
    "急了": ("梗", "对方被戳中痛点的嘲讽"),
    "鉴定为": ("梗", "抽象圈鉴定腔调"),
    "老登": ("梗", "年长男性称呼, 可调侃可损"),
    "小登": ("梗", "年轻男性"),
    "开摆": ("梗", "开始摆烂"),
    "炫饭": ("梗", "大口吃饭"),
    "拷打": ("梗", "严厉质问"),
    "启动": ("梗", "'原神, 启动!'梗, 泛指开始干某事"),
    "原批": ("梗", "原神玩家戏称/自嘲"),
    "好活": ("梗", "好节目好操作(夸奖)"),
    "整活": ("梗", "搞节目效果"),
    "乐子人": ("梗", "看热闹不嫌事大的人"),
    "吃瓜": ("梗", "围观八卦"),
    "塌房": ("梗", "偶像人设崩塌"),
    "梦女": ("梗", "幻想与角色恋爱的粉丝"),
    "乙游": ("梗", "女性向恋爱游戏(恋与深空等)"),
    "主理人": ("梗", "网红开店自称, 带调侃色彩(2024)"),
    "互联网嘴替": ("梗", "替自己说出心声的人"),
    "菜就多练": ("梗", "'菜就多练, 输不起就别玩'游戏嘲讽"),
    "服了你个老六": ("梗", "老六=阴人玩家"),
    # ===== 虚拟形象/AI形象 =====
    "奶龙": ("形象", "国产黄色小龙动画IP, '我是奶龙'梗, 表情包大户"),
    "豆包": ("形象", "字节跳动的AI助手, 国民级AI应用"),
    "DeepSeek": ("形象", "国产开源大模型'深度求索', 鲸鱼logo被爱称大肥鱼, 2025爆火"),
    "大肥鱼": ("形象", "DeepSeek鲸鱼logo的爱称"),
    "Kimi": ("形象", "月之暗面的AI助手, 长文本处理出名"),
    "文小言": ("形象", "百度的AI助手(原文心一言)"),
    "通义千问": ("形象", "阿里的AI助手"),
    "智谱清言": ("形象", "智谱AI的助手, GLM模型"),
    "元宝": ("形象", "腾讯的AI助手"),
    "Loopy": ("形象", "韩国动画《小企鹅Pororo》的海狸, 凸嘴表情包爆火"),
    "吉伊卡哇": ("形象", "日本IP: 吉伊(白,爱哭)/乌萨奇(黄兔,疯)/小桃(粉,小飞鼠)"),
    "chiikawa": ("形象", "即吉伊卡哇, '小小可爱'之意"),
    "乌萨奇": ("形象", "吉伊卡哇里的黄色兔子, 疯癫可爱"),
    "线条小狗": ("形象", "韩国插画马尔济斯犬线条表情包"),
    "卡皮巴拉": ("形象", "水豚, 情绪稳定顶流动物"),
    "水豚": ("形象", "即卡皮巴拉, 淡定担当"),
    "悲伤蛙": ("形象", "Pepe绿色青蛙表情包"),
    "doge": ("形象", "神烦狗柴犬kabosu"),
    "cheems": ("形象", "咬字不清的小柴(已去世, 2024悼念潮)"),
    "玲娜贝儿": ("形象", "迪士尼粉红色小狐狸"),
    "星黛拉": ("形象", "迪士尼紫色兔子"),
    "蛋仔": ("形象", "网易《蛋仔派对》圆滚滚形象"),
    "小蓝": ("形象", "《小蓝和他的朋友》蓝色小人表情包"),
    "长草颜团子": ("形象", "头顶长草的白团子表情包"),
    "乖巧宝宝": ("形象", "经典白色小人表情包"),
    "雪王": ("形象", "蜜雪冰城雪人, '你爱我我爱你'"),
    "旺仔": ("形象", "旺旺招牌瞪眼小人"),
    "真香": ("梗", "王境泽哥: 说不要结果真香, 打脸梗"),
    "打工是不可能打工": ("梗", "窃格瓦拉周某名言"),
    "黑神话悟空": ("梗", "游戏科学的国产3A(2024), 主角称天命人"),
    "天命人": ("梗", "黑神话悟空主角称呼"),
    "哪吒2": ("梗", "2025现象级动画电影, 中国影史票房冠军"),
    "孙笑川": ("梗", "抽象文化代表主播, '带带大师兄'"),
    "电棍": ("梗", "otto电棍, 主播'欧内的手'名场面"),
    "永雏塔菲": ("梗", "虚拟主播, '关注永雏塔菲谢谢喵'"),
    "东雪莲": ("梗", "虚拟主播相关梗人物"),
    "嘉然": ("梗", "A-SOUL成员, '嘉然今天吃什么'"),
    "A-SOUL": ("梗", "虚拟偶像团体: 嘉然/向晚/贝拉/乃琳/珈乐"),
    "顶碗人": ("梗", "A-SOUL贝拉的粉丝名"),
    "芙宁娜": ("梗", "原神水神角色, 戏很多的小可爱"),
    "雷电将军": ("梗", "原神高人气角色"),
    "弈星": ("梗", "王者荣耀棋盘法师英雄"),
}


def _load():
    try:
        return json.load(open(MEME_FP, encoding="utf-8"))
    except Exception:
        return {}


def _save(d):
    json.dump(d, open(MEME_FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _masters():
    try:
        fp = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "masters.json"))
        return [str(x) for x in json.load(open(fp, encoding="utf-8")).get("masters", [])]
    except Exception:
        m = os.environ.get("MASTER_QQ", "")
        return [m] if m.isdigit() else []


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


@register("twin_meme", "wuzihang", "梗与形象知识库: 种子词典+查询/教学/自动学习", "1.0.0")
class TwinMeme(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        d = _load()
        fresh = {k: list(v) for k, v in SEED.items() if k not in d}
        if fresh:
            d.update(fresh)
            _save(d)
            print(f"[twin_meme] 装载知识库: {len(d)}条(新增种子{len(fresh)})")
        self._mined_day = ""

    # ---------- 对话注入: 命中词条带进上下文 ----------
    @filter.on_llm_request()
    async def inject(self, event: AstrMessageEvent, req: ProviderRequest):
        try:
            text = event.message_str or ""
            d = _load()
            hits = []
            for word, (typ, mean) in d.items():
                if len(word) >= 2 and word.lower() in text.lower() and word not in hits:
                    hits.append(word)
                if len(hits) >= MAX_INJECT:
                    break
            if hits:
                lines = [f"- {w}: {d[w][1]}" for w in hits]
                req.system_prompt = (req.system_prompt or "") + \
                    "\n[梗/形象词典(你懂这些, 自然使用别解释除非被问)]\n" + "\n".join(lines)
        except Exception:
            pass

    # ---------- "什么是X" 主动学习 ----------
    @filter.event_message_type(EventMessageType.ALL)
    async def on_ask(self, event: AstrMessageEvent, *args, **kwargs):
        try:
            text = (event.message_str or "").strip()
            if not text or text.startswith(("/", "!", "＃")):
                return
            word = ""
            for pat in (r"^什么是(.{1,12}?)[啊呀?？!！。]*$",
                        r"^(.{1,12}?)是什么意思[啊呀?？!！。]*$",
                        r"^(.{1,12}?)是什么[啊呀?？!！。]*$",
                        r"^(.{1,12}?)啥意思[啊呀?？!！。]*$"):
                m = re.match(pat, text)
                if m:
                    word = m.group(1).strip()
                    break
            if not word or len(word) < 2:
                return
            event.stop_event()
            d = _load()
            if word in d:
                await event.send(MessageChain(chain=[Plain(f"{word}: {d[word][1]}")]))
                return
            ans = await self._search_learn(word)
            await event.send(MessageChain(chain=[Plain(ans or f"「{word}」我也不熟, 主人教教我? /学梗 {word} 含义")]))
        except Exception:
            pass

    async def _search_learn(self, word) -> str:
        key = _tavily_key()
        if not key:
            return ""
        try:
            def _q():
                return requests.post("https://api.tavily.com/search", timeout=25, json={
                    "api_key": key, "query": f"{word} 梗 是什么意思 网络用语",
                    "max_results": 5, "search_depth": "basic"}).json()
            r = await asyncio.get_event_loop().run_in_executor(None, _q)
            snips = "\n".join(x.get("content", "")[:200] for x in r.get("results", []))
            if not snips:
                return ""
            provider = self.context.get_using_provider()
            if provider is None:
                return ""
            resp = await provider.text_chat(
                prompt=f"搜索结果:\n{snips[:2500]}\n\n据此解释网络梗/词「{word}」: 40字内说明含义和来源; "
                       "如果搜索结果和它无关就只输出'未知'。只输出解释或'未知'。",
                system_prompt="你是网络文化词典, 只输出要求的内容。")
            out = (getattr(resp, "completion_text", "") or "").strip()
            if out and "未知" not in out[:6] and len(out) >= 6:
                d = _load()
                d[word] = ["梗", out[:100]]
                _save(d)
                print(f"[twin_meme] 联网学会: {word}")
                return out
        except Exception:
            pass
        return ""

    # ---------- /梗 ----------
    @filter.command("梗")
    async def cmd_query(self, event: AstrMessageEvent, *, kw: str = ""):
        event.stop_event()
        kw = kw.strip()
        if not kw:
            await event.send(MessageChain(chain=[Plain("/梗 关键词 查询, /学梗 词 含义(主人) 教我")]))
            return
        d = _load()
        if kw in d:
            await event.send(MessageChain(chain=[Plain(f"{kw}[{d[kw][0]}]: {d[kw][1]}")]))
            return
        hits = [k for k in d if kw in k][:5]
        if hits:
            await event.send(MessageChain(chain=[Plain(
                "词典里相关的: " + "、".join(f"{k}" for k in hits))]))
        else:
            ans = await self._search_learn(kw)
            await event.send(MessageChain(chain=[Plain(ans or f"「{kw}」没查到, 主人可以 /学梗 {kw} 含义 教我")]))

    # ---------- /学梗 (主人) ----------
    @filter.command("学梗")
    async def cmd_teach(self, event: AstrMessageEvent, *, rest: str = ""):
        if str(event.get_sender_id()) not in _masters():
            event.stop_event()
            await event.send(MessageChain(chain=[Plain("这个指令只有主人能用")]))
            return
        event.stop_event()
        rest = rest.strip()
        parts = rest.split(None, 1)
        if len(parts) < 2:
            await event.send(MessageChain(chain=[Plain("格式: /学梗 词 含义")]))
            return
        word, mean = parts[0][:12], parts[1][:100]
        d = _load()
        d[word] = ["梗", mean]
        _save(d)
        await event.send(MessageChain(chain=[Plain(f"学会了: {word} = {mean}")]))

    # ---------- 每日自动挖梗 ----------
    @filter.on_llm_request()
    async def _mine_maybe(self, event: AstrMessageEvent, req: ProviderRequest):
        today = time.strftime("%Y-%m-%d")
        if self._mined_day == today:
            return
        self._mined_day = today          # 标记先置位, 防重入
        asyncio.create_task(self._mine())

    async def _mine(self):
        key = _tavily_key()
        if not key:
            return
        try:
            def _q():
                return requests.post("https://api.tavily.com/search", timeout=25, json={
                    "api_key": key, "query": "最近 网络热梗 流行语 盘点 本周",
                    "max_results": 6, "search_depth": "basic",
                    "topic": "news"}).json()
            r = await asyncio.get_event_loop().run_in_executor(None, _q)
            snips = "\n".join(x.get("content", "")[:300] for x in r.get("results", []))
            if not snips:
                return
            provider = self.context.get_using_provider()
            if provider is None:
                return
            d = _load()
            known = "、".join(list(d.keys())[-60:])
            resp = await provider.text_chat(
                prompt=f"最新资讯:\n{snips[:3000]}\n\n已知词典(不用重复): {known}\n"
                       "从资讯里提取最多8个新热梗/新词/新形象, 只输出JSON: "
                       '{"词": "40字内含义"}  没有就输出 {}',
                system_prompt="你是网络文化词典编辑, 只输出JSON。")
            raw = (getattr(resp, "completion_text", "") or "").strip()
            m = re.search(r'\{.*\}', raw, re.S)
            if not m:
                return
            new = json.loads(m.group())
            added = 0
            for k, v in new.items():
                k = str(k)[:12]
                if 2 <= len(k) <= 12 and k not in d and isinstance(v, str) and len(v) > 5:
                    d[k] = ["梗", v[:100]]
                    added += 1
            if added:
                _save(d)
                print(f"[twin_meme] 每日挖掘+{added}条新梗")
        except Exception as e:
            print(f"[twin_meme] 挖掘失败: {e}")
