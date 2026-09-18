import asyncio
import base64
import io
import json
import time
from datetime import datetime
from pathlib import Path

import httpx
from PIL import Image as PILImage

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image as ImageComp
from astrbot.api.message_components import Plain

ROOT = Path(__file__).resolve().parents[2] / "fensheng"
EMOJI_DIR = ROOT / "emojis"
QUARANTINE = EMOJI_DIR / "_quarantine"
INDEX_FILE = EMOJI_DIR / "index.json"
CONFIG_FILE = ROOT / "config.json"
LOG_DIR = ROOT / "logs"


def ahash(img: PILImage.Image, size: int = 8) -> str:
    """平均哈希，用于感知去重"""
    g = img.convert("L").resize((size, size), PILImage.LANCZOS)
    px = list(g.getdata())
    avg = sum(px) / len(px)
    return "".join("1" if p > avg else "0" for p in px)


def hamming(a: str, b: str) -> int:
    return sum(c1 != c2 for c1, c2 in zip(a, b))


def compress_to_data_url(data: bytes, max_px: int = 512) -> str:
    img = PILImage.open(io.BytesIO(data))
    img.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


TAG_PROMPT = """你是表情包标注员。看这张表情包，只输出一个JSON对象（不要markdown代码块），字段如下：
{"is_meme": true或false, "desc": "画面内容一句话", "text": "图上叠加的文字，没有则为空字符串", "emotion": "主情绪，从这些选：开心/大笑/赞同/无语/不屑/阴阳怪气/愤怒/悲伤/摆烂/害羞/惊讶/疑惑/可爱/卖萌/其他", "scene": "什么聊天场景适合发这张图（一句话，口语化）", "keywords": ["关键词1", "关键词2", "关键词3"], "suitable": true或false}
说明：is_meme=false 表示这不是表情包（是自拍/照片/聊天截图等）；suitable=false 表示内容含色情/政治敏感/血腥/严重辱骂或泄露他人隐私，不适合再被转发。"""


