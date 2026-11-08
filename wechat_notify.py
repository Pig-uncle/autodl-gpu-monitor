"""AutoDL official WeChat message API, with local-only developer credentials."""
import json
import os
from pathlib import Path
import sys

import requests

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
SETTINGS = ROOT / '.local' / 'wechat-notification.json'
URL = 'https://www.autodl.com/api/v1/wechat/message/send'


def load_settings():
    if not SETTINGS.exists():
        return {'enabled': False, 'token': '', 'account_id': None}
    try:
        data = json.loads(SETTINGS.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or type(data.get('enabled')) is not bool:
            raise ValueError
        token = data.get('token', '')
        if not isinstance(token, str):
            raise ValueError
        return {'enabled': data['enabled'], 'token': token, 'account_id': data.get('account_id')}
    except (OSError, UnicodeError, ValueError):
        raise ValueError('微信通知配置无法读取，请重新设置') from None


def save_settings(enabled, token, account_id, clear=False):
    if type(enabled) is not bool or type(clear) is not bool or not isinstance(token, str):
        raise ValueError('微信通知设置格式错误')
    previous = load_settings()
    token = token.strip()
    if token and (len(token) > 1024 or any(c.isspace() or ord(c) < 32 for c in token)):
        raise ValueError('开发者 Token 格式无效')
    if clear:
        token, bound = '', None
        enabled = False
    elif token:
        if account_id is None:
            raise ValueError('请先登录与开发者 Token 对应的 AutoDL 账号')
        bound = str(account_id)
    else:
        token, bound = previous['token'], previous['account_id']
    if enabled and not token:
        raise ValueError('请先填写 AutoDL 开发者 Token')
    if enabled and (account_id is None or str(account_id) != str(bound)):
        raise ValueError('当前账号与保存 Token 时的账号不一致，请重新填写对应的 Token')
    data = {'enabled': enabled, 'token': token, 'account_id': bound}
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    temp = SETTINGS.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, SETTINGS)


def send_message(settings, account_id, title, name, content):
    token = settings.get('token')
    if not token:
        raise ValueError('请先设置 AutoDL 开发者 Token')
    if account_id is None or str(account_id) != str(settings.get('account_id')):
        raise ValueError('当前账号与通知 Token 绑定的账号不一致，请重新设置')
    try:
        response = requests.post(URL, headers={'Authorization': token},
                                 json={'title': title, 'name': name, 'content': content},
                                 timeout=(10, 15))
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        raise ValueError('微信通知请求失败，请检查网络和开发者 Token') from None
    if not isinstance(data, dict) or data.get('code') != 'Success':
        raise ValueError('AutoDL 未确认通知发送成功，请检查 Token、微信绑定及推送额度')
