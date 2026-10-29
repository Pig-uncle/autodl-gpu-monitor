"""Existing-instance first, bounded single-instance acquisition for AutoDL."""
import copy
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time

import requests

from login_helper import load_authorization

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
LOCAL = ROOT / '.local'
CONFIG_PATH = ROOT / 'config.local.json'
JOURNAL = LOCAL / 'order.json'
GIB = 1024 ** 3
DEFAULTS = {
    'instance_uuid': '',
    'strategy': 'parallel',
    'fallback_after_minutes': 10,
    'poll_seconds': 15,
    'gpu_models': ['RTX PRO 6000'],
    'gpu_counts': [1, 2],
    'expand_disk_gb': 250,
    'max_hourly_price': None,
    'base_image_uuid': 'base-image-ec1e9vdbd3',
    'private_image_uuid': '',
    'region_sign_list': [],
    'creation_method': 'fresh',
    'clone_source_uuid': '',
    'clone_copy_data': False,
    'billing_type': 'payg',
    'billing_duration': 1,
    'max_order_price': None,
    'schedule_enabled': False,
    'schedule_at': '',
}
STOPPED = {'shutdown', 'shutdown_by_starting_error', 'shutdown_by_running_error',
           'created', 'created_clone_success', 're_initialized', 'oss_merged'}
PENDING = {'starting', 'restarting', 'creating', 'record_inserted',
           'pulling_in_creating', 'pulled_in_creating', 'created_cloning'}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def validate_config(raw, allow_missing_instance=False):
    c = copy.deepcopy(DEFAULTS)
    c.update({k: v for k, v in raw.items() if k in DEFAULTS})
    if c['strategy'] not in {'existing_only', 'existing_then_new', 'new_only', 'parallel'}:
        raise ValueError('请选择有效的抢卡策略')
    if c['creation_method'] not in {'fresh', 'clone'}:
        raise ValueError('请选择新机器创建方式')
    if c['billing_type'] not in {'payg', 'daily', 'weekly', 'monthly'}:
        raise ValueError('请选择有效的计费方式')
    duration = c['billing_duration']
    limit = {'payg': 1, 'daily': 6, 'weekly': 3, 'monthly': 12}[c['billing_type']]
    if type(duration) is not int or not 1 <= duration <= limit:
        raise ValueError(f'该计费方式的租期数量应为 1–{limit}')
    order_limit = c['max_order_price']
    if order_limit is not None and (isinstance(order_limit, bool) or not isinstance(order_limit, (int, float)) or not math.isfinite(order_limit) or order_limit <= 0):
        raise ValueError('预付订单上限必须大于 0，留空表示不限')
    if type(c['schedule_enabled']) is not bool or not isinstance(c['schedule_at'], str):
        raise ValueError('定时设置格式无效')
    if c['schedule_enabled']:
        try:
            scheduled = datetime.fromisoformat(c['schedule_at'])
            if scheduled.tzinfo is None or scheduled.utcoffset() is None:
                raise ValueError
            timestamp = scheduled.timestamp()
        except (ValueError, OverflowError, OSError):
            raise ValueError('请选择包含本地时区的有效定时日期和时间') from None
        if not allow_missing_instance and timestamp <= time.time():
            raise ValueError('定时开始时间必须晚于当前时间')
    if type(c['clone_copy_data']) is not bool:
        raise ValueError('数据盘复制选项无效')
    if not isinstance(c['clone_source_uuid'], str):
        raise ValueError('克隆源实例 UUID 必须为文本')
    c['clone_source_uuid'] = c['clone_source_uuid'].strip()
    if c['creation_method'] == 'clone' and c['strategy'] != 'existing_only':
        if not (allow_missing_instance and not c['clone_source_uuid']) and not re.fullmatch(r'[A-Za-z0-9_-]{5,100}', c['clone_source_uuid']):
            raise ValueError('请填写克隆源实例 UUID')
    if c['strategy'] != 'new_only' and not (allow_missing_instance and not c['instance_uuid']) and not re.fullmatch(r'[A-Za-z0-9_-]{5,100}', c['instance_uuid']):
        raise ValueError('请填写有效的实例 UUID')
    for key, lo, hi in [('poll_seconds', 5, 3600), ('fallback_after_minutes', 0, 10080),
                        ('expand_disk_gb', 0, 100000)]:
        value = c[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f'{key} 必须在 {lo}–{hi} 范围内')
    if int(c['expand_disk_gb']) != c['expand_disk_gb']:
        raise ValueError('数据盘扩容必须为整数 GB')
    if not isinstance(c['gpu_models'], list) or not c['gpu_models'] or any(not isinstance(x, str) or not x.strip() for x in c['gpu_models']):
        raise ValueError('至少选择一个显卡型号')
    if not isinstance(c['gpu_counts'], list) or not c['gpu_counts'] or any(type(n) is not int or not 1 <= n <= 8 for n in c['gpu_counts']):
        raise ValueError('卡数必须为 1–8 的整数列表')
    if len(set(c['gpu_counts'])) != len(c['gpu_counts']):
        raise ValueError('卡数不可重复')
    price = c['max_hourly_price']
    if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0):
        raise ValueError('价格上限必须大于 0，留空表示不限')
    for key in ['base_image_uuid', 'private_image_uuid', 'instance_uuid']:
        if not isinstance(c[key], str):
            raise ValueError(f'{key} 必须为文本')
        c[key] = c[key].strip()
    if c['strategy'] != 'existing_only' and c['creation_method'] == 'fresh' and not (c['base_image_uuid'] or c['private_image_uuid']):
        raise ValueError('新建实例需要选择公共镜像或填写私有镜像 UUID')
    if not isinstance(c['region_sign_list'], list) or any(not isinstance(x, str) for x in c['region_sign_list']):
        raise ValueError('地区必须为列表')
    return c


