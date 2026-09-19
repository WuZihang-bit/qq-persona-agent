import asyncio
import base64
import httpx, requests, json, os, re, time, uuid
from collections import Counter
from datetime import datetime

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.filter.event_message_type import EventMessageType

# ---- 配置 ----
MASTER_QQ = "10002"
BOT_UIN = "10001"
ROUTER_PROVIDER_ID = "deepseek"        # Jev式快速路由用的provider(便宜快)
ROUTER_MODEL = "deepseek-flash"
AGENT_MAX_STEPS = 8                    # 每阶段步数
AGENT_MAX_PHASES = 3                   # 长任务最多阶段数(8x3=24步)
TAVILY_KEY = "YOUR_TAVILY_KEY"
TASK_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fensheng", "tasks"))
VISION_BASE = "https://open.bigmodel.cn/api/paas/v4"
VISION_KEY = "YOUR_GLM_KEY"
VISION_MODEL = "glm-5.3-flash"

ROUTER_PROMPT = '''你是消息路由器。分析用户消息，只输出JSON（不要其他文字）：
{"need_search": bool, "search_query": "需搜索时的查询词(否则空串)", "need_serious": bool, "is_meme": bool, "agent": bool, "agent_instruction": "需Agent执行时的任务描述(否则空串)"}
判断规则：
- need_search: 涉及时事新闻/最新动态/实时数据/你不确定的具体事实
- need_serious: 用户情绪低落/严肃求助/正经事务, 需要认真专业回答
- is_meme: 含网络梗/玩笑/整活
- agent: 用户是主人且要求执行多步任务/分析群数据/长进程工作(如"分析群聊""统计谁最活跃""调研并汇报")。闲聊/提问一律false'''

AGENT_SYSTEM = '''你是吴子航的Agent执行器，替他完成多步任务。可用工具：
{"tool":"web_search","args":{"query":"..."}}        # 联网搜索
{"tool":"list_groups"}                              # 列出bot所在的群
{"tool":"get_group_history","args":{"group_id":123,"count":100}}   # 拉取群最近消息
{"tool":"analyze_group_activity","args":{"group_id":123,"count":200}}  # 统计群活跃度(谁话多/时段/高频词)
{"tool":"send_group_message","args":{"group_id":123,"text":"..."}}
{"tool":"send_private_message","args":{"qq":123,"text":"..."}}
{"tool":"save_note","args":{"text":"..."}}          # 保存备忘
{"tool":"list_tasks","args":{"status":"全部|执行中|挂起|完成"}}  # 查询历史任务列表和状态
{"tool":"send_forward","args":{"type":"private|group","id":123,"items":["段1","段2"],"sender_name":"吴子航"}}  # 打包多条为合并转发消息发出(整理群消息/汇总报告时用)
{"tool":"daily_digest","args":{"group_id":123,"days":1}}    # 生成群聊日报(自动拉历史+AI总结), days默认1

每步只输出一个JSON对象：
{"thought":"简短思考","tool":"工具名","args":{...}}
任务完成时输出：
{"final":"给主人的最终汇报"}
规则：步数宝贵，每步做实在的事；需要多角度就多次调用工具；给主人的汇报要口语化、有结论。'''


