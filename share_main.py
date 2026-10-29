"""Launch one foreground service for both double-click and BAT users."""
import json
import sys
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
import webbrowser

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
PORT = 18765
URL = f'http://127.0.0.1:{PORT}'


def inspect():
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(URL + '/api/status', timeout=2) as response:
            data = json.load(response)
        root = data.get('app_root')
        return (data.get('app_id') == 'autodl-share-v1' and isinstance(root, str)
                and bool(root.strip()) and Path(root).resolve() == ROOT)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def main():
    from ui import main as serve
    # Both direct EXE and BAT run this same process in the foreground.
    sys.argv = [sys.argv[0], '--port', str(PORT)]
    try:
        serve()
    except OSError as exc:
        if getattr(exc, 'winerror', None) not in (10048, 10013) and exc.errno not in (48, 98):
            raise
        if inspect():
            print('AutoDL is already running from this folder. Opening its page; keep the original window open.', flush=True)
            webbrowser.open(URL)
            return
        print('Port 18765 is occupied by another application or another copy. Close its window and try again.', flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
