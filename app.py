import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from engine import demo, load_csv, run
from market import Market, MarketError
from research import compare
from store import RunStore
from strategies import catalog, get_strategy

market = Market()
store = RunStore()


def read_options(payload):
    options = dict(capital=float(payload.get('capital', 100000)), fee=float(payload.get('fee', .0003)),
                   slippage=float(payload.get('slippage', .001)), stop=float(payload.get('stop', .08)),
                   max_positions=int(payload.get('max_positions', 5)),
                   strategy=payload.get('strategy', 'trend_breakout'),
                   strategy_params=payload.get('strategy_params'))
    get_strategy(options['strategy'], options['strategy_params'])
    return options


def read_data(payload):
    mode = payload.get('mode', 'csv' if payload.get('csv') else 'demo')
    if mode == 'market':
        history = market.history(payload.get('symbols', ''), int(payload.get('days', 500)))
        return history['data'], history['source'], {k:v for k,v in history.items() if k != 'data'}
    if mode == 'csv':
        return load_csv(payload.get('csv', '')), '用户导入 CSV', None
    if mode == 'demo':
        return demo(), '合成演示数据（非真实行情）', None
    raise ValueError('未知数据来源')


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == '/api/strategies':
                return self.respond(200, catalog())
            if parsed.path == '/api/runs':
                return self.respond(200, {'runs': store.recent()})
            if parsed.path.startswith('/api/runs/'):
                parts = parsed.path.strip('/').split('/')
                if len(parts) not in (3, 4) or len(parts) == 4 and parts[3] != 'snapshot':
                    return self.respond(404, {'error': '记录不存在'})
                record = store.get(parts[2], include_data=len(parts) == 4)
                return self.respond(200, record) if record else self.respond(404, {'error': '记录不存在或已超过保留数量'})
            if parsed.path == '/api/stocks':
                result = market.stocks()
                search = query.get('q', [''])[0].strip().lower()
                return self.respond(200, dict(result, stocks=[s for s in result['stocks'] if search in s['symbol'] or search in s['name']]))
            if parsed.path == '/api/quotes':
                return self.respond(200, market.quotes(query.get('symbols', [''])[0]))
            if parsed.path == '/health':
                return self.respond(200, {'status': 'ok'})
            assets = {'/static/app.js': 'text/javascript; charset=utf-8',
                      '/static/style.css': 'text/css; charset=utf-8'}
            if parsed.path in assets:
                content = (Path(__file__).parent / parsed.path.lstrip('/')).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', assets[parsed.path])
                self.end_headers()
                return self.wfile.write(content)
            if parsed.path == '/':
                content = Path(__file__).with_name('index.html').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                return self.wfile.write(content)
            self.respond(404, {'error': 'Not found'})
        except MarketError as exc:
            self.respond(502, {'error': str(exc)})
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            self.respond(400, {'error': f'数据或参数错误：{exc}'})
        except (sqlite3.Error, OSError):
            self.respond(500, {'error': '本地记录读取失败，请检查 data 目录权限及磁盘空间'})

    def respond(self, status, value):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        parts = path.strip('/').split('/')
        replay = len(parts) == 4 and parts[:2] == ['api', 'runs'] and parts[3] == 'replay'
        if path not in ('/api/run', '/api/compare') and not replay:
            return self.respond(404, {'error': 'Not found'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 10_000_000:
                return self.respond(413, {'error': '请求大小须在 1–10MB 范围内'})
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError('请求必须是 JSON 对象')
            previous = None
            if replay:
                previous = store.get(parts[2], include_data=True)
                if previous is None:
                    return self.respond(404, {'error': '记录不存在或已超过保留数量'})
                snapshot = previous['snapshot']
                data, options = snapshot['data'], snapshot['options']
                keys, kind = snapshot['strategy_keys'], previous['kind']
                source = previous['result']['source']
                metadata = previous['result'].get('market_metadata')
            else:
                options = read_options(payload)
                data, source, metadata = read_data(payload)
                kind = 'compare' if path == '/api/compare' else 'backtest'
                keys = payload.get('strategies')
            result = compare(data, options, keys) if kind == 'compare' else run(data, **options)
            if metadata:
                result['market_metadata'] = metadata
            result = store.save(kind, result, data, options, source, keys, parts[2] if replay else None)
            self.respond(200, result)
        except MarketError as exc:
            self.respond(502, {'error': str(exc)})
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            self.respond(400, {'error': f'数据或参数错误：{exc}'})
        except (sqlite3.Error, OSError):
            self.respond(500, {'error': '结果保存失败，请检查 data 目录权限及磁盘空间'})


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Quantitative tools listening on port {args.port}', flush=True)
    server.serve_forever()
