"""Persistent, versioned naming rules shared by every new batch."""
import json
from pathlib import Path
from settings import data_root

DEFAULT = {'version': 1, 'format': '对象｜具体用途', 'separator': '｜',
           'max_length': 32, 'preserve_clear_titles': True,
           'instructions': '对象用稳定的项目或主题名称；用途写具体任务或成果，不用开场白，不随批次换格式。'}


def validate_policy(value):
    if set(value) != set(DEFAULT) or value['format'] != '对象｜具体用途':
        raise ValueError('Unsupported naming policy fields/format')
    if type(value['version']) is not int or value['version'] < 1:
        raise ValueError('Policy version must be positive')
    if value['separator'] not in ('｜', ' | ', ' · ', ' — '):
        raise ValueError('Unsupported separator')
    if type(value['max_length']) is not int or not 8 <= value['max_length'] <= 80:
        raise ValueError('max_length must be 8..80')
    if type(value['preserve_clear_titles']) is not bool or not isinstance(value['instructions'], str):
        raise ValueError('Invalid policy options')
    return value


def load_policy():
    path = data_root() / 'naming-policy.json'
    if path.exists():
        return validate_policy(json.loads(path.read_text(encoding='utf-8-sig')))
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids overwriting another process/user's rule.
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(DEFAULT, stream, ensure_ascii=False, indent=2)
    except FileExistsError:
        return load_policy()
    return dict(DEFAULT)


def validate_title(title, policy):
    if not isinstance(title, str) or title != title.strip() or not 1 <= len(title) <= policy['max_length']:
        raise ValueError('Title length/whitespace violates saved naming policy')
    if any(ord(c) < 32 for c in title):
        raise ValueError('Title contains control characters')
    parts = title.split(policy['separator'])
    if len(parts) != 2 or any(not p.strip() for p in parts):
        raise ValueError('Title must follow saved object/separator/purpose format')
    return title
