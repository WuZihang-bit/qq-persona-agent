# -*- coding: utf-8 -*-
"""Eidolon Web 控制台(服务器版): 零依赖, 只读
python3 console_server.py [port]  ->  http://<YOUR_SERVER_IP>:8899
面板: 系统状态(watchdog/QQ/容器) / 今日日程 / 记忆档案 / 群印象 / 提醒跟进 / 主动关怀 / 表情包 / 用量
"""
import json
import os
import sqlite3
import subprocess
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

BASE = '/root/eidolon'
DATA = os.path.join(BASE, 'data')
FEN = os.path.join(DATA, 'fensheng')


def _j(fp, default):
    try:
        return json.load(open(fp, encoding='utf-8'))
    except Exception:
        return default


def _sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, timeout=8,
                              text=True).stdout.strip()
    except Exception:
        return '?'


def collect():
    out = {}
    # 系统
    wd = open(os.path.join(BASE, 'watchdog.state')).read().strip() if os.path.exists(os.path.join(BASE, 'watchdog.state')) else '?'
    wdlog = []
    try:
        wdlog = [l for l in open(os.path.join(BASE, 'watchdog.log'), encoding='utf-8').read().splitlines() if l.strip()][-6:]
    except Exception:
        pass
    out['sys'] = {
        'watchdog': wd,
        'uptime': _sh("docker ps --format '{{.Names}}: {{.Status}}' | grep -E 'napcat|astrbot'"),
        'disk': _sh("df -h / | tail -1 | awk '{print $3\"/\"$2\" (\"$5\")\"}'"),
        'mem': _sh("free -h | awk '/Mem/{print $3\"/\"$2}'"),
        'now': time.strftime('%Y-%m-%d %H:%M:%S'),
        'wdlog': wdlog,
    }
    # 日程
    sched = _j(os.path.join(FEN, 'schedule.json'), {})
    now_txt = ''
    try:
        now_txt = open(os.path.join(FEN, 'schedule_now.txt'), encoding='utf-8').read().strip()
    except Exception:
        pass
    out['schedule'] = {'date': sched.get('date', ''), 'note': sched.get('note', ''),
                       'slots': sched.get('slots', []), 'now': now_txt}
    # 记忆档案
    mems = []
    md = os.path.join(FEN, 'memory')
    if os.path.isdir(md):
        for f in sorted(os.listdir(md)):
            if not f.endswith('.json'):
                continue
            d = _j(os.path.join(md, f), {})
            mems.append({
                'key': f[:-5],
                'entries': len(d.get('entries') or []),
                'facts': len(d.get('facts') or []),
                'diary': (d.get('diary') or {}).get('text', ''),
                'proactive': [p.get('text', '')[:40] for p in (d.get('proactive') or [])[-2:]],
                'n_llm': d.get('n_llm', 0),
            })
    out['memory'] = mems
    # 群印象
    out['impressions'] = _j(os.path.join(FEN, 'group_impressions.json'), {})
    # 提醒/跟进/关怀/生日
    out['reminders'] = [r for r in _j(os.path.join(FEN, 'reminders.json'), []) if not r.get('done')][-8:]
    out['followups'] = [f for f in _j(os.path.join(FEN, 'followups.json'), []) if not f.get('fired')][-8:]
    out['care'] = _j(os.path.join(FEN, 'care_plan.json'), {})
    out['birthdays'] = _j(os.path.join(FEN, 'birthdays.json'), {})
    # 表情包
    out['emojis'] = len(_j(os.path.join(FEN, 'emojis', 'index.json'), []))
    # 用量(sqlite只读)
    try:
        db = sqlite3.connect(f"file:{os.path.join(DATA, 'data_v4.db')}?mode=ro", uri=True)
        cur = db.cursor()
        rows = cur.execute(
            "SELECT provider_model, SUM(token_input_other + token_input_cached), SUM(token_output), COUNT(*) "
            "FROM provider_stats GROUP BY provider_model ORDER BY 2 DESC LIMIT 6").fetchall()
        out['usage'] = [{'name': r[0], 'prompt': r[1] or 0, 'completion': r[2] or 0, 'calls': r[3]} for r in rows]
        db.close()
    except Exception:
        out['usage'] = []
    return out


