"""Local order records and reusable settings; not an AutoDL billing statement."""
import copy
import hashlib
import json

from monitor import DEFAULTS


ORDER_FIELDS = ('created_at', 'state', 'uuid', 'order_uuid', 'source', 'machine_id',
                'gpu_model', 'gpu_count', 'expand_disk_gb', 'gpu_hourly_price',
                'disk_daily_price', 'quoted_order_price', 'order_price',
                'billing_type', 'billing_duration', 'creation_method', 'clone_copy_data')


def template_for_order(order):
    raw = order.get('template')
    if isinstance(raw, dict):
        template = {k: copy.deepcopy(raw[k]) for k in DEFAULTS if k in raw}
        complete = all(k in raw for k in DEFAULTS)
    else:
        template = {}
        for source, target in [('gpu_model', 'gpu_models'), ('gpu_count', 'gpu_counts')]:
            if order.get(source) is not None:
                template[target] = [order[source]]
        for key in ('expand_disk_gb', 'billing_type', 'billing_duration', 'creation_method', 'clone_copy_data'):
            if key in order:
                template[key] = order[key]
        complete = False
    template['schedule_enabled'] = False
    template['schedule_at'] = ''
    return template, complete


def list_history(local, journal):
    files = ([journal] if journal.exists() else []) + list(local.glob('order-archived-*.json'))
    entries = []
    for path in files:
        try:
            content = path.read_bytes()
            order = json.loads(content.decode('utf-8-sig'))
            if not isinstance(order, dict):
                raise ValueError('invalid record')
            item = {k: order[k] for k in ORDER_FIELDS if k in order}
            item.update(id='current' if path == journal else path.name,
                        current=path == journal, fingerprint=hashlib.sha256(content).hexdigest(),
                        recorded_at=order.get('created_at') or path.stat().st_mtime)
            item['template'], item['template_complete'] = template_for_order(order)
            entries.append(item)
        except (OSError, UnicodeError, ValueError):
            entries.append({'id': 'current' if path == journal else path.name,
                            'current': path == journal, 'unreadable': True,
                            'recorded_at': 0})
    return sorted(entries, key=lambda x: x['recorded_at'] if isinstance(x['recorded_at'], (int, float)) else 0, reverse=True)