def load_config():
    if CONFIG_PATH.exists():
        return validate_config(json.loads(CONFIG_PATH.read_text(encoding='utf-8-sig')), allow_missing_instance=True)
    return copy.deepcopy(DEFAULTS)


class ApiError(RuntimeError):
    def __init__(self, code, msg=''):
        self.code = code
        self.msg = msg
        super().__init__(f'{code}: {msg}')


def capacity_error(exc):
    text = str(exc).lower()
    return any(t in text for t in ['gpu不足', 'gpu 不足', '空闲gpu', '空闲 gpu',
        'gpu数量不足', 'gpu 数量不足', '没有足够', '资源不足', '库存不足',
        'insufficientgpu', 'nogpu', 'not enough gpu', 'gpu资源不足',
        '显卡不足', '已被租用', '空闲卡数', '可用gpu', '可用 gpu'])


class AutoDL:
    def __init__(self):
        token = load_authorization()
        if not token:
            raise ValueError('请先扫码登录 AutoDL')
        self.session = requests.Session()
        self.session.headers.update({'Authorization': token, 'User-Agent': 'Mozilla/5.0'})

    def call(self, method, path, payload=None, params=None):
        r = self.session.request(method, 'https://www.autodl.com/api/v1/' + path,
                                 json=payload, params=params, timeout=(10, 30))
        r.raise_for_status()
        try:
            obj = r.json()
        except ValueError as exc:
            raise requests.RequestException('AutoDL 返回非 JSON 响应') from exc
        if not isinstance(obj, dict) or 'code' not in obj:
            raise requests.RequestException('AutoDL 返回格式异常')
        if obj['code'] != 'Success':
            raise ApiError(obj['code'], obj.get('msg', ''))
        return obj.get('data')

    def instances(self):
        page = 1
        while True:
            data = self.call('POST', 'instance', {'page_index': page, 'page_size': 100,
                                                'status': [], 'charge_type': []})
            rows = data['list']
            yield from rows
            if page >= data.get('max_page', 1) or not rows:
                break
            page += 1

    def instance(self, uuid):
        for item in self.instances():
            if item['uuid'] == uuid:
                return item
        raise ValueError(f'当前账号找不到实例 {uuid}，请检查 UUID 和登录账号')

    def start(self, uuid):
        return self.call('POST', 'instance/power_on', {'instance_uuid': uuid})

    def machines(self, model, count, regions, stop, charge_type='payg'):
        page = 1
        while not stop.is_set():
            data = self.call('POST', 'user/machine/list', {
                'charge_type': charge_type, 'default_order': True,
                'gpu_idle_num': count, 'gpu_type_name': [model],
                'region_sign_list': regions, 'page_index': page, 'page_size': 100,
            })
            yield from data['list']
            if page >= data.get('max_page', 1) or not data['list']:
                break
            page += 1

    def images(self):
        rows = self.call('POST', 'image/get', {})
        rows = [x for x in rows if not x.get('is_off') and x.get('chip_corp') == 'nvidia'
                and x.get('cpu_arch') == 'x86' and x.get('framework_name') == 'PyTorch']
        def version(x):
            return tuple(int(n) for n in re.findall(r'\d+', x.get('framework_version', '0')))
        return [{'uuid': x['uuid'], 'label': f"PyTorch {x['framework_version']} / CUDA {x['cuda_version']} / Python {x['python_version']}"}
                for x in sorted(rows, key=version, reverse=True)]

    def account(self):
        user = self.call('GET', 'user/detail')
        wallet = self.call('GET', 'wallet/balance')
        # Explicit allowlist: never return phone, email, open_id or credential fields.
        return {'id': user['id'], 'name': user.get('nickname') or user.get('username') or 'AutoDL 用户',
                'balance': wallet['assets'] / 1000,
                'frozen_balance': wallet.get('blocked_asset', 0) / 1000,
                'voucher_balance': wallet.get('voucher_balance', 0) / 1000,
                'updated_at': time.time()}


