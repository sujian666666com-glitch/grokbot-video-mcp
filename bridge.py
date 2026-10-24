"""Local, capability-scoped file exchange for Grok Bot video routines."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import signal
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('GROKBOT_BRIDGE_DATA', ROOT / 'data')).expanduser().resolve()
MAX_IMAGE = 20 * 1024 * 1024
MAX_VIDEO = 90 * 1024 * 1024


def executable(name):
    """Resolve optional overrides, a local Cloudflare binary, then PATH."""
    override = os.environ.get('GROKBOT_' + name.upper())
    candidates = [override] if override else []
    if not override:
        if name == 'cloudflared':
            candidates.append(str(ROOT / 'bin' / name))
        candidates.append(name)
    for candidate in candidates:
        resolved = shutil.which(os.path.expanduser(candidate))
        if resolved:
            return resolved
    raise FileNotFoundError(f'{name} is required; install it or set GROKBOT_{name.upper()}')


def prepare():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    (DATA / 'jobs').mkdir(exist_ok=True, mode=0o700)
    (DATA / 'logs').mkdir(exist_ok=True, mode=0o700)
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, document TEXT NOT NULL)')


def db():
    c = sqlite3.connect(DATA / 'jobs.sqlite', timeout=30)
    c.execute('PRAGMA busy_timeout=30000')
    return c


def save_new(job):
    with db() as c:
        c.execute('INSERT INTO jobs VALUES (?,?)', (job['job_id'], json.dumps(job, ensure_ascii=False)))


def get_job(job_id):
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('Invalid job_id')
    with db() as c:
        row = c.execute('SELECT document FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        raise ValueError('Unknown job_id')
    return json.loads(row[0])


def update_job(job_id, change):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT document FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('Unknown job_id')
        job = json.loads(row[0])
        change(job)
        job['updated_at'] = time.time()
        c.execute('UPDATE jobs SET document=? WHERE id=?', (json.dumps(job, ensure_ascii=False), job_id))
    return job


def event(job, name, **fields):
    job.setdefault('events', []).append({'at': time.time(), 'event': name, **fields})


def public_job(job):
    visible = {k: v for k, v in job.items() if k not in ('read_token', 'write_token', 'base_url')}
    # A Bot may accidentally echo a callback URL in an error or progress message.
    text = json.dumps(visible, ensure_ascii=False)
    for name in ('read_token', 'write_token'):
        if job.get(name):
            text = text.replace(job[name], '[REDACTED]')
    return json.loads(text)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def request_json(url, body=None, headers=None, timeout=30, method=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    if data is not None:
        request.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(request, timeout=timeout) as r:
        raw = r.read(65536)
        return r.status, json.loads(raw) if raw else {}


def config():
    p = DATA / 'config.json'
    if not p.exists():
        raise ValueError('Grok Bot webhook is not configured yet')
    value = json.loads(p.read_text())
    if not value.get('webhook_url', '').startswith('https://') or not value.get('webhook_key'):
        raise ValueError('An HTTPS webhook URL and key are required')
    return value


def ensure_service():
    for name in ('cloudflared', 'ffmpeg', 'ffprobe'):
        executable(name)
    prepare()
    lock = (DATA / 'service.lock').open('a+')
    with lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        runtime_path = DATA / 'runtime.json'
        runtime = json.loads(runtime_path.read_text()) if runtime_path.exists() else {}
        if runtime.get('public_url'):
            local_alive = False
            try:
                _, health = request_json(f'http://127.0.0.1:{runtime["port"]}/healthz', timeout=2)
                local_alive = health.get('instance') == runtime.get('instance')
            except (OSError, ValueError, urllib.error.URLError):
                pass
            if local_alive:
                for attempt in range(3):
                    try:
                        _, external = request_json(runtime['public_url'] + '/healthz', timeout=8)
                        if external.get('instance') == runtime['instance']:
                            return runtime
                    except (OSError, ValueError, urllib.error.URLError):
                        pass
                    time.sleep(1)
                raise RuntimeError('Existing HTTPS tunnel is temporarily unreachable; no request submitted. Retry later without replacing the active tunnel.')
        # Do not kill processes based on stale PIDs. A new daemon gets an unused port.
        runtime_path.unlink(missing_ok=True)
        log = (DATA / 'logs' / 'daemon.log').open('ab')
        subprocess.Popen([sys.executable, str(ROOT / 'bridge.py'), 'serve'],
                         cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True)
        log.close()
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            if runtime_path.exists():
                runtime = json.loads(runtime_path.read_text())
                if runtime.get('public_url'):
                    try:
                        _, health = request_json(runtime['public_url'] + '/healthz', timeout=8)
                        if health.get('instance') == runtime['instance']:
                            return runtime
                    except (OSError, ValueError, urllib.error.URLError):
                        pass
                if runtime.get('error'):
                    raise RuntimeError(runtime['error'])
            time.sleep(1)
        raise RuntimeError('HTTPS tunnel startup timed out; no video request was submitted')


def create_job(prompt, image_paths=None, duration_seconds=6, aspect_ratio='16:9', silent=True):
    if not prompt.strip() or len(prompt) > 12000:
        raise ValueError('prompt must contain 1–12000 characters')
    if not 1 <= duration_seconds <= 15:
        raise ValueError('duration_seconds must be between 1 and 15')
    if aspect_ratio not in ('16:9', '9:16', '1:1', '4:3', '3:4'):
        raise ValueError('Unsupported aspect_ratio')
    image_paths = image_paths or []
    if len(image_paths) > 5:
        raise ValueError('At most 5 reference images')
    from PIL import Image
    checked = []
    for name in image_paths:
        p = Path(name).expanduser().resolve(strict=True)
        if not p.is_file() or p.stat().st_size > MAX_IMAGE:
            raise ValueError('Each image must be a file smaller than 20 MiB')
        with Image.open(p) as im:
            if im.format not in ('PNG', 'JPEG', 'WEBP'):
                raise ValueError('Use PNG, JPEG, or WebP images')
            kind, size = im.format, im.size
            im.verify()
        checked.append((p, kind, size))
    runtime = ensure_service()
    job_id = uuid.uuid4().hex
    folder = DATA / 'jobs' / job_id
    folder.mkdir(mode=0o700)
    inputs = []
    for i, (p, kind, size) in enumerate(checked):
        ext = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}[kind]
        target = folder / f'input-{i}{ext}'
        shutil.copyfile(p, target)
        os.chmod(target, 0o600)
        inputs.append({'index': i, 'filename': target.name, 'sha256': sha256(target),
                       'bytes': target.stat().st_size, 'size': list(size)})
    job = {'job_id': job_id, 'status': 'prepared', 'prompt': prompt,
           'duration_seconds': duration_seconds, 'aspect_ratio': aspect_ratio, 'silent': silent,
           'inputs': inputs, 'read_token': secrets.token_urlsafe(32),
           'write_token': secrets.token_urlsafe(32), 'base_url': runtime['public_url'],
           'created_at': time.time(), 'updated_at': time.time(), 'expires_at': time.time() + 86400,
           'events': [], 'video': None, 'error': None}
    event(job, 'prepared')
    save_new(job)
    return job


def bot_payload(job):
    base = job['base_url'] + '/jobs/' + job['job_id']
    read = urllib.parse.quote(job['read_token'])
    write = urllib.parse.quote(job['write_token'])
    return {
        'protocol': 'grokbot-video-bridge/1', 'job_id': job['job_id'],
        'prompt': job['prompt'], 'duration_seconds': job['duration_seconds'],
        'aspect_ratio': job['aspect_ratio'], 'silent': job['silent'],
        'image_urls': [f'{base}/images/{x["index"]}?token={read}' for x in job['inputs']],
        'status_url': f'{base}/status?token={write}',
        'result_upload_url': f'{base}/video?token={write}',
    }


def submit_video(prompt, image_paths=None, duration_seconds=6, aspect_ratio='16:9', silent=True):
    credentials = config()  # Fail before starting services if unconfigured.
    job = create_job(prompt, image_paths, duration_seconds, aspect_ratio, silent)
    payload = bot_payload(job)
    def mark_submitting(j):
        j['status'] = 'submitting'
        event(j, 'webhook_submission_started')
    update_job(job['job_id'], mark_submitting)
    try:
        request = urllib.request.Request(credentials['webhook_url'],
                    data=json.dumps(payload, ensure_ascii=False).encode(),
                    headers={'Content-Type': 'application/json',
                             'Authorization': 'Bearer ' + credentials['webhook_key']}, method='POST')
        with urllib.request.urlopen(request, timeout=35) as response:
            code = response.status
            response.read(16384)
        def accepted(j):
            if j['status'] == 'submitting':
                j['status'] = 'submitted'
            event(j, 'webhook_accepted', http_status=code)
        job = update_job(job['job_id'], accepted)
    except urllib.error.HTTPError as e:
        def rejected(j):
            j['status'] = 'failed'
            j['error'] = f'Webhook rejected request: HTTP {e.code}'
            event(j, 'webhook_rejected', http_status=e.code)
        job = update_job(job['job_id'], rejected)
    except (OSError, urllib.error.URLError) as e:
        def uncertain(j):
            j['status'] = 'submission_unknown'
            j['error'] = 'Webhook transport interrupted; do not automatically resubmit (could duplicate generation).'
            event(j, 'webhook_transport_error', error_type=type(e).__name__)
        job = update_job(job['job_id'], uncertain)
    return public_job(job)


def inspect_video(path):
    result = subprocess.run([executable('ffprobe'), '-v', 'error', '-show_streams',
                             '-show_format', '-of', 'json', str(path)],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('Uploaded file could not be decoded as a video')
    value = json.loads(result.stdout)
    videos = [s for s in value.get('streams', []) if s.get('codec_type') == 'video']
    if not videos or float(value.get('format', {}).get('duration', 0)) <= 0:
        raise ValueError('Uploaded file has no playable video stream')
    first = videos[0]
    return {'width': first['width'], 'height': first['height'], 'codec': first['codec_name'],
            'duration_seconds': float(value['format']['duration']),
            'has_audio': any(s.get('codec_type') == 'audio' for s in value.get('streams', []))}


class Handler(BaseHTTPRequestHandler):
    server_version = 'GrokBotVideoBridge/1'

    def log_message(self, fmt, *args):
        pass  # Never log capability URLs or credentials.

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def route(self, write=False):
        parsed = urllib.parse.urlsplit(self.path)
        match = re.fullmatch(r'/jobs/([0-9a-f]{32})/(images/([0-9]+)|status|video)', parsed.path)
        if not match:
            raise ValueError('Unknown route')
        job = get_job(match[1])
        token = urllib.parse.parse_qs(parsed.query).get('token', [''])[0]
        if not hmac.compare_digest(token, job['write_token' if write else 'read_token']):
            raise PermissionError('Unauthorized')
        if time.time() > job['expires_at']:
            raise PermissionError('Job links expired')
        return job, match[2], match[3]

    def do_GET(self):
        if self.path == '/healthz':
            return self.reply(200, {'ok': True, 'instance': self.server.instance})
        try:
            job, route, index = self.route()
            if index is None:
                raise ValueError('Unknown route')
            record = job['inputs'][int(index)]
            path = DATA / 'jobs' / job['job_id'] / record['filename']
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(path.stat().st_size))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            with path.open('rb') as f:
                shutil.copyfileobj(f, self.wfile)
            update_job(job['job_id'], lambda j: event(j, 'reference_downloaded', index=int(index), sha256=record['sha256']))
        except PermissionError:
            self.reply(403, {'error': 'Unauthorized or expired'})
        except (ValueError, IndexError):
            self.reply(404, {'error': 'Not found'})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            job, route, _ = self.route(write=True)
            if route != 'status':
                raise ValueError('Use PUT for the video upload')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 16384:
                raise ValueError('Invalid JSON body size')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object')
            state = body.get('status')
            if state not in ('running', 'failed', 'succeeded'):
                raise ValueError('status must be running, failed or succeeded')
            def change(j):
                if j['status'] == 'succeeded':
                    return
                if state == 'succeeded' and not j['video']:
                    raise ValueError('Upload the playable video before declaring success')
                j['status'] = state
                if state == 'failed': j['error'] = str(body.get('error', 'Bot reported failure'))[:2000]
                event(j, 'bot_status', status=state, message=str(body.get('message', ''))[:2000])
            updated = update_job(job['job_id'], change)
            self.reply(200, {'job_id': job['job_id'], 'status': updated['status']})
        except PermissionError:
            self.reply(403, {'error': 'Unauthorized or expired'})
        except (ValueError, json.JSONDecodeError) as e:
            self.reply(400, {'error': str(e)})

    def do_PUT(self):
        temporary = None
        try:
            job, route, _ = self.route(write=True)
            if route != 'video':
                raise ValueError('Unknown upload route')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_VIDEO:
                raise ValueError('Video must have Content-Length and be no larger than 90 MiB')
            folder = DATA / 'jobs' / job['job_id']
            temporary = folder / ('upload-' + uuid.uuid4().hex + '.mp4')
            self.connection.settimeout(120)
            remaining = size
            with temporary.open('xb') as f:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk: raise ValueError('Incomplete upload')
                    f.write(chunk)
                    remaining -= len(chunk)
            metadata = inspect_video(temporary)
            if job['silent'] and metadata['has_audio']:
                silent = folder / ('silent-' + uuid.uuid4().hex + '.mp4')
                result = subprocess.run([executable('ffmpeg'), '-v', 'error', '-i', str(temporary),
                    '-map', '0:v:0', '-c:v', 'copy', '-an', '-movflags', '+faststart', str(silent)],
                    capture_output=True, timeout=60)
                if result.returncode: raise ValueError('Could not remove audio track')
                temporary.unlink()
                temporary = silent
                metadata = inspect_video(temporary)
            target = folder / 'video.mp4'
            digest = sha256(temporary)
            os.chmod(temporary, 0o600)
            try:
                # Atomic publish: concurrent callbacks cannot overwrite another result.
                os.link(temporary, target)
            except FileExistsError:
                if sha256(target) != digest:
                    raise ValueError('This job already has a different result; it cannot be overwritten')
            temporary.unlink()
            def success(j):
                j['video'] = {**metadata, 'path': str(target), 'sha256': digest, 'bytes': target.stat().st_size}
                j['status'] = 'succeeded'
                j['error'] = None
                event(j, 'video_received_and_verified', sha256=digest, **metadata)
            update_job(job['job_id'], success)
            self.reply(200, {'job_id': job['job_id'], 'status': 'succeeded', 'sha256': digest})
        except PermissionError:
            self.reply(403, {'error': 'Unauthorized or expired'})
        except (ValueError, OSError, subprocess.TimeoutExpired) as e:
            self.reply(400, {'error': str(e)[:500]})
        finally:
            if temporary and temporary.exists(): temporary.unlink()


def serve():
    prepare()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.instance = secrets.token_hex(16)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    runtime = {'pid': os.getpid(), 'port': port, 'instance': server.instance, 'started_at': time.time()}
    path = DATA / 'runtime.json'
    def write_runtime():
        tmp = DATA / 'runtime.tmp'
        tmp.write_text(json.dumps(runtime))
        tmp.replace(path)
    write_runtime()
    log = (DATA / 'logs' / f'tunnel-{os.getpid()}.log').open('w')
    cloudflared = executable('cloudflared')
    proc = subprocess.Popen([cloudflared, 'tunnel', '--no-autoupdate',
                             '--url', f'http://127.0.0.1:{port}', '--protocol', 'http2'],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    runtime['tunnel_pid'] = proc.pid
    runtime['tunnel_executable'] = cloudflared
    write_runtime()
    try:
        for line in proc.stdout:
            log.write(line); log.flush()
            match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
            if match:
                runtime['public_url'] = match[0]
                write_runtime()
        runtime['error'] = f'cloudflared stopped with code {proc.wait()}'
        write_runtime()
    finally:
        server.shutdown()
        proc.terminate()
        log.close()


def download_video(job_id, output_dir=None):
    job = get_job(job_id)
    if job['status'] != 'succeeded' or not job.get('video'):
        raise ValueError('Video is not ready')
    source = Path(job['video']['path'])
    if sha256(source) != job['video']['sha256']:
        raise ValueError('Stored video checksum mismatch')
    folder = Path(output_dir).expanduser() if output_dir else Path.home() / 'Downloads/grokbot-videos'
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f'grokbot-{job_id}.mp4'
    if destination.exists() and sha256(destination) != job['video']['sha256']:
        raise ValueError('Destination already contains a different file')
    if not destination.exists(): shutil.copyfile(source, destination)
    return {**job['video'], 'path': str(destination), 'job_id': job_id}


def stop_service():
    with db() as c:
        jobs = [json.loads(row[0]) for row in c.execute('SELECT document FROM jobs')]
    active = [j['job_id'] for j in jobs if j['status'] in ('submitting', 'submitted', 'running', 'submission_unknown')
              and j['expires_at'] > time.time()]
    if active:
        raise ValueError('Jobs are still in flight; do not invalidate their callback links: ' + ', '.join(active))
    p = DATA / 'runtime.json'
    if not p.exists(): return {'stopped': True}
    r = json.loads(p.read_text())
    for key, expected in [('tunnel_pid', r.get('tunnel_executable', str(ROOT / 'bin/cloudflared'))), ('pid', str(ROOT / 'bridge.py'))]:
        pid = r.get(key)
        if not pid: continue
        proc = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
        if expected in proc.stdout:
            with contextlib.suppress(ProcessLookupError): os.kill(pid, signal.SIGTERM)
    p.unlink(missing_ok=True)
    return {'stopped': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['serve', 'start', 'stop', 'health', 'status', 'list', 'submit', 'download', 'configure'])
    parser.add_argument('--job-id'); parser.add_argument('--prompt'); parser.add_argument('--image', action='append', default=[])
    parser.add_argument('--duration', type=int, default=6); parser.add_argument('--aspect-ratio', default='16:9')
    parser.add_argument('--output-dir'); parser.add_argument('--webhook-url'); parser.add_argument('--key-file')
    args = parser.parse_args()
    if args.command == 'serve': return serve()
    prepare()
    if args.command == 'configure':
        key = Path(args.key_file).read_text().strip() if args.key_file else sys.stdin.read().strip()
        key = re.sub(r'^Authorization:\s*Bearer\s+', '', key, flags=re.I)
        if not key or not (args.webhook_url or '').startswith('https://'): raise ValueError('Invalid webhook configuration')
        p = DATA / 'config.json'
        with os.fdopen(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
            json.dump({'webhook_url': args.webhook_url, 'webhook_key': key}, f)
        os.chmod(p, 0o600)
        result = {'configured': True}
    elif args.command == 'start':
        r = ensure_service(); result = {'running': True, 'public_url': r['public_url']}
    elif args.command == 'stop': result = stop_service()
    elif args.command == 'health':
        r = json.loads((DATA / 'runtime.json').read_text()) if (DATA / 'runtime.json').exists() else {}
        result = {'configured': (DATA / 'config.json').exists(), 'runtime': r}
    elif args.command == 'status': result = public_job(get_job(args.job_id))
    elif args.command == 'submit': result = submit_video(args.prompt, args.image, args.duration, args.aspect_ratio)
    elif args.command == 'download': result = download_video(args.job_id, args.output_dir)
    elif args.command == 'list':
        with db() as c: rows = c.execute('SELECT document FROM jobs ORDER BY rowid DESC LIMIT 20').fetchall()
        result = [public_job(json.loads(x[0])) for x in rows]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