class EmojiHub(star.Star):
    """表情包中心：收集群友发的图 → 感知去重 → VLM 理解打标 → 入库/隔离"""

    def __init__(self, context: star.Context):
        self.context = context
        EMOJI_DIR.mkdir(parents=True, exist_ok=True)
        QUARANTINE.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.cfg = self._load_cfg()
        self.lock = asyncio.Lock()
        self.sem = asyncio.Semaphore(2)
        self.pending: set[str] = set()
        self.hashes: list[str] = []
        self.index: list[dict] = []
        self._load_index()

    # ---------- 数据 ----------
    def _load_cfg(self) -> dict:
        if CONFIG_FILE.exists():
            try:
                return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"[emoji_hub] config.json 解析失败: {e}")
        return {"vlm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4v-flash", "api_key": ""}}

    def _load_index(self) -> None:
        if INDEX_FILE.exists():
            try:
                self.index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.index = []
        self.index = [e for e in self.index if e.get("file") != "示例.jpg"]
        self.hashes = [e.get("phash", "") for e in self.index if e.get("phash")]
        logger.info(f"[emoji_hub] 表情包库已加载: {len(self.index)} 张")

    def _save_index(self) -> None:
        INDEX_FILE.write_text(json.dumps(self.index, ensure_ascii=False, indent=1), encoding="utf-8")

    def _daily_count(self) -> int:
        f = LOG_DIR / f"collect_{datetime.now():%Y%m%d}.count"
        return int(f.read_text()) if f.exists() else 0

    def _bump_daily(self) -> None:
        f = LOG_DIR / f"collect_{datetime.now():%Y%m%d}.count"
        f.write_text(str(self._daily_count() + 1))

    # ---------- 消息监听 ----------
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        for comp in event.message_obj.message:
            if isinstance(comp, ImageComp) and comp.url:
                url = str(comp.url)
                if url in self.pending:
                    continue
                self.pending.add(url)
                asyncio.create_task(self._collect(url))

    # ---------- 收集管线 ----------
    async def _collect(self, url: str) -> None:
        try:
            limit = int(self.cfg.get("emoji", {}).get("daily_collect_limit", 100))
            if self._daily_count() >= limit:
                return
            data = await self._download(url)
            if not data or len(data) > int(self.cfg.get("emoji", {}).get("max_kb", 2048)) * 1024:
                return
            if data[:6] not in (b"\x89NPNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"GIF87a", b"GIF89a") and not data.startswith(b"\xff\xd8"):
                return
            try:
                img = PILImage.open(io.BytesIO(data))
                img.verify()
                img = PILImage.open(io.BytesIO(data))
            except Exception:
                return
            h = ahash(img)
            if any(hamming(h, old) <= int(self.cfg.get("emoji", {}).get("phash_dist", 5)) for old in self.hashes):
                return

            tag = await self._tag_image(compress_to_data_url(data))
            if not tag:
                return
            if not tag.get("is_meme", False):
                logger.debug(f"[emoji_hub] 非表情包，跳过: {tag.get('desc', '')[:30]}")
                return
            ext = ".gif" if data.startswith(b"GIF") else ".jpg"
            fname = f"{int(time.time())}_{abs(hash(url)) % 10000:04d}{ext}"
            if not tag.get("suitable", False):
                (QUARANTINE / fname).write_bytes(data)
                logger.warning(f"[emoji_hub] 不适宜内容已隔离: {fname}")
                return

            (EMOJI_DIR / fname).write_bytes(data)
            entry = {
                "file": fname,
                "phash": h,
                "desc": tag.get("desc", "")[:80],
                "text": tag.get("text", "")[:60],
                "emotion": tag.get("emotion", "其他"),
                "scene": tag.get("scene", "")[:80],
                "keywords": [k[:12] for k in tag.get("keywords", [])[:5]],
                "source": "collect",
                "time": datetime.now().isoformat(timespec="seconds"),
            }
            async with self.lock:
                self.index.append(entry)
                self.hashes.append(h)
                self._save_index()
                self._bump_daily()
            logger.info(f"[emoji_hub] 入库 {fname} | {entry['emotion']} | {entry['desc'][:30]}")
        except Exception as e:
            logger.error(f"[emoji_hub] 收集失败: {e}")
        finally:
            self.pending.discard(url)

    async def _download(self, url: str) -> bytes | None:
        try:
            # NapCat 未开 enableLocalFile2Url 时, 图片是本地路径, 直接读文件
            if url and not url.startswith(("http://", "https://")):
                p = url
                if p.startswith("file:///"):
                    p = p[8:]
                elif p.startswith("file://"):
                    p = p[7:]
                fp = Path(p)
                if fp.exists():
                    return fp.read_bytes()
                logger.warning(f"[emoji_hub] 本地文件不存在: {p}")
                return None
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
                r = await c.get(url)
                r.raise_for_status()
                return r.content
        except Exception as e:
            logger.warning(f"[emoji_hub] 下载失败 {url[:60]}: {e}")
            return None

    async def _tag_image(self, data_url: str) -> dict | None:
        vlm = self.cfg.get("vlm", {})
        if not vlm.get("api_key") or "填" in vlm.get("api_key", "填"):
            return None
        payload = {
            "model": vlm.get("model", "glm-4v-flash"),
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": TAG_PROMPT},
            ]}],
        }
        try:
            async with self.sem:
                async with httpx.AsyncClient(timeout=60) as c:
                    r = await c.post(
                        f"{vlm['base_url'].rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {vlm['api_key']}"},
                        json=payload,
                    )
                    r.raise_for_status()
                    text = r.json()["choices"][0]["message"]["content"]
            m = text[text.find("{"): text.rfind("}") + 1]
            return json.loads(m)
        except Exception as e:
            logger.warning(f"[emoji_hub] VLM打标失败: {e}")
            return None

    # ---------- 管理命令 ----------
    @filter.command("表情包库")
    async def stats(self, event: AstrMessageEvent):
        emo_count: dict[str, int] = {}
        for e in self.index:
            emo_count[e.get("emotion", "?")] = emo_count.get(e.get("emotion", "?"), 0) + 1
        top = sorted(emo_count.items(), key=lambda x: -x[1])[:6]
        lines = [f"表情包库：{len(self.index)} 张（今日收集 {self._daily_count()}）"]
        if top:
            lines.append("情绪分布：" + "、".join(f"{k}{v}" for k, v in top))
        lines.append("用「找表情 关键词」试试检索")
        await event.send(MessageChain([Plain("\n".join(lines))]))

    @filter.command("找表情")
    async def find(self, event: AstrMessageEvent, keyword: str = ""):
        if not keyword:
            await event.send(MessageChain([Plain("用法：找表情 关键词（如 无语 / 猫 / 就这）")]))
            return
        hits = self.search(keyword)
        if not hits:
            await event.send(MessageChain([Plain(f"库里没有匹配「{keyword}」的图")]))
            return
        path = EMOJI_DIR / hits[0]["file"]
        if path.exists():
            await event.send(MessageChain([ImageComp(file=str(path))]))
        await event.send(MessageChain([Plain(f"命中{len(hits)}张：{hits[0]['desc']}（{hits[0]['emotion']}）场景：{hits[0]['scene']}")]))

    def search(self, keyword: str, top_n: int = 5) -> list[dict]:
        kw = keyword.lower()

        def score(e: dict) -> int:
            blob = " ".join([e.get("desc", ""), e.get("text", ""), e.get("emotion", ""), e.get("scene", ""), " ".join(e.get("keywords", []))]).lower()
            s = 0
            if kw in e.get("keywords", []):
                s += 10
            if kw == e.get("emotion", ""):
                s += 8
            if kw in blob:
                s += 4
            return s

        ranked = sorted((e for e in self.index if score(e) > 0), key=score, reverse=True)
        return ranked[:top_n]
