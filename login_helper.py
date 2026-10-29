"""AutoDL login; credentials stay in the ignored .local directory."""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
import time
from urllib.parse import urlparse

import requests

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
AUTH_PATH = ROOT / '.local' / 'auth.json'
BASE = 'https://www.autodl.com/api/v1/'


def _phone_api(path, payload):
    try:
        response = requests.post(BASE + path, json=payload,
                                 headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError('连接 AutoDL 登录服务失败，请稍后重试') from exc
    if not isinstance(result, dict) or result.get('code') != 'Success':
        code = result.get('code', 'Unknown') if isinstance(result, dict) else 'Unknown'
        # Do not relay server messages that might echo the phone or code.
        raise RuntimeError(f'AutoDL 未通过请求（{code}），请检查手机号、验证码或稍后重试')
    return result.get('data') or {}


def _validate_phone(phone, phone_area):
    if not isinstance(phone, str) or not re.fullmatch(r'\d{5,20}', phone):
        raise ValueError('请输入有效的手机号，仅填写数字')
    if not isinstance(phone_area, str) or not re.fullmatch(r'\+\d{1,4}', phone_area):
        raise ValueError('请输入有效的国际区号，例如 +86')


def send_phone_code(phone, phone_area, captcha_verify_param):
    _validate_phone(phone, phone_area)
    if not isinstance(captcha_verify_param, (str, dict)) or not captcha_verify_param:
        raise ValueError('请先完成 AutoDL 人机验证')
    _phone_api('send_message', {'phone': phone, 'phone_area': phone_area,
               'sms_type': 'login', 'captcha_verify_param': captcha_verify_param})


def phone_login(phone, phone_area, code):
    _validate_phone(phone, phone_area)
    if not isinstance(code, str) or not re.fullmatch(r'\d{4,10}', code):
        raise ValueError('请输入短信验证码')
    result = _phone_api('fast_login', {'phone': phone, 'phone_area': phone_area,
                         'v_code': code, 'password': '', 'device_fingerprint': ''})
    ticket = result.get('ticket') if isinstance(result, dict) else None
    if not ticket:
        raise RuntimeError('AutoDL 登录结果缺少凭据，请重试')
    data = _phone_api('passport', {'ticket': ticket})
    token = data.get('token') if isinstance(data, dict) else None
    if not token:
        raise RuntimeError('AutoDL 未返回登录令牌，请重试')
    update_config_authorization(token)


def update_config_authorization(token):
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = AUTH_PATH.with_suffix('.tmp')
    temp.write_text(json.dumps({'authorization': token}), encoding='utf-8')
    os.replace(temp, AUTH_PATH)


def load_authorization():
    if os.environ.get('AUTODL_AUTHORIZATION'):
        return os.environ['AUTODL_AUTHORIZATION'].strip()
    if AUTH_PATH.exists():
        return json.loads(AUTH_PATH.read_text(encoding='utf-8'))['authorization']
    return ''


def login_and_update_config(phone=None, password=None, *, open_qr=True, cancel_event=None):
    session = requests.Session()
    session.headers['User-Agent'] = 'Mozilla/5.0'

    def call(method, path, payload=None):
        r = session.request(method, BASE + path, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get('code') != 'Success':
            raise RuntimeError(f"Login API failed: {data.get('code')}")
        return data.get('data') or {}

    if phone is not None and password is not None:
        data = call('POST', 'new_login', {
            'phone': str(phone), 'phone_area': '+86',
            'password': hashlib.sha1(str(password).encode()).hexdigest(),
        })
        ticket = data['ticket']
    else:
        data = call('GET', 'wx/login')
        qr_url = data['qrcode_url']
        if urlparse(qr_url).scheme != 'https':
            raise RuntimeError('QR URL must use HTTPS')
        qr = session.get(qr_url, timeout=30)
        qr.raise_for_status()
        AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        qr_path = AUTH_PATH.parent / 'login-qr.png'
        qr_path.write_bytes(qr.content)
        print(f'Scan with your AutoDL WeChat and confirm login: {qr_path}', flush=True)
        if open_qr and hasattr(os, 'startfile'):
            os.startfile(str(qr_path))
        deadline = time.monotonic() + min(data.get('expire_seconds', 180), 300)
        ticket = None
        while time.monotonic() < deadline:
            if cancel_event and cancel_event.is_set():
                raise RuntimeError('登录已取消')
            r = session.post(BASE + 'wx/polling/query', json={'uuid': data['uuid']}, timeout=35)
            r.raise_for_status()
            poll = r.json()
            ticket = (poll.get('data') or {}).get('ticket')
            if ticket:
                break
            if cancel_event:
                cancel_event.wait(2)
            else:
                time.sleep(2)
        if not ticket:
            raise RuntimeError('QR expired; run login again')
    token = call('POST', 'passport', {'ticket': ticket})['token']
    if cancel_event and cancel_event.is_set():
        raise RuntimeError('登录已取消')
    update_config_authorization(token)
    print('Login successful. Token saved locally in .local/auth.json.', flush=True)
    return token


def login(phone=None, password=None):
    return login_and_update_config(phone, password)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    try:
        login_and_update_config(open_qr=not args.no_open)
    except (RuntimeError, requests.RequestException, KeyError) as exc:
        print(f'Login not completed: {exc}', flush=True)
        raise SystemExit(1)
