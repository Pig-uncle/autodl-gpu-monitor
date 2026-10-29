"""Local-only Chinese control panel. Run with python main.py."""
import argparse
import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
import webbrowser

from login_helper import (AUTH_PATH, load_authorization, login_and_update_config,
                          send_phone_code, phone_login)
from monitor import (AutoDL, CONFIG_PATH, JOURNAL, LOCAL, ROOT, Monitor,
                     atomic_json, load_config, validate_config)


class App:
    def __init__(self):
        self.monitor = Monitor()
        self.lock = threading.RLock()
        self.login_status = 'idle'
        self.login_message = ''
        self.login_thread = None
        self.login_cancel = threading.Event()
        self.account_cache = None
        self.account_cached_at = 0
        self.account_lock = threading.Lock()

    def account(self, force=False):
        with self.account_lock:
            if force or not self.account_cache or time.monotonic() - self.account_cached_at > 30:
                self.account_cache = AutoDL().account()
                self.account_cached_at = time.monotonic()
            return self.account_cache

    def login(self):
        with self.lock:
            if self.login_thread and self.login_thread.is_alive():
                return
            if self.monitor.snapshot()['active']:
                raise ValueError('请先停止抢卡，再重新登录')
            qr = LOCAL / 'login-qr.png'
            if qr.exists():
                qr.unlink()
            self.login_status = 'waiting'
            self.login_message = '正在生成二维码'
            self.login_cancel.clear()
            def run():
                try:
                    login_and_update_config(open_qr=False, cancel_event=self.login_cancel)
                    self.login_status = 'success'
                    self.login_message = '登录成功'
                    self.account_cached_at = 0
                except Exception:
                    self.login_status = 'idle' if self.login_cancel.is_set() else 'error'
                    self.login_message = '登录已取消' if self.login_cancel.is_set() else '登录未完成或二维码过期，请重试'
            self.login_thread = threading.Thread(target=run, daemon=True)
            self.login_thread.start()


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def allowed(self):
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in hosts:
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + h for h in hosts}:
                return False
            return True

        def send(self, value, status=200, mime='application/json; charset=utf-8'):
            if not isinstance(value, bytes):
                value = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(value)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            self.wfile.write(value)

        def do_GET(self):
            if not self.allowed():
                return self.send({'error': '仅允许本机访问'}, 403)
            path = self.path.split('?')[0]
            try:
                if path == '/':
                    return self.send((Path(__file__).resolve().parent / 'web' / 'index.html').read_bytes(), mime='text/html; charset=utf-8')
                if path == '/api/status':
                    return self.send({**app.monitor.snapshot(), 'app_id': 'autodl-share-v1', 'app_root': str(ROOT.resolve()), 'logged_in': bool(load_authorization()),
                        'login_status': app.login_status, 'login_message': app.login_message,
                        'qr_ready': app.login_status == 'waiting' and (LOCAL / 'login-qr.png').exists(),
                        'has_order_record': JOURNAL.exists()})
                if path == '/api/config':
                    return self.send(load_config())
                if path == '/api/account':
                    return self.send(app.account(force='refresh=1' in self.path))
                if path == '/api/qr' and app.login_status == 'waiting':
                    return self.send((LOCAL / 'login-qr.png').read_bytes(), mime='image/png')
                if path == '/api/catalog':
                    api = AutoDL()
                    models = [x['gpu_name'] for x in api.call('GET', 'machine/gpu_type')]
                    regions = api.call('GET', 'region/list')
                    return self.send({'models': models, 'images': api.images(), 'regions': regions})
                return self.send({'error': '未找到'}, 404)
            except Exception as exc:
                return self.send({'error': str(exc)}, 400)

        def do_POST(self):
            if not self.allowed() or not self.headers.get('Content-Type', '').startswith('application/json'):
                return self.send({'error': '请求来源或格式不正确'}, 403)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 65536:
                    raise ValueError('请求过大')
                raw = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(raw, dict):
                    raise ValueError('配置格式错误')
                with app.lock:
                    if self.path in {'/api/config', '/api/start'}:
                        if app.monitor.snapshot()['active']:
                            raise ValueError('请先停止抢卡，再保存或启动')
                        if app.login_thread and app.login_thread.is_alive():
                            raise ValueError('请先完成扫码登录')
                        c = validate_config(raw, allow_missing_instance=self.path == '/api/config')
                        if self.path == '/api/start':
                            if not load_authorization():
                                raise ValueError('请先扫码登录')
                        atomic_json(CONFIG_PATH, c)
                        if self.path == '/api/start':
                            app.monitor.start(c)
                        return self.send({'ok': True})
                    if self.path == '/api/stop':
                        app.monitor.stop()
                        return self.send({'ok': True})
                    if self.path == '/api/login':
                        app.login()
                        return self.send({'ok': True})
                    if self.path == '/api/login/cancel':
                        app.login_cancel.set()
                        return self.send({'ok': True})
                    if self.path in {'/api/phone/send-code', '/api/phone/login'}:
                        if app.monitor.snapshot()['active']:
                            raise ValueError('请先停止抢卡，再重新登录')
                        if app.login_thread and app.login_thread.is_alive():
                            raise ValueError('微信扫码登录正在进行，请先关闭并等待二维码过期后再切换')
                        phone, area = raw.get('phone'), raw.get('phone_area')
                        if self.path.endswith('send-code'):
                            send_phone_code(phone, area, raw.get('captcha_verify_param'))
                        else:
                            phone_login(phone, area, raw.get('code'))
                            app.account_cache = None
                            app.account_cached_at = 0
                            app.login_status = 'success'
                            app.login_message = '登录成功'
                        return self.send({'ok': True})
                    if self.path == '/api/inspect':
                        c = validate_config(raw)
                        if c['strategy'] == 'new_only':
                            return self.send({'message': '当前选择只抢新机器，无需检查原实例'})
                        row = AutoDL().instance(c['instance_uuid'])
                        labels = {'running': '运行中', 'shutdown': '已关机', 'starting': '开机中'}
                        size = row.get('expand_data_disk_size', 0) / 1024 ** 3
                        return self.send({'message': f"原实例：{labels.get(row['status'], row['status'])} · {row.get('req_gpu_amount', '?')} 张卡 · 已扩容 {size:g} GB · {row.get('payg_price', 0)/1000:g} 元/小时"})
                    if self.path == '/api/reset-order':
                        if app.monitor.snapshot()['active']:
                            raise ValueError('请先停止抢卡')
                        if JOURNAL.exists():
                            # Retain audit history instead of deleting evidence of a paid order.
                            JOURNAL.rename(LOCAL / f'order-archived-{time.time_ns()}.json')
                        return self.send({'ok': True})
                return self.send({'error': '未找到'}, 404)
            except Exception as exc:
                return self.send({'error': str(exc)}, 400)
    return Handler


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    parser = argparse.ArgumentParser(description='AutoDL 自动开机 / 抢卡控制台')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--start-monitor', action='store_true')
    args = parser.parse_args()
    app = App()
    server = LocalServer(('127.0.0.1', args.port), make_handler(app))
    if not CONFIG_PATH.exists():
        atomic_json(CONFIG_PATH, load_config())
    url = f'http://127.0.0.1:{args.port}'
    print(f'AutoDL UI: {url}', flush=True)
    if args.start_monitor:
        app.monitor.start(load_config())
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.monitor.stop()
        server.server_close()


if __name__ == '__main__':
    main()