def eligible(machine, model, count, disk_gb):
    if machine.get('gpu_name') != model or machine.get('gpu_idle_num', 0) < count:
        return False
    if machine.get('on_line') in {3, 4, 5, 6}:
        return False
    if machine.get('binding_instance_num', 0) >= machine.get('max_instance_num', 0):
        return False
    if machine.get('max_data_disk_expand_size', 0) < int(disk_gb * GIB):
        return False
    rent = machine.get('rent_mode') or ''
    if rent:
        match = re.search(r'\d+', rent)
        if not match:
            return False
        n = int(match.group())
        if rent.startswith('Base') and count < n:
            return False
        if not rent.startswith('Base') and (n == 0 or count % n):
            return False
    return True


def billing_eligible(machine, c):
    if c['billing_type'] == 'payg':
        return True
    if not machine.get('pay_' + c['billing_type']):
        return False
    try:
        deadline = datetime.fromisoformat(machine['rent_deadline'])
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            return False
        days = {'daily': 1, 'weekly': 7, 'monthly': 30}[c['billing_type']] * c['billing_duration']
        return deadline.timestamp() > time.time() + days * 86400
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return False


def price_payload(c, machine_id, count):
    return {'charge_type': c['billing_type'], 'machine_id': machine_id, 'num': count,
            'expand_data_disk': int(c['expand_disk_gb'] * GIB), 'coupon_id_list': [], 'duration': c['billing_duration']}


def create_payload(c, machine_id, count):
    return {'instance_info': {
        'charge_type': c['billing_type'], 'machine_id': machine_id, 'req_gpu_amount': count,
        'expand_data_disk': int(c['expand_disk_gb'] * GIB),
        'base_image_uuid': '' if c['private_image_uuid'] else c['base_image_uuid'],
        'private_image_uuid': c['private_image_uuid'], 'instance_name': 'GPU Monitor',
    }, 'price_info': price_payload(c, machine_id, count)}


def clone_source(api, c):
    """Read source metadata; do not stop or modify the source instance."""
    row = api.instance(c['clone_source_uuid'])
    if row.get('status') not in {'shutdown', 'shutdown_by_starting_error', 'shutdown_by_running_error'}:
        raise ValueError('克隆源实例须先在 AutoDL 关机；程序不会自动关机')
    region = row.get('region_sign')
    if not isinstance(region, str) or not region:
        raise ValueError('无法确认克隆源地区，已停止')
    if c['region_sign_list'] and region not in c['region_sign_list']:
        raise ValueError('所选地区不包含克隆源所在区域，克隆只能在源所在区域')
    disk = row.get('expand_data_disk_size')
    if type(disk) is not int or disk < 0:
        raise ValueError('无法确认源实例数据盘扩容容量，已停止')
    if c['clone_copy_data'] and int(c['expand_disk_gb'] * GIB) < disk:
        raise ValueError(f'复制数据盘时，目标额外数据盘需至少 {math.ceil(disk / GIB)} GB')
    snap = api.call('GET', 'instance/snapshot', params={'instance_uuid': c['clone_source_uuid']})
    image = snap.get('image') or {}
    chip = image.get('chip_corp') or snap.get('chip_corp')
    arch = image.get('cpu_arch') or snap.get('cpu_arch')
    if not chip or not arch:
        raise ValueError('无法确认源实例的芯片厂商或 CPU 架构，已停止克隆')
    return {'uuid': c['clone_source_uuid'], 'region': region, 'disk_bytes': disk,
            'chip_corp': chip, 'cpu_arch': arch,
            'base_image_uuid': image.get('image_name', '') if image.get('image_type') == 'public' else '',
            'private_image_uuid': row.get('private_image_uuid', ''),
            'reproduction_uuid': row.get('reproduction_uuid', ''),
            'reproduction_id': row.get('reproduction_id') or 0,
            'cg_application_uuid': row.get('cg_application_uuid', ''),
            'cg_application_info': snap.get('cg_application_info') or {}}