PAGE_HEAD = ('<!DOCTYPE html><html><head><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width,initial-scale=1">'
             '<title>Eidolon 控制台</title><style>'
             'body{background:#0f1117;color:#d7dae0;font-family:system-ui;margin:0;padding:20px}'
             'h1{font-size:20px;color:#7ee0a3}h2{font-size:15px;color:#9ecbff;border-bottom:1px solid #23263a;padding-bottom:4px;margin:24px 0 8px}'
             '.card{background:#171a24;border-radius:10px;padding:12px 16px;margin:8px 0}'
             '.row{display:flex;gap:12px;flex-wrap:wrap}.row .card{flex:1;min-width:220px}'
             '.kv{display:flex;justify-content:space-between;padding:3px 0;font-size:13px}'
             '.kv b{color:#e8eaf0;font-weight:500}.ok{color:#7ee0a3}.bad{color:#e06c75}'
             'table{width:100%;border-collapse:collapse;font-size:13px}'
             'td,th{padding:5px 8px;border-bottom:1px solid #23263a;text-align:left}'
             'small{color:#6b7280}'
             '</style></head><body>')

JS = '''<script>
const esc = s => String(s??'').replace(/[<>&"]/g, c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
async function refresh(){
  const d = await (await fetch('/api/stats')).json();
  const s=d.sys;
  document.getElementById('body').innerHTML = `
  <h1>Eidolon 控制台 <small>${esc(s.now)} · 自动刷新30s</small></h1>
  <div class="row">
    <div class="card"><h2 style="margin-top:0">系统</h2>
      <div class="kv"><span>看门狗</span><b class="${s.watchdog=='online'?'ok':'bad'}">${esc(s.watchdog)}</b></div>
      <div class="kv"><span>容器</span><b>${esc(s.uptime).replace(/\\n/g,'<br>')}</b></div>
      <div class="kv"><span>磁盘</span><b>${esc(s.disk)}</b></div>
      <div class="kv"><span>内存</span><b>${esc(s.mem)}</b></div>
    </div>
    <div class="card"><h2 style="margin-top:0">今日日程</h2>
      <div class="kv"><span>现在</span><b class="ok">${esc(d.schedule.now||'—')}</b></div>
      <div class="kv"><span>备注</span><b>${esc(d.schedule.note)}</b></div>
      ${d.schedule.slots.map(x=>`<div class="kv"><span>${esc(x.start)}-${esc(x.end)}</span><b>${esc(x.act)} <small>(${esc(x.mood)})</small></b></div>`).join('')}
    </div>
    <div class="card"><h2 style="margin-top:0">资产</h2>
      <div class="kv"><span>表情包</span><b>${d.emojis} 张</b></div>
      <div class="kv"><span>待提醒</span><b>${d.reminders.length} 条</b></div>
      <div class="kv"><span>待跟进</span><b>${d.followups.length} 条</b></div>
      <div class="kv"><span>今晚关怀</span><b>${(d.care.plans||[]).filter(p=>!p.done).map(p=>p.at+'→'+p.qq).join(' ')||'未排'}</b></div>
    </div>
  </div>
  <h2>LLM 用量</h2><div class="card"><table><tr><th>Provider</th><th>调用</th><th>输入tok</th><th>输出tok</th></tr>
    ${d.usage.map(u=>`<tr><td>${esc(u.name)}</td><td>${u.calls}</td><td>${u.prompt}</td><td>${u.completion}</td></tr>`).join('')||'<tr><td colspan=4>无数据</td></tr>'}</table></div>
  <h2>记忆档案 (${d.memory.length})</h2><div class="card"><table><tr><th>联系人</th><th>轮次</th><th>档案</th><th>原料</th><th>日记</th><th>主动</th></tr>
    ${d.memory.map(m=>`<tr><td>${esc(m.key)}</td><td>${m.n_llm}</td><td>${m.entries}</td><td>${m.facts}</td><td>${esc(m.diary)}</td><td>${m.proactive.map(esc).join('<br>')}</td></tr>`).join('')}</table></div>
  <h2>群印象 (${Object.keys(d.impressions).length})</h2>
  ${Object.entries(d.impressions).map(([g,v])=>`<div class="card"><b>群${esc(g)}</b> <small>${esc(v.updated)}</small><br>
    ${esc(v.vibe)}<br><small>常聊:${(v.topics||[]).join('/')} ${v.members?'·核心:'+esc(v.members):''}</small>
    ${Object.keys(v.slang||{}).length?'<br><small>黑话:'+Object.entries(v.slang).map(([k,x])=>esc(k)+'='+esc(x)).join('; ')+'</small>':''}</div>`).join('')||'<div class="card">暂无</div>'}
  <h2>看门狗日志</h2><div class="card" style="font-size:12px">${d.sys.wdlog.map(l=>esc(l)).join('<br>')||'无事件'}</div>`;
}
refresh(); setInterval(refresh, 30000);
</script>'''


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/api/stats'):
            body = json.dumps(collect(), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
        else:
            body = (PAGE_HEAD + '<div id="body">加载中...</div>' + JS + '</body></html>').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    print(f'Eidolon console on :{port}')
    HTTPServer(('0.0.0.0', port), H).serve_forever()
