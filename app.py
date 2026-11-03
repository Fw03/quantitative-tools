import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from engine import demo, load_csv, run

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            content = Path(__file__).with_name('index.html').read_bytes()
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.end_headers(); self.wfile.write(content)
        elif self.path == '/health':
            self.respond(200, {'status':'ok'})
        else:
            self.respond(404, {'error':'Not found'})
    def respond(self, status, value):
        body=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        if self.path != '/api/run':
            return self.respond(404,{'error':'Not found'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 10_000_000:
                return self.respond(413,{'error':'请求大小须在 1–10MB 范围内'})
            payload=json.loads(self.rfile.read(size))
            data=load_csv(payload['csv']) if payload.get('csv') else demo()
            result=run(data, capital=float(payload.get('capital',100000)), fee=float(payload.get('fee',.0003)), slippage=float(payload.get('slippage',.001)), stop=float(payload.get('stop',.08)),max_positions=int(payload.get('max_positions',5)))
            result['source']='用户导入 CSV' if payload.get('csv') else '合成演示数据（非真实行情）'
            self.respond(200,result)
        except (ValueError,KeyError,TypeError,OverflowError) as exc:
            self.respond(400,{'error':f'数据或参数错误：{exc}'})

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8000); args=parser.parse_args()
    print(f'Quantitative tools listening on port {args.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