def clone_payload(c, machine_id, count, source):
    return {'instance_uuid': source['uuid'], 'instance_info': {
        'charge_type': c['billing_type'], 'machine_id': machine_id, 'req_gpu_amount': count,
        'expand_data_disk': int(c['expand_disk_gb'] * GIB),
        'copy_data_disk_after_clone': c['clone_copy_data'],
        'keep_src_user_service_address_after_clone': False,
        'base_image_uuid': source['base_image_uuid'],
        'private_image_uuid': source['private_image_uuid'],
        'reproduction_uuid': source['reproduction_uuid'],
        'reproduction_id': source['reproduction_id'],
        'cg_application_uuid': source['cg_application_uuid'],
        'cg_application_info': source['cg_application_info'], 'ft_sparse': False,
    }, 'price_info': price_payload(c, machine_id, count)}


class Monitor:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.race_done = None
        self.status = {'phase': 'idle', 'message': '等待开始', 'attempts': 0,
                       'started_at': None, 'result': None, 'logs': []}

    def snapshot(self):
        with self.lock:
            data = copy.deepcopy(self.status)
            data['active'] = bool(self.thread and self.thread.is_alive())
            return data

    def log(self, text):
        # Never persist the Authorization header, even if a server error reflects it.
        token = load_authorization()
        if token:
            text = str(text).replace(token, '[redacted]')
        line = {'time': time.strftime('%H:%M:%S'), 'text': str(text)}
        with self.lock:
            self.status['message'] = str(text)
            self.status['logs'].append(line)
            self.status['logs'] = self.status['logs'][-300:]
        LOCAL.mkdir(exist_ok=True)
        with (LOCAL / 'monitor.log').open('a', encoding='utf-8') as f:
            f.write(f"{time.strftime('%Y-%m-%d')} {line['time']} {text}\n")

    def phase(self, phase):
        with self.lock:
            self.status['phase'] = phase

    def start(self, config):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('抢卡正在运行，请先停止后再修改配置')
            self.stop_event.clear()
            self.race_done = None
            self.status.update(phase='checking', attempts=0, started_at=time.time(), result=None)
            self.thread = threading.Thread(target=self._run, args=(copy.deepcopy(config),), daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.race_done is not None:
            self.race_done.set()
        with self.lock:
            idle = not (self.thread and self.thread.is_alive())
            if idle and self.status['phase'] != 'success':
                self.status['phase'] = 'stopped'
        if idle and self.status['phase'] != 'success':
            self.log('自动任务已停止。')

    def finish(self, result):
        with self.lock:
            self.status['result'] = result
            self.status['phase'] = 'success'
        self.log(f"成功：实例 {result['uuid']} 已确认 GPU 运行中。抢卡已停止。")
        if result.get('creation_method') == 'clone' and result.get('clone_copy_data'):
            self.log(f"数据盘复制状态：{result.get('clone_copy_status') or '未知'}；请在 AutoDL 控制台核对复制是否完成。")

    def _run(self, c):
        try:
            if c['schedule_enabled']:
                target = datetime.fromisoformat(c['schedule_at']).timestamp()
                self.phase('scheduled')
                self.log(f"已定时：{c['schedule_at']} 开始抢卡，等待期间保持程序运行。")
                while not self.stop_event.is_set():
                    remaining = target - time.time()
                    if remaining <= 0:
                        break
                    self.stop_event.wait(min(remaining, 30))
                if self.stop_event.is_set():
                    return
                self.log('定时时间已到，开始执行抢卡策略。')
            api = AutoDL()
            # An order may have succeeded even when the client timed out. Never resubmit it.
            if JOURNAL.exists():
                order = json.loads(JOURNAL.read_text(encoding='utf-8'))
                if not order.get('uuid'):
                    if order.get('order_uuid'):
                        raise ValueError(f"预付订单 {order['order_uuid']} 已生成或支付结果待核验。请到 AutoDL 订单页面检查；不会自动重复下单或支付。")
                    raise ValueError('上次下单结果不确定，已阻止重复购买。请先到 AutoDL 核对，再清除本地订单记录。')
                self.log(f"检测到本工具已有新租实例 {order['uuid']}，只核验该实例，不再购买。")
                self._wait_running(api, order['uuid'], c, order)
                return
            if c['creation_method'] == 'clone' and c['strategy'] != 'existing_only':
                clone_source(api, c)
                self.log('克隆源校验通过：已关机、目标容量和地区设置符合要求。')
            if c['strategy'] == 'parallel':
                self._run_parallel(c)
                return
            if c['strategy'] != 'new_only':
                self.phase('existing')
                self.log(f"优先自动开机 {c['instance_uuid']}，每 {c['poll_seconds']} 秒重试。")
                deadline = time.monotonic() + c['fallback_after_minutes'] * 60
                while not self.stop_event.is_set():
                    try:
                        row = api.instance(c['instance_uuid'])
                    except requests.RequestException:
                        self.log('读取原实例状态网络异常，稍后重试。')
                        self.stop_event.wait(c['poll_seconds'])
                        continue
                    status = row['status']
                    self.log(f'原实例状态：{status}')
                    if status == 'running':
                        if row.get('start_mode') == 'non_gpu':
                            raise ValueError('原实例处于无卡运行模式，请在 AutoDL 关机后再运行自动 GPU 开机')
                        self.finish({'uuid': row['uuid'], 'source': 'existing'})
                        return
                    if status in STOPPED:
                        if self.stop_event.is_set():
                            break
                        with self.lock:
                            self.status['attempts'] += 1
                        try:
                            api.start(c['instance_uuid'])
                            self.log('开机请求已接受，等待 GPU 运行状态确认。')
                            # No fallback while an accepted power-on is in progress.
                            self._wait_running(api, c['instance_uuid'], c, {'source': 'existing'})
                            return
                        except ApiError as exc:
                            if not capacity_error(exc):
                                raise
                            self.log(f'原实例 GPU 暂不足：{exc}')
                        except requests.RequestException as exc:
                            raise ValueError('开机响应不确定，已暂停；请检查原实例状态后重新开始') from exc
                        # A mutation timeout is deliberately fatal: its outcome is uncertain.
                        if c['strategy'] == 'existing_then_new' and time.monotonic() >= deadline:
                            self.log('原实例等待时间已到，切换抢新机器。')
                            break
                    elif status not in PENDING and status != 'shutting_down':
                        raise ValueError(f'原实例状态 {status} 无法自动开机，请在控制台处理')
                    self.stop_event.wait(c['poll_seconds'])
                if c['strategy'] == 'existing_only' or self.stop_event.is_set():
                    return
            self._buy_new(api, c)
        except Exception as exc:
            self.phase('error')
            self.log(f'已停止：{exc}')
        finally:
            if self.stop_event.is_set() and self.status['phase'] not in {'success', 'error'}:
                self.phase('stopped')
                self.log('监控已停止。已接受的开机/下单请求仍会由 AutoDL 完成。')

    def _wait_running(self, api, uuid, c, result):
        self.phase('waiting')
        visible_deadline = time.monotonic() + 120
        clone_start_accepted = False
        while not self.stop_event.is_set():
            try:
                row = api.instance(uuid)
            except ValueError:
                if result.get('source') != 'new' or time.monotonic() >= visible_deadline:
                    raise
                self.log('新实例尚未出现在实例列表，等待创建记录同步。')
                self.stop_event.wait(c['poll_seconds'])
                continue
            except requests.RequestException:
                self.log('查询实例状态网络异常，继续核验，不重复下单。')
                self.stop_event.wait(c['poll_seconds'])
                continue
            self.log(f"等待实例 {uuid}：{row['status']}")
            if row['status'] == 'running':
                if row.get('start_mode') == 'non_gpu':
                    raise ValueError('实例处于无卡运行模式，无法认定 GPU 抢卡成功')
                if result.get('creation_method') == 'clone':
                    result = {**result, 'clone_copy_status': (row.get('additional_info') or {}).get('copy_data_disk_after_clone_status', '')}
                self.finish({**result, 'uuid': uuid})
                return
            if result.get('creation_method') == 'clone' and row['status'] == 'created_clone_success':
                if not clone_start_accepted:
                    try:
                        api.start(uuid)
                        clone_start_accepted = True
                        self.log('系统盘克隆已完成，克隆实例开机请求已接受。')
                    except ApiError as exc:
                        if not capacity_error(exc):
                            raise
                        self.log('克隆实例 GPU 暂不足，将继续尝试开机。')
                    except requests.RequestException as exc:
                        raise ValueError('克隆实例开机结果不确定，请检查 AutoDL 控制台') from exc
                self.stop_event.wait(c['poll_seconds'])
                continue
            if row['status'] not in PENDING:
                raise ValueError(f"实例 {uuid} 当前为 {row['status']}，请在控制台检查；不会重复租用")
            self.stop_event.wait(c['poll_seconds'])

    def _run_parallel(self, c):
        self.phase('parallel')
        self.log('同步抢卡已启动：原实例开机和新机器搜索同时进行；任一路请求被接受后，另一侧停止提交。')
        race = {'lock': threading.Lock(), 'done': threading.Event(), 'owner': None}
        self.race_done = race['done']

        def existing():
            api = AutoDL()
            while not self.stop_event.is_set() and not race['done'].is_set():
                try:
                    # Serialize only paid mutations; market queries run in parallel.
                    with race['lock']:
                        if race['done'].is_set() or self.stop_event.is_set():
                            return
                        row = api.instance(c['instance_uuid'])
                        state = row['status']
                        self.log(f'[原实例] 当前状态：{state}')
                        if state == 'running':
                            if row.get('start_mode') == 'non_gpu':
                                self.log('[原实例] 无卡运行中，等待关机后尝试 GPU 开机；新机器搜索继续。')
                            else:
                                race['owner'] = 'autodl-existing'
                                race['done'].set()
                                self.finish({'uuid': row['uuid'], 'source': 'existing'})
                                return
                        elif state in PENDING:
                            race['owner'] = 'autodl-existing'
                            race['done'].set()
                        elif state in STOPPED:
                            with self.lock:
                                self.status['attempts'] += 1
                            try:
                                api.start(c['instance_uuid'])
                            except ApiError as exc:
                                if not capacity_error(exc):
                                    race['owner'] = 'autodl-existing'
                                    race['done'].set()
                                    raise
                                self.log(f'[原实例] GPU 暂不足：{exc}')
                            except requests.RequestException as exc:
                                race['owner'] = 'autodl-existing'
                                race['done'].set()
                                raise ValueError('原实例开机响应不确定，已停止两路提交，请核对实例状态') from exc
                            else:
                                race['owner'] = 'autodl-existing'
                                race['done'].set()
                                self.log('[原实例] 开机已接受，停止新租提交并等待运行。')
                        elif state != 'shutting_down':
                            raise ValueError(f'原实例状态 {state} 无法自动开机')
                    if race['done'].is_set():
                        self._wait_running(api, c['instance_uuid'], c, {'source': 'existing'})
                        return
                except requests.RequestException:
                    self.log('[原实例] 状态查询网络异常，下一轮重试。')
                race['done'].wait(c['poll_seconds'])

        def guarded(fn):
            try:
                fn()
            except Exception as exc:
                # Coordinate errors under the same lock as mutations. No other branch
                # can start a paid operation after an uncertain response.
                with race['lock']:
                    if race['done'].is_set() and race['owner'] not in {None, threading.current_thread().name}:
                        return
                    race['done'].set()
                    self.stop_event.set()
                self.phase('error')
                self.log(f'同步抢卡已停止：{exc}')

        workers = [threading.Thread(name='autodl-existing', target=guarded, args=(existing,), daemon=True),
                   threading.Thread(name='autodl-new', target=guarded, args=(lambda: self._buy_new(AutoDL(), c, race),), daemon=True)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

    def _buy_new(self, api, c, race=None):
        if race is None:
            self.phase('new')
        self.log(f"开始抢新机器：{' → '.join(c['gpu_models'])}；卡数 {c['gpu_counts']}；额外数据盘 {c['expand_disk_gb']} GB；最多新租 1 台。")
        while not self.stop_event.is_set() and not (race and race['done'].is_set()):
            try:
                source = clone_source(api, c) if c['creation_method'] == 'clone' else None
                regions = [source['region']] if source else c['region_sign_list']
                # Check the original again before considering any new paid instance.
                if c['strategy'] == 'existing_then_new':
                    row = api.instance(c['instance_uuid'])
                    if row['status'] == 'running':
                        if row.get('start_mode') == 'non_gpu':
                            raise ValueError('原实例已无卡开机，请先到控制台检查')
                        self.finish({'uuid': row['uuid'], 'source': 'existing'})
                        return
                    if row['status'] not in STOPPED:
                        raise ValueError(f"原实例已变为 {row['status']}，暂停购买以避免同时计费")
                for model in c['gpu_models']:
                    # Fetch once for the minimum acceptable count; filter counts locally.
                    rows = list(api.machines(model, min(c['gpu_counts']), regions, self.stop_event, c['billing_type']))
                    for count in c['gpu_counts']:
                        candidates = [m for m in rows if eligible(m, model, count, c['expand_disk_gb'])
                                      and billing_eligible(m, c)
                                      and (not source or (m.get('region_sign') == source['region']
                                           and m.get('chip_corp') == source['chip_corp']
                                           and m.get('cpu_arch') == source['cpu_arch']))]
                        candidates.sort(key=lambda m: m.get('payg_price', float('inf')))
                        self.log(f'{model} × {count}：满足空闲卡和扩容要求的机器 {len(candidates)} 台')
                        for machine in candidates:
                            if self.stop_event.is_set() or (race and race['done'].is_set()):
                                return
                            mid = machine['machine_id']
                            try:
                                preview = api.call('POST', 'order/price/preview', price_payload(c, mid, count))
                            except ApiError as exc:
                                if capacity_error(exc):
                                    self.log(f'{mid} 资源已被抢占，继续寻找。')
                                    continue
                                raise
                            raw_hourly = disk_daily = upfront = None
                            if c['billing_type'] == 'payg':
                                # AutoDL GPU billing is hourly; expanded data disk is billed daily.
                                raw_hourly = machine['payg_price'] * count / 1000
                                disk_daily = (preview.get('data_disk_info') or {}).get('data_disk_deal_daily_price')
                                if disk_daily is None:
                                    raise ValueError('报价缺少数据盘费用，已停止下单')
                                self.log(f"候选 {mid}：{model} × {count}，GPU 标价 {raw_hourly:g} 元/小时；扩容盘 {disk_daily/1000:g} 元/日。")
                                equivalent = raw_hourly + disk_daily / 1000 / 24
                                if c['max_hourly_price'] is not None and equivalent > c['max_hourly_price']:
                                    continue
                            else:
                                amount = preview.get('deal_price')
                                if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
                                    raise ValueError('预付报价缺少有效订单总价，已停止下单')
                                upfront = amount / 1000
                                self.log(f"候选 {mid}：{model} × {count}，{c['billing_type']} × {c['billing_duration']}，预付报价 {upfront:g} 元。")
                                if c['expand_disk_gb']:
                                    disk_prepaid = preview.get('prepay_expand_data_disk')
                                    if type(disk_prepaid) is not bool:
                                        raise ValueError('无法确认预付数据盘的计费方式，已停止下单')
                                    if not disk_prepaid:
                                        disk_daily = (preview.get('data_disk_info') or {}).get('data_disk_deal_daily_price')
                                        if type(disk_daily) not in (int, float) or not math.isfinite(disk_daily) or disk_daily < 0:
                                            raise ValueError('无法确认数据盘日费，已停止下单')
                                        self.log(f'扩容数据盘另计 {disk_daily/1000:g} 元/日；该费用不计入预付订单上限。')
                                if c['max_order_price'] is not None and upfront > c['max_order_price']:
                                    continue
                            order = {'source': 'new', 'machine_id': mid, 'gpu_model': model,
                                     'gpu_count': count, 'expand_disk_gb': c['expand_disk_gb'],
                                     'gpu_hourly_price': raw_hourly, 'disk_daily_price': disk_daily / 1000 if disk_daily is not None else None,
                                     'created_at': time.time(), 'state': 'submitting',
                                     'billing_type': c['billing_type'], 'billing_duration': c['billing_duration'],
                                     'quoted_order_price': upfront,
                                     'creation_method': c['creation_method'],
                                     'clone_copy_data': c['clone_copy_data'] if source else False}
                            if self._submit_new(api, c, mid, count, order, race, source):
                                return
            except requests.RequestException as exc:
                if JOURNAL.exists():
                    raise ValueError('下单响应不确定，已停止防止重复购买；请到 AutoDL 核对订单') from exc
                self.log('资源查询网络异常，将在下一轮重试。')
            (race['done'] if race else self.stop_event).wait(c['poll_seconds'])

    def _submit_new(self, api, c, mid, count, order, race=None, source=None):
        mutex = race['lock'] if race else threading.Lock()
        existing_uuid = None
        with mutex:
            if self.stop_event.is_set() or (race and race['done'].is_set()):
                return True
            if race:
                # Catch an external/manual power-on before committing a new order.
                row = api.instance(c['instance_uuid'])
                if (row['status'] == 'running' or row['status'] in PENDING) and row.get('start_mode') != 'non_gpu':
                    race['owner'] = 'autodl-new'
                    race['done'].set()
                    existing_uuid = row['uuid']
                elif row['status'] not in STOPPED and not (row['status'] == 'running' and row.get('start_mode') == 'non_gpu'):
                    return False
            if existing_uuid is None:
                if source:
                    latest = clone_source(api, c)
                    if latest != source:
                        raise ValueError('克隆源信息已变化，请重新检查后再开始')
                atomic_json(JOURNAL, order)
                with self.lock:
                    self.status['attempts'] += 1
                try:
                    endpoint = 'order/instance/' + ('clone/' if source else 'create/') + ('payg' if c['billing_type'] == 'payg' else 'prepay')
                    payload = clone_payload(c, mid, count, source) if source else create_payload(c, mid, count)
                    data = api.call('POST', endpoint, payload)
                except ApiError as exc:
                    if capacity_error(exc):
                        JOURNAL.unlink()
                        self.log(f'{mid} 下单时资源不足，继续寻找。')
                        return False
                    if race:
                        race['owner'] = 'autodl-new'
                        race['done'].set()
                    raise
                except Exception:
                    if race:
                        race['owner'] = 'autodl-new'
                        race['done'].set()
                    raise
                if race:
                    race['owner'] = 'autodl-new'
                    race['done'].set()
                if c['billing_type'] != 'payg':
                    order_uuid = data.get('uuid') if isinstance(data, dict) else None
                    if not isinstance(order_uuid, str) or not order_uuid:
                        raise ValueError('预付订单响应缺少订单号，请到 AutoDL 核对；不会再次下单')
                    order.update(order_uuid=order_uuid, state='order_created')
                    atomic_json(JOURNAL, order)
                    if self.stop_event.is_set():
                        self.log(f'已停止；预付订单 {order_uuid} 已生成，请在 AutoDL 订单页面核对。')
                        return True
                    detail = api.call('GET', 'order', params={'order_uuid': order_uuid})
                    detail = detail.get('order') if isinstance(detail, dict) else None
                    info = detail.get('price_info') if isinstance(detail, dict) else None
                    if (not isinstance(detail, dict) or not isinstance(info, dict)
                            or detail.get('uuid') != order_uuid or detail.get('status') != 'unpaid'
                            or detail.get('machine_id') != mid or detail.get('charge_type') != c['billing_type']
                            or detail.get('order_type') != ('clone_instance' if source else 'create_instance')
                            or info.get('num') != count or info.get('duration') != c['billing_duration']
                            or info.get('expand_data_disk') != int(c['expand_disk_gb'] * GIB)):
                        raise ValueError(f'预付订单 {order_uuid} 详情无法核实，请到 AutoDL 订单页面处理')
                    final_amount = detail.get('deal_price')
                    if type(final_amount) not in (int, float) or not math.isfinite(final_amount) or final_amount < 0:
                        raise ValueError(f'预付订单 {order_uuid} 金额无法核实，请到 AutoDL 订单页面处理')
                    order['order_price'] = final_amount / 1000
                    atomic_json(JOURNAL, order)
                    if c['max_order_price'] is not None and order['order_price'] > c['max_order_price']:
                        raise ValueError(f"预付订单金额 {order['order_price']:g} 元超过上限，请到 AutoDL 订单页面处理")
                    if self.stop_event.is_set():
                        return True
                    order['state'] = 'paying'
                    atomic_json(JOURNAL, order)
                    self.log(f"支付预付订单 {order_uuid}：{order['order_price']:g} 元。")
                    try:
                        data = api.call('PUT', 'order/pay', {'order_uuid': order_uuid})
                    except Exception as exc:
                        raise ValueError(f'预付订单 {order_uuid} 支付状态不确定，请到 AutoDL 订单页面核对；不会自动重复支付') from exc
                uuid = data if isinstance(data, str) else (data or {}).get('uuid')
                if not uuid:
                    raise ValueError('下单已返回成功但缺少实例 UUID，请到 AutoDL 核对；不会再次下单')
                order.update(uuid=uuid, state='accepted')
                atomic_json(JOURNAL, order)
        if existing_uuid:
            self.log('[原实例] 已在开机或运行，停止新租提交。')
            self._wait_running(api, existing_uuid, c, {'source': 'existing'})
        else:
            self.log(f'已成功下单 {uuid}，停止其他提交，等待实际运行。')
            self._wait_running(api, uuid, c, order)
        return True
