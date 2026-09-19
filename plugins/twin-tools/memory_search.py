# -*- coding: utf-8 -*-
"""记忆检索升级: 多关键词+同义词扩展+时间衰减评分
替换twin_tools里的简单匹配, 提升召回率
"""
import json, os, re
from datetime import datetime

MEM_DIR = r'F:\yuanfei_skill\bot\data\fensheng\memory'

# 轻量同义词表(可自行扩充)
SYNONYMS = {
    '比赛': ['比赛', '球赛', '打球', '羽毛球', '篮球', '乒乓', '竞赛'],
    '面试': ['面试', '招聘', 'offer', '简历'],
    '考试': ['考试', '期末', '测验', '挂科', '绩点'],
    '生日': ['生日', '生辰', '过生'],
    '吃': ['吃', '饭', '外卖', '食堂', '探店', '好吃'],
    '游戏': ['游戏', '王者', '开黑', '上分', '打游戏'],
    '学习': ['学习', '复习', '作业', '课程', '上课', '写代码'],
}

def expand(kw: str):
    words = {kw}
    for base, syns in SYNONYMS.items():
        if kw in base or kw in syns:
            words.update(syns)
            words.add(base)
    return words

def search(about: str, top_n: int = 8):
    """返回与about相关的记忆文本列表(按相关度+新鲜度排序)"""
    kws = expand(about)
    hits = []
    for f in os.listdir(MEM_DIR):
        try:
            d = json.load(open(os.path.join(MEM_DIR, f), encoding='utf-8'))
        except Exception:
            continue
        for fact in d.get('facts', []):
            text = fact.get('text', '')
            score = sum(1 for kw in kws if kw in text)
            if score == 0:
                continue
            # 时间衰减: 30天内满时间分, 越老越低
            try:
                age = (datetime.now() - datetime.fromisoformat(fact.get('time', ''))).days
                fresh = max(0.0, 1.0 - age / 60.0)
            except Exception:
                fresh = 0.5
            hits.append((score * 2 + fresh, text))
    hits.sort(key=lambda x: -x[0])
    return [t for _, t in hits[:top_n]]

if __name__ == '__main__':
    import sys
    print(search(sys.argv[1] if len(sys.argv) > 1 else '比赛'))
