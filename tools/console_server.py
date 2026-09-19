# -*- coding: utf-8 -*-
"""Eidolon 控制台: 零依赖可视化面板
python console_server.py [port]  ->  http://localhost:8900
展示: 消息统计 / 记忆库 / 任务列表 / 提醒与跟进 / 表情包库 / 系统状态
"""
import json, os, re, glob
from http.server import HTTPServer, BaseHTTPRequestHandler

DATA_DIR = r'F:\yuanfei_skill\bot\data\fensheng'
KB_INDEX = r'F:\yuanfei_skill\bot\data\fensheng\knowledge\_index.json'
MSG_JSONL = r'F:\yuanfei_skill\work\my_messages.jsonl'


def get_stats():
    out = {"memory_files": 0, "memory_facts": 0, "tasks": [], "reminders": [],
           "followups": [], "emoji_count": 0, "kb_chunks": 0, "hour_hist": {}, "total_msgs": 0}
    # 记忆
    mem_dir = os.path.join(DATA_DIR, 'memory')
    if os.path.isdir(mem_dir):
        for f in os.listdir(mem_dir):
            if f.endswith('.json'):
                out["memory_files"] += 1
                try:
                    out["memory_facts"] += len(json.load(open(os.path.join(mem_dir, f), encoding='utf-8')).get('facts', []))
                except Exception:
                    pass
    # 任务
    td = os.path.join(DATA_DIR, 'tasks')
    if os.path.isdir(td):
        for f in os.listdir(td):
            if f.endswith('.json'):
                try:
                    t = json.load(open(os.path.join(td, f), encoding='utf-8'))
                    out["tasks"].append({"id": t.get('id'), "instruction": t.get('instruction', '')[:50],
                                         "status": t.get('status'), "steps": len(t.get('steps', []))})
                except Exception:
                    pass
    # 提醒/跟进
    for key, name in [('reminders.json', 'reminders'), ('followups.json', 'followups')]:
        fp = os.path.join(DATA_DIR, key)
        if os.path.exists(fp):
            try:
                out[name] = json.load(open(fp, encoding='utf-8'))
            except Exception:
                pass
    # 表情包
    idx_fp = os.path.join(DATA_DIR, 'emojis', 'index.json')
    if os.path.exists(idx_fp):
        try:
            out["emoji_count"] = len([e for e in json.load(open(idx_fp, encoding='utf-8')) if e.get('file') != '示例.jpg'])
        except Exception:
            pass
    # 知识库
    if os.path.exists(KB_INDEX):
        try:
            out["kb_chunks"] = len(json.load(open(KB_INDEX, encoding='utf-8')))
        except Exception:
            pass
    # 消息时段分布
    if os.path.exists(MSG_JSONL):
        hours = {}
        n = 0
        for line in open(MSG_JSONL, encoding='utf-8'):
            try:
                m = json.loads(line)
                h = int(m.get('t', 0) // 3600 % 24)
                hours[h] = hours.get(h, 0) + 1
                n += 1
            except Exception:
                continue
        out["hour_hist"] = {str(h): hours.get(h, 0) for h in range(24)}
        out["total_msgs"] = n
    return out

PAGE = '''<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Eidolon Console</title><style>
*{box-sizing:border-box}body{font-family:system-ui;background:#0d1117;color:#e6edf3;margin:0;padding:20px}
h1{font-size:22px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px}
.card h2{margin:0 0 12px;font-size:15px;color:#58a6ff}
.big{font-size:30px;font-weight:700}.sub{opacity:.6;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:6px;border-bottom:1px solid #21262d;text-align:left}
.bar{display:inline-block;height:14px;background:#3fb950;border-radius:3px}
.tag{display:inline-block;background:#21262d;border-radius:6px;padding:2px 8px;margin:2px;font-size:12px}
.ok{color:#3fb950}.warn{color:#d29922}.err{color:#f85149}
</style></head><body>
<h1>Eidolon Console <span class="sub">数字分身控制台</span></h1>
<div id="app">加载中...</div>
<script>
fetch('/api/stats').then(r=>r.json()).then(d=>{
  let hours='';
  const max=Math.max(1,...Object.values(d.hour_hist));
  for(const [h,v] of Object.entries(d.hour_hist))
    hours+=`<span title="${h}点: ${v}条" class="bar" style="width:8px;opacity:${0.25+0.75*v/max};height:${8+28*v/max}px"></span> `;
  let tasks=d.tasks.map(t=>`<tr><td>${t.id}</td><td>${t.instruction}</td><td class="${t.status=='完成'?'ok':'warn'}">${t.status}</td><td>${t.steps}步</td></tr>`).join('');
  let rems=(d.reminders||[]).map(r=>`<tr><td>${r.time}</td><td>${r.content}</td><td class="${r.done?'ok':'warn'}">${r.done?'已发':'待发'}</td></tr>`).join('');
  let fups=(d.followups||[]).map(f=>`<tr><td>${f.due}</td><td>${f.topic}</td><td class="${f.fired?'ok':'warn'}">${f.fired?'已问':'待问'}</td></tr>`).join('');
  document.getElementById('app').innerHTML=`
  <div class="grid">
    <div class="card"><h2>语料规模</h2><div class="big">${d.total_msgs}</div><div class="sub">条分身本人消息(蒸馏源)</div></div>
    <div class="card"><h2>长期记忆</h2><div class="big">${d.memory_facts}</div><div class="sub">条事实 / ${d.memory_files} 个联系人档案</div></div>
    <div class="card"><h2>表情包库</h2><div class="big">${d.emoji_count}</div><div class="sub">张(GLM视觉打标)</div></div>
    <div class="card"><h2>知识库</h2><div class="big">${d.kb_chunks}</div><div class="sub">个RAG切片</div></div>
  </div>
  <div class="card" style="margin-top:16px"><h2>消息活跃时段</h2>${hours}</div>
  <div class="grid" style="margin-top:16px">
    <div class="card"><h2>Agent 任务</h2><table><tr><th>ID</th><th>任务</th><th>状态</th><th>步数</th></tr>${tasks||'<tr><td colspan=4>暂无</td></tr>'}</table></div>
    <div class="card"><h2>定时提醒</h2><table><tr><th>时间</th><th>内容</th><th>状态</th></tr>${rems||'<tr><td colspan=3>暂无</td></tr>'}</table></div>
    <div class="card"><h2>事项跟进</h2><table><tr><th>到期</th><th>主题</th><th>状态</th></tr>${fups||'<tr><td colspan=3>暂无</td></tr>'}</table></div>
  </div>`;
});
setInterval(()=>location.reload(), 60000);
</script></body></html>'''


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ct='text/html; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', ct)
        self.end_headers()
        self.wfile.write(body.encode('utf-8'))

    def do_GET(self):
        if self.path == '/':
            self._send(200, PAGE)
        elif self.path == '/api/stats':
            self._send(200, json.dumps(get_stats(), ensure_ascii=False), 'application/json; charset=utf-8')
        else:
            self._send(404, 'nf')

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8900
    print(f'Eidolon Console: http://localhost:{port}')
    HTTPServer(('0.0.0.0', port), H).serve_forever()
