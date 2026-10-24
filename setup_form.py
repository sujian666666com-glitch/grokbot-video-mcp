"""One-use loopback-only credential import form. Never logs form values."""
import json
import os
from pathlib import Path
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit
import bridge

ROOT = Path(__file__).resolve().parent
PATH = '/setup/' + secrets.token_urlsafe(24)
EXPIRES = time.time() + 600


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_GET(self):
        if self.path != PATH or time.time() > EXPIRES:
            self.send_error(404); return
        body = '''<!doctype html><meta charset="utf-8"><title>Grok Bot 本地连接配置</title>
        <h1>Grok Bot 本地连接配置</h1><p>仅将凭据保存到本机；不会发送到其他服务。</p>
        <form method="post"><p><label>Webhook URL <input name="url" type="url" required size="80"></label></p>
        <p><label>Webhook key <input name="key" type="password" required size="80" autocomplete="off"></label></p>
        <button type="submit">保存到本机</button></form>'''.encode()
        self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        origin = f'http://127.0.0.1:{self.server.server_port}'
        if self.path != PATH or self.headers.get('Origin') != origin or time.time() > EXPIRES:
            self.send_error(403); return
        size = int(self.headers.get('Content-Length', 0))
        if not 0 < size < 16384: self.send_error(400); return
        values = parse_qs(self.rfile.read(size).decode())
        url = values.get('url', [''])[0].strip(); key = values.get('key', [''])[0].strip()
        if not key or urlsplit(url).scheme != 'https': self.send_error(400); return
        target = bridge.DATA / 'config.json'
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
            json.dump({'webhook_url': url, 'webhook_key': key}, f)
        os.chmod(target, 0o600)
        body = '<meta charset="utf-8"><h1>已安全保存到本机</h1><p>配置完成，可以关闭此页。</p>'.encode()
        self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(body)
        print('Webhook configuration saved; key was not printed.', flush=True)
        threading.Thread(target=self.server.shutdown, daemon=True).start()


if __name__ == '__main__':
    bridge.prepare()
    server = HTTPServer(('127.0.0.1', 0), Handler)
    print(f'http://127.0.0.1:{server.server_port}{PATH}', flush=True)
    timer = threading.Timer(600, server.shutdown); timer.daemon = True; timer.start()
    server.serve_forever(); server.server_close(); timer.cancel()
