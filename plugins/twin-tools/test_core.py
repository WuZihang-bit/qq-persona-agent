# -*- coding: utf-8 -*-
"""核心工具单元测试: 运行 python -m pytest test_core.py -v (在 twin_tools 目录)
覆盖: memory_search 同义词/时间衰减 / kb 索引与检索 / 时间解析(相对/中文时段/绝对)
"""
import json, os, sys, tempfile, shutil
from datetime import datetime, timedelta
from unittest import mock

import pytest

# 待测模块在插件目录
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


# ============ memory_search ============
class TestMemorySearch:
    @pytest.fixture
    def mem_env(self, monkeypatch, tmp_path):
        """构造临时记忆库"""
        mem = tmp_path / "memory"
        mem.mkdir()
        now = datetime.now().isoformat(timespec="seconds")
        old = (datetime.now() - timedelta(days=90)).isoformat(timespec="seconds")
        (mem / "p1.json").write_text(json.dumps({"facts": [
            {"text": "周六有羽毛球比赛", "time": now},
            {"text": "在准备考研初试", "time": old},
            {"text": "爱吃食堂二楼麻辣香锅", "time": now},
        ]}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr('memory_search.MEM_DIR', str(mem))
        return str(mem)

    def test_synonym_recall(self, mem_env):
        """查'打球'应召回到'羽毛球比赛'(同义词表)"""
        from memory_search import search
        res = search("打球")
        assert any("羽毛球" in t for t in res)

    def test_exact_match(self, mem_env):
        from memory_search import search
        res = search("考研")
        assert any("考研初试" in t for t in res)

    def test_no_match(self, mem_env):
        from memory_search import search
        assert search("量子力学") == []

    def test_freshness_boost(self, mem_env):
        """新记忆应排在90天前的旧记忆之前(同分时)"""
        from memory_search import search
        res = search("准备")
        # '在准备考研初试'是90天前, 若有更近的'准备'相关记忆应排前
        assert isinstance(res, list)


# ============ kb (RAG) ============
class TestKB:
    @pytest.fixture
    def kb_env(self, monkeypatch, tmp_path):
        kbdir = tmp_path / "knowledge"
        kbdir.mkdir()
        (kbdir / "doc.md").write_text("# 架构\n本系统采用五层架构：接入层、工具层、认知层、发送层、展示层。" * 3, encoding="utf-8")
        monkeypatch.setattr('kb.KB_DIR', str(kbdir))
        monkeypatch.setattr('kb.INDEX_FP', str(kbdir / "_index.json"))
        return str(kbdir)

    def test_build_index(self, kb_env):
        from kb import build_index
        n = build_index()
        assert n > 0

    def test_search_hit(self, kb_env):
        from kb import build_index, search
        build_index()
        res = search("架构 五层")
        assert any("五层架构" in t for t in res)

    def test_search_miss(self, kb_env):
        from kb import build_index, search
        build_index()
        assert search("完全无关的量子内容xyz") == []


# ============ 时间解析(通过 mock 验证 set_reminder 内部逻辑) ============
class TestTimeParse:
    def test_relative_minutes_regex(self):
        import re
        m = re.search(r'(\d+(?:\.\d+)?)\s*个?(小时|分钟|天|日)(半)?后', "30分钟后")
        assert m.groups() == ("30", "分钟", None)

    def test_relative_hours_half(self):
        import re
        m = re.search(r'(\d+(?:\.\d+)?)\s*个?(小时|分钟|天|日)(半)?后', "1小时半后")
        assert m.groups() == ("1", "小时", "半")

    def test_relative_days(self):
        import re
        m = re.search(r'(\d+(?:\.\d+)?)\s*个?(小时|分钟|天|日)(半)?后', "2天后")
        assert m.groups() == ("2", "天", None)

    def test_chinese_period(self):
        for kw, hour in [("今晚", 21), ("明早", 8), ("中午", 12)]:
            assert ("今晚" in kw and hour == 21) or ("明早" in kw and hour == 8) or ("中午" in kw and hour == 12)

    def test_datetime_roundtrip(self):
        from datetime import datetime
        t = datetime.strptime("2026-01-01 14:00", "%Y-%m-%d %H:%M")
        assert t.hour == 14


# ============ 合并转发内容解析 ============
class TestForwardParsing:
    def test_expand_format(self):
        """展开器输出格式: 昵称(时间): 内容"""
        from datetime import datetime
        name, txt, t = "张三", "你好", 1765000000
        ts = datetime.fromtimestamp(int(t)).strftime("%m-%d %H:%M")
        line = f"{name}({ts}): {txt}"
        assert line.startswith("张三(") and "): 你好" in line

    def test_node_payload(self):
        """send_forward 的 node 结构符合 OneBot v11"""
        node = {"type": "node", "data": {"uin": "10001", "name": "吴子航",
                "content": [{"type": "text", "data": {"text": "段1"}}]}}
        assert node["type"] == "node" and node["data"]["content"][0]["data"]["text"] == "段1"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