@register("twin_brain", "your-name", "Jev式路由 + 主人专属Agent引擎", "1.0.0")
class TwinBrain(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        os.makedirs(TASK_DIR, exist_ok=True)

    # ---------- 基础 ----------
    def _fast_llm(self, prompt: str, system: str = "", smart: bool = False):
        async def _run():
            try:
                if smart:
                    p = self.context.get_using_provider()          # Agent执行: GLM主模型
                    model = None
                else:
                    p = await self.context.get_provider_by_id(ROUTER_PROVIDER_ID)
                    p = p or self.context.get_using_provider()
                    model = ROUTER_MODEL if p.id == ROUTER_PROVIDER_ID else None
                r = await p.text_chat(prompt=prompt, system_prompt=system, model=model)
                return (getattr(r, "completion_text", "") or "").strip()
            except Exception:
                return ""
        return _run

    def _tavily(self, query: str) -> str:
        try:
            r = requests.post('https://api.tavily.com/search',
                              headers={'Authorization': 'Bearer ' + TAVILY_KEY},
                              json={'query': query, 'max_results': 5, 'search_depth': 'advanced'}, timeout=30)
            out = []
            for x in r.json().get('results', [])[:5]:
                out.append(f"- {x.get('title','')}\n  {x.get('url','')}\n  {x.get('content','')[:300]}")
            return '\n'.join(out) if out else '无结果'
        except Exception as e:
            return f'搜索失败: {e}'

    # ---------- 视觉代理: 图片先转录成文字, 绕开主模型送审被拦 ----------
    def _describe_image_sync(self, url: str):
        try:
            p = None
            if url.startswith("file:///"):
                p = url[8:]
            elif url.startswith("file://"):
                p = url[7:]
            elif not url.startswith("http"):
                p = url
            if p is not None:
                if not os.path.exists(p):
                    return None
                data = open(p, "rb").read()
            else:
                data = requests.get(url, timeout=20).content
            b64 = base64.b64encode(data).decode()
            mime = "gif" if data.startswith(b"GIF") else ("png" if data[:4] == bytes([0x89,0x50,0x4E,0x47]) else "jpeg")
            r = requests.post(VISION_BASE + "/chat/completions",
                headers={"Authorization": "Bearer " + VISION_KEY},
                json={"model": VISION_MODEL, "temperature": 0.1, "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64," + b64}},
                    {"type": "text", "text": "请完整转录图片中的所有文字内容（保持原顺序和数字符号）。如果图中没有文字，用一句话描述画面。只输出转录/描述本身。"}]}]},
                timeout=60)
            return r.json()["choices"][0]["message"]["content"].strip()[:1500]
        except Exception:
            return None

    async def _proxy_vision(self, req: ProviderRequest):
        urls = list(req.image_urls or [])
        if not urls:
            return
        descs, kept = [], []
        for url in urls:
            d = await asyncio.get_event_loop().run_in_executor(None, self._describe_image_sync, url)
            if d:
                descs.append(d)
            else:
                kept.append(url)
        if descs:
            req.prompt = (req.prompt or "") + "\n[图片内容转录]\n" + "\n---\n".join(descs)
            req.image_urls = kept

    # ---------- 合并转发展开 ----------
    async def _expand_forward(self, event):
        try:
            bot = getattr(event, "bot", None)
            if bot is None:
                return None
            for comp in event.message_obj.message:
                if type(comp).__name__ == "Forward" and getattr(comp, "id", ""):
                    try:
                        r = await bot.call_action("get_forward_msg", {"message_id": comp.id})
                    except Exception:
                        r = await bot.call_action("get_forward_msg", {"id": comp.id})
                    msgs = r.get("messages") if isinstance(r, dict) else (r if isinstance(r, list) else [])
                    lines = []
                    for m in (msgs or [])[:50]:
                        name = (m.get("sender") or {}).get("nickname", "?")
                        txt = str(m.get("raw_message", "") or m.get("content", ""))[:150]
                        t = m.get("time", 0)
                        try:
                            ts = datetime.fromtimestamp(int(t)).strftime("%m-%d %H:%M")
                        except Exception:
                            ts = ""
                        lines.append(f"{name}({ts}): {txt}")
                    return "\n".join(lines)[:3000] if lines else None
        except Exception:
            return None
        return None

    # ---------- Jev式路由 ----------
    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        try:
            if req.image_urls:
                await self._proxy_vision(req)
            # 合并转发展开: Forward组件 -> get_forward_msg -> 文字注入
            try:
                fwd_desc = await self._expand_forward(event)
                if fwd_desc:
                    tag = "\n[合并转发的聊天记录内容]\n"
                    req.prompt = (req.prompt or "") + tag + fwd_desc
            except Exception:
                pass
            user_text = (req.prompt or "").strip()
            if not user_text or user_text.startswith('/'):
                return
            if len(user_text) <= 6 and "分析" not in user_text and "调研" not in user_text:
                return  # 超短寒暄跳过路由, 降低延迟
            is_master = str(event.get_sender_id()) == MASTER_QQ
            # 路由决策(快速模型)
            run = self._fast_llm(f"用户消息: {user_text[:500]}\n(主人发消息={is_master})", ROUTER_PROMPT)
            raw = await run()
            m = re.search(r'\{.*\}', raw, re.S)
            if not m:
                return
            try:
                d = json.loads(m.group())
            except Exception:
                return

            # 主人专属: Agent任务接管
            if is_master and d.get("agent") and d.get("agent_instruction"):
                event.stop_event()
                instruction = d["agent_instruction"].strip()
                gid = event.get_group_id()
                await event.send(MessageChain(chain=[Plain(f"收到 开始干活: {instruction}\n(后台执行 完成后汇报)")]))
                asyncio.create_task(self._run_agent(event, instruction, gid))
                return

            # 普通消息: 注入风格与资料
            hints = []
            if d.get("need_serious"):
                hints.append("本轮用户需要认真专业的回答: 收起玩笑和梗, 直接给干货。")
            if d.get("is_meme"):
                hints.append("本轮消息含梗: 可以玩梗接梗, 放松一点。")
            if d.get("need_search") and d.get("search_query"):
                res = self._tavily(d["search_query"])
                hints.append(f"[实时资料-仅供你参考回答时使用]\n{res[:2500]}")
            if hints:
                req.system_prompt = (req.system_prompt or "") + "\n" + "\n".join(hints)
        except Exception:
            pass  # 路由失败不影响正常聊天

    # ---------- 主人"继续任务"恢复 ----------
    @filter.event_message_type(EventMessageType.PRIVATE_MESSAGE)
    async def on_private(self, event: AstrMessageEvent, *args, **kwargs):
        try:
            if str(event.get_sender_id()) != MASTER_QQ:
                return
            t = (event.message_str or "").strip()
            if not t.startswith("继续任务"):
                return
            pending = [f for f in os.listdir(TASK_DIR) if f.endswith('.json')]
            latest, latest_t = None, 0
            for f in pending:
                fp = os.path.join(TASK_DIR, f)
                st = json.load(open(fp, encoding='utf-8'))
                if st.get("status") == "挂起" and st.get("updated", 0) > latest_t:
                    latest, latest_t = st, st["updated"]
            if not latest:
                await event.send(MessageChain(chain=[Plain("没有挂起的任务")]))
                event.stop_event()
                return
            await event.send(MessageChain(chain=[Plain(f"恢复任务: {latest['instruction']}")]))
            event.stop_event()
            asyncio.create_task(self._run_agent(event, latest["instruction"], latest.get("group_id"), resume=latest["id"]))
        except Exception:
            pass

    # ---------- Agent引擎 ----------
    async def _run_agent(self, event, instruction: str, group_id, resume: str = None):
        task_id = resume or uuid.uuid4().hex[:8]
        state = {"id": task_id, "instruction": instruction, "group_id": group_id,
                 "status": "执行中", "steps": [], "updated": time.time()}
        if resume:
            old_fp = os.path.join(TASK_DIR, f"{resume}.json")
            if os.path.exists(old_fp):
                old = json.load(open(old_fp, encoding='utf-8'))
                state["steps"] = old.get("steps", [])
        fp = os.path.join(TASK_DIR, f"{task_id}.json")
        json.dump(state, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

        bot = getattr(event, "bot", None)

        # ---- 工具实现 ----
        async def tool_call(name, args):
            try:
                if name == "web_search":
                    return await asyncio.get_event_loop().run_in_executor(None, self._tavily, args.get("query", ""))
                if name == "list_groups":
                    r = await bot.call_action("get_group_list")
                    return json.dumps([{"group_id": g.get("group_id"), "name": g.get("group_name")} for g in r], ensure_ascii=False)
                if name == "get_group_history":
                    gid = int(args.get("group_id") or group_id or 0)
                    n = min(int(args.get("count", 50)), 300)
                    try:
                        r = await bot.call_action("get_group_msg_history", {"group_id": gid, "count": n})
                    except Exception:
                        r = await bot.call_action("get_chat_history", {"group_id": gid, "count": n})
                    msgs = (r.get("messages") if isinstance(r, dict) else r) or []
                    lines = []
                    for msg in msgs[-n:]:
                        s = msg.get("sender", {}).get("nickname", "?")
                        raw = msg.get("raw_message", "") or ""
                        lines.append(f"{s}: {str(raw)[:80]}")
                    return "\n".join(lines)[-3000:]
                if name == "analyze_group_activity":
                    gid = int(args.get("group_id") or group_id or 0)
                    n = min(int(args.get("count", 200)), 500)
                    try:
                        r = await bot.call_action("get_group_msg_history", {"group_id": gid, "count": n})
                    except Exception:
                        r = await bot.call_action("get_chat_history", {"group_id": gid, "count": n})
                    msgs = (r.get("messages") if isinstance(r, dict) else r) or []
                    senders, hours, words = Counter(), Counter(), Counter()
                    for msg in msgs:
                        senders[msg.get("sender", {}).get("nickname", "?")] += 1
                        try:
                            hours[datetime.fromtimestamp(int(msg.get("time", 0))).hour] += 1
                        except Exception:
                            pass
                        for w in re.findall(r'[\u4e00-\u9fff]{2,4}', str(msg.get("raw_message", ""))):
                            words[w] += 1
                    top = "、".join(f"{w}({c})" for w, c in words.most_common(10))
                    return (f"样本{len(msgs)}条 | 话痨榜: " +
                            "、".join(f"{k}({v}条)" for k, v in senders.most_common(8)) +
                            f" | 高频词: {top}")
                if name == "send_group_message":
                    await bot.call_action("send_group_msg", {"group_id": int(args["group_id"]),
                                                             "message": [{"type": "text", "data": {"text": args["text"]}}]})
                    return "已发送"
                if name == "send_private_message":
                    await bot.call_action("send_private_msg", {"user_id": int(args["qq"]),
                                                               "message": [{"type": "text", "data": {"text": args["text"]}}]})
                    return "已发送"
                if name == "send_forward":
                    # 打包多段文字为合并转发消息发给指定对象
                    target_type = args.get("type", "private")
                    tid = int(args.get("id", 0))
                    items = args.get("items", [])
                    nodes = []
                    for it in items[:50]:
                        nodes.append({"type": "node", "data": {
                            "uin": str(BOT_UIN), "name": args.get("sender_name", "吴子航"),
                            "content": [{"type": "text", "data": {"text": str(it)[:800]}}]}})
                    payload = {"messages": nodes}
                    if target_type == "group":
                        payload["group_id"] = tid
                    else:
                        payload["user_id"] = tid
                    try:
                        await bot.call_action("send_group_forward_msg" if target_type == "group" else "send_private_forward_msg", payload)
                        return f"已打包{len(nodes)}条合并转发发出"
                    except Exception:
                        # 降级: 逐条发送
                        fn = "send_group_msg" if target_type == "group" else "send_private_msg"
                        for it in items[:20]:
                            await bot.call_action(fn, {"group_id" if target_type == "group" else "user_id": tid,
                                                       "message": [{"type": "text", "data": {"text": str(it)[:800]}}]})
                            await asyncio.sleep(1)
                        return f"合并接口不可用, 已降级逐条发送{min(len(items),20)}条"
                if name == "list_tasks":
                    status = args.get("status", "全部")
                    out = []
                    for f in sorted(os.listdir(TASK_DIR)):
                        if not f.endswith(".json"):
                            continue
                        try:
                            t = json.load(open(os.path.join(TASK_DIR, f), encoding="utf-8"))
                        except Exception:
                            continue
                        if status == "全部" or t.get("status") == status:
                            out.append(f"[{t.get('id')}] {t.get('instruction', '')[:40]} — {t.get('status')}({len(t.get('steps', []))}步)")
                    jn = "\n"
                    return jn.join(out[-15:]) if out else "没有任务记录"
                if name == "daily_digest":
                    from daily_digest import generate as gen_digest
                    gid = int(args.get("group_id") or group_id or 0)
                    days = int(args.get("days", 1))
                    async def _llm(prompt, system):
                        r = await (self._fast_llm(prompt, system, smart=True))()
                        return r or "总结生成失败"
                    report = await gen_digest(bot, gid, days, _llm)
                    return report[-2500:] if report else "当天没有消息"
                if name == "save_note":
                    with open(os.path.join(TASK_DIR, "..", "notes.md"), "a", encoding="utf-8") as f:
                        f.write(f"- [{datetime.now():%m-%d %H:%M}] {args.get('text','')}\n")
                    return "已保存"
                return f"未知工具: {name}"
            except Exception as e:
                return f"工具出错: {e}"

        def parse_step(text):
            m = re.search(r'\{.*\}', text, re.S)
            if not m:
                return None
            try:
                return json.loads(m.group())
            except Exception:
                return None

        # ---- 主循环: 多阶段长进程 ----
        ctx_lines = [f"任务: {instruction}"]
        if group_id:
            ctx_lines.append(f"(触发群ID: {group_id})")
        if state["steps"]:
            ctx_lines.append("之前已完成步骤: " + json.dumps(state["steps"][-6:], ensure_ascii=False))

        for phase in range(AGENT_MAX_PHASES):
            remaining = AGENT_MAX_STEPS
            final = None
            for step in range(remaining):
                prompt = "\n".join(ctx_lines) + f"\n\n历史步骤:\n" + json.dumps(state["steps"], ensure_ascii=False) + \
                         f"\n\n继续执行下一步(这是第{phase+1}阶段第{step+1}步)。"
                run = self._fast_llm(prompt, AGENT_SYSTEM, smart=True)
                raw = await run()
                if not raw:
                    raw = ""
                d = parse_step(raw) or {}
                if "final" in d:
                    final = d["final"]
                    break
                tool, args = d.get("tool"), d.get("args", {})
                if not tool:
                    continue
                result = await tool_call(tool, args)
                state["steps"].append({"thought": d.get("thought", ""), "tool": tool,
                                       "args": args, "result": str(result)[:400]})
                json.dump(state, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

            if final:
                state.update({"status": "完成", "final": final, "updated": time.time()})
                json.dump(state, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
                await event.send(MessageChain(chain=[Plain(f"任务完成 [{task_id}]\n{final[:800]}")]))
                return
            # 阶段收尾: 汇报进度, 询问是否继续
            state.update({"status": "挂起", "updated": time.time()})
            json.dump(state, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            done_summary = " → ".join(s["tool"] for s in state["steps"][-AGENT_MAX_STEPS:]) or "无"
            await event.send(MessageChain(chain=[Plain(
                f"[{task_id}] 阶段{phase+1}完成, 已执行{len(state['steps'])}步: {done_summary}\n"
                + (f"还需继续, 私聊我'继续任务'推进下一阶段" if phase < AGENT_MAX_PHASES - 1 else "达到阶段上限, 详情让我汇报"))]))
            return  # 等主人说"继续任务"再推进(避免深夜狂发)
