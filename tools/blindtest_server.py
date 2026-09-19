# -*- coding: utf-8 -*-
"""盲测数据收集服务器: 零依赖 http.server
用法: python blindtest_server.py  (默认 http://localhost:8899)
流程: 参与者填昵称 -> 看10轮对话 -> 每轮猜"真人/AI" -> 提交
"""
import json, os, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

DATA_FP = os.path.join(os.path.dirname(__file__), 'blindtest_results.jsonl')
PAGE_FP = os.path.join(os.path.dirname(__file__), 'blindtest.html')
CONV_FP = os.path.join(os.path.dirname(__file__), 'blindtest_conversations.json')

HTML = '''<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Eidolon 盲测</title><style>
body{font-family:system-ui;max-width:640px;margin:20px auto;padding:0 16px;background:#111;color:#eee}
.bubble{margin:8px 0;padding:10px 14px;border-radius:14px;max-width:80%}
.left{background:#2a2a2a}.right{background:#3d8f5d;margin-left:auto}
.meta{font-size:12px;opacity:.5}.btn{padding:12px 28px;font-size:18px;margin:8px;border:0;border-radius:8px;cursor:pointer}
.human{background:#3d8f5d;color:#fff}.ai{background:#b3541e;color:#fff}
#done{display:none;text-align:center;margin-top:40px}
</style></head><body>
<h2>Eidolon 人格盲测</h2><p>每轮是一段QQ对话（右为分身回复）。猜猜分身这条回复是<b>真人发的</b>还是<b>AI生成的</b>。</p>
<div id="quiz"></div>
<div id="done"><h3>✅ 提交成功</h3><p>感谢参与！你的判断已记录。</p></div>
<script>
let data=null, round=0, answers=[];
fetch('/conversations').then(r=>r.json()).then(d=>{data=d;show();});
function show(){
  const q=document.getElementById('quiz');
  if(round>=data.length){q.innerHTML='<div id="done"><h3>全部完成! 提交中...</h3></div>';
    fetch('/submit',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({when:new Date().toISOString(),answers:answers})})
      .then(()=>{document.getElementById('done').innerHTML='<h3>✅ 提交成功</h3><p>感谢参与!</p>';});
    return;}
  const c=data[round]; let h='<div class="meta">第 '+(round+1)+' / '+data.length+' 轮</div>';
  for(const m of c.messages) h+='<div class="bubble '+(m.side=='me'?'right':'left')+'">'+m.text+'</div>';
  h+='<div style="margin-top:20px;text-align:center"><button class="btn human" onclick="guess(1)">真人</button>'+
     '<button class="btn ai" onclick="guess(0)">AI</button></div>';
  q.innerHTML=h;
}
function guess(isHuman){answers.push({round:round,human:!!isHuman});round++;show();}
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype='text/html; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.end_headers()
        self.wfile.write(body.encode('utf-8'))

    def do_GET(self):
        if self.path == '/':
            self._send(200, open(PAGE_FP, encoding='utf-8').read() if os.path.exists(PAGE_FP) else HTML)
        elif self.path == '/conversations':
            convs = json.load(open(CONV_FP, encoding='utf-8')) if os.path.exists(CONV_FP) else []
            # 洗牌答案顺序(每个访问者随机化真人/AI顺序不透露)
            self._send(200, json.dumps(convs, ensure_ascii=False), 'application/json; charset=utf-8')
        else:
            self._send(404, 'not found')

    def do_POST(self):
        if self.path == '/submit':
            n = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(n))
            with open(DATA_FP, 'a', encoding='utf-8') as f:
                f.write(json.dumps(body, ensure_ascii=False) + '\n')
            self._send(200, '{"ok":true}', 'application/json')
        else:
            self._send(404, 'not found')

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    print(f'盲测服务器: http://localhost:{port}  (对话数据: {CONV_FP})')
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()
