# -*- coding: utf-8 -*-
"""RAG 知识库: 把文档切片建索引, 关键词+TF交叉评分检索
文档目录: data/fensheng/knowledge/ 下所有 .md/.txt
"""
import json, os, re
from collections import Counter

KB_DIR = r'F:\yuanfei_skill\bot\data\fensheng\knowledge'
INDEX_FP = os.path.join(KB_DIR, '_index.json')
CHUNK = 350          # 每片字符数
OVERLAP = 60

def build_index():
    """全量重建索引: 文档->切片->词频表"""
    os.makedirs(KB_DIR, exist_ok=True)
    chunks = []
    for root, _, files in os.walk(KB_DIR):
        for fn in files:
            if not fn.endswith(('.md', '.txt')) or fn.startswith('_'):
                continue
            fp = os.path.join(root, fn)
            try:
                text = open(fp, encoding='utf-8').read()
            except Exception:
                continue
            text = re.sub(r'\n{3,}', '\n\n', text)
            step = CHUNK - OVERLAP
            for i in range(0, len(text), step):
                piece = text[i:i + CHUNK].strip()
                if len(piece) < 40:
                    continue
                # 中文分词: 2字滑窗+英文单词
                words = set(re.findall(r'[\u4e00-\u9fff]{2,4}|[a-zA-Z]{3,}', piece))
                chunks.append({'doc': os.path.relpath(fp, KB_DIR), 'off': i,
                               'text': piece, 'tf': {w: 1 for w in words}})
    json.dump(chunks, open(INDEX_FP, 'w', encoding='utf-8'), ensure_ascii=False)
    return len(chunks)

def search(query: str, top_n: int = 3):
    """检索: 与查询词重叠度最高的切片"""
    if not os.path.exists(INDEX_FP):
        build_index()
    chunks = json.load(open(INDEX_FP, encoding='utf-8'))
    qwords = set(re.findall(r'[\u4e00-\u9fff]{2,4}|[a-zA-Z]{3,}', query))
    scored = []
    for c in chunks:
        overlap = len(qwords & set(c['tf'].keys()))
        if overlap:
            scored.append((overlap, c))
    scored.sort(key=lambda x: -x[0])
    return [c['text'] for _, c in scored[:top_n]]

if __name__ == '__main__':
    import sys
    n = build_index()
    print(f'索引构建完成: {n} 个切片')
    if len(sys.argv) > 1:
        for t in search(' '.join(sys.argv[1:])):
            print('---', t[:120])
