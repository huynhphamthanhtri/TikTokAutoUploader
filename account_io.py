import re


DEFAULT_FIELDS = (
    'name',
    'email',
    'password',
    'idTiktok',
    'proxy',
    'cookie',
    'auth2fa',
    'passmail',
    'mailBackup',
    'passMailBackup',
    'note',
)

SENSITIVE_FIELDS = frozenset({'password', 'cookie', 'auth2fa', 'passmail', 'passMailBackup'})

LEGACY_FIELDS = ('name', 'cookie', 'proxy', 'idTiktok')

DEFAULT_FORMAT = '|'.join(DEFAULT_FIELDS)
LEGACY_FORMAT = 'Tên|Cookie|Proxy|ID TikTok'

DEFAULT_PROXY_TYPE = 'http'

# exchange field (trao đổi) -> internal config key. name là khóa profile, không nằm trong config.
INTERNAL_FIELD_MAP = {
    'name': None,
    'email': 'email',
    'password': 'password',
    'idTiktok': 'tiktok_id',
    'proxy': 'proxy_string',
    'cookie': 'cookie_str',
    'auth2fa': 'auth2fa',
    'passmail': 'passmail',
    'mailBackup': 'mail_backup',
    'passMailBackup': 'pass_mail_backup',
    'note': 'note',
}

INTERNAL_TO_EXCHANGE = {v: k for k, v in INTERNAL_FIELD_MAP.items() if v}

# Các trường account mặc định cần đảm bảo khi migration profile cũ.
ACCOUNT_DEFAULTS = {
    'email': '',
    'password': '',
    'tiktok_id': '',
    'proxy_string': '',
    'cookie_str': '',
    'auth2fa': '',
    'passmail': '',
    'mail_backup': '',
    'pass_mail_backup': '',
    'note': '',
    'proxy_type': DEFAULT_PROXY_TYPE,
}


def _normalize_token(token):
    token = str(token or '').strip().lower()
    token = token.replace('_', ' ')
    token = re.sub(r'[\s\-\.]+', ' ', token).strip()
    return token


_ALIAS_MAP = {}


def _register_alias(token, canonical):
    _ALIAS_MAP[_normalize_token(token)] = canonical


for _field in DEFAULT_FIELDS:
    _register_alias(_field, _field)

_register_alias('Tên', 'name')
_register_alias('Tên tài khoản', 'name')
_register_alias('Tên đăng nhập', 'name')
_register_alias('Ten tai khoan', 'name')
_register_alias('Ten dang nhap', 'name')
_register_alias('Username', 'name')
_register_alias('User', 'name')
_register_alias('ID TikTok', 'idTiktok')
_register_alias('Id TikTok', 'idTiktok')
_register_alias('TikTok ID', 'idTiktok')
_register_alias('Tiktok', 'idTiktok')
_register_alias('Tiktok id', 'idTiktok')
_register_alias('Email', 'email')
_register_alias('Password', 'password')
_register_alias('Pass', 'password')
_register_alias('Mật khẩu', 'password')
_register_alias('Mat khau', 'password')
_register_alias('Proxy', 'proxy')
_register_alias('Cookie', 'cookie')
_register_alias('2FA', 'auth2fa')
_register_alias('Auth 2fa', 'auth2fa')
_register_alias('Auth2fa', 'auth2fa')
_register_alias('Mã 2fa', 'auth2fa')
_register_alias('Ma 2fa', 'auth2fa')
_register_alias('Passmail', 'passmail')
_register_alias('Pass mail', 'passmail')
_register_alias('Mật khẩu mail', 'passmail')
_register_alias('Mat khau mail', 'passmail')
_register_alias('Mail', 'mailBackup')
_register_alias('Mailbackup', 'mailBackup')
_register_alias('Mail backup', 'mailBackup')
_register_alias('Email backup', 'mailBackup')
_register_alias('Email dự phòng', 'mailBackup')
_register_alias('Mail dự phòng', 'mailBackup')
_register_alias('Mail du phong', 'mailBackup')
_register_alias('Passmailbackup', 'passMailBackup')
_register_alias('Pass mail backup', 'passMailBackup')
_register_alias('Mật khẩu mail backup', 'passMailBackup')
_register_alias('Mat khau mail backup', 'passMailBackup')
_register_alias('Note', 'note')
_register_alias('Ghi chú', 'note')
_register_alias('Ghi chu', 'note')


def resolve_field(token):
    return _ALIAS_MAP.get(_normalize_token(token))


def parse_format(format_str):
    raw = str(format_str or '').strip()
    if not raw:
        raise ValueError('Chưa nhập format.')
    fields = []
    seen = set()
    for token in raw.split('|'):
        token = token.strip()
        canonical = resolve_field(token)
        if canonical is None:
            raise ValueError(f'Trường không hợp lệ: {token}')
        if canonical in seen:
            raise ValueError(f'Trường bị trùng: {token}')
        seen.add(canonical)
        fields.append(canonical)
    if not fields:
        raise ValueError('Format rỗng.')
    return fields


def _quote_state(text):
    in_quotes = False
    i = 0
    n = len(text)
    while i < n:
        if text[i] == '"':
            if i + 1 < n and text[i + 1] == '"':
                i += 2
                continue
            in_quotes = not in_quotes
        i += 1
    return in_quotes


def _split_line(line, delimiter='|'):
    parts = []
    current = []
    in_quotes = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == '"' and not in_quotes and not current:
            in_quotes = True
            i += 1
            continue
        if ch == '"' and in_quotes:
            if i + 1 < n and line[i + 1] == '"':
                current.append('"')
                i += 2
                continue
            in_quotes = False
            i += 1
            continue
        if ch == delimiter and not in_quotes:
            parts.append(''.join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    parts.append(''.join(current))
    return parts


def parse_rows(text, delimiter='|'):
    lines = (text or '').split('\n')
    rows = []
    buffer = None
    buffer_start = 0
    for index, line in enumerate(lines):
        if buffer is None:
            buffer = line
            buffer_start = index + 1
        else:
            buffer += '\n' + line
        if _quote_state(buffer):
            continue
        rows.append((buffer_start, _split_line(buffer, delimiter)))
        buffer = None
    if buffer is not None:
        rows.append((buffer_start, _split_line(buffer, delimiter)))
    return rows


def build_record(fields, values):
    record = {}
    for field, value in zip(fields, values):
        value = '' if value is None else str(value).strip()
        if field == 'name':
            record['name'] = value
            continue
        if not value:
            continue
        internal = INTERNAL_FIELD_MAP.get(field)
        if internal:
            record[internal] = value
    return record


def parse_data_into_records(text, fields, skip_header=False):
    rows = parse_rows(text)
    start = 1 if skip_header else 0
    records = []
    errors = []
    for line_no, row in rows[start:]:
        if not any(str(cell).strip() for cell in row):
            continue
        if len(row) > len(fields):
            errors.append((line_no, 'Thừa cột'))
            continue
        values = list(row) + [''] * (len(fields) - len(row))
        record = build_record(fields, values)
        if not record.get('name'):
            errors.append((line_no, 'Thiếu tên'))
            continue
        records.append(record)
    return records, errors


def plan_import(records, existing_names, policy='skip'):
    existing = set(existing_names or ())
    seen_in_batch = set()
    plans = []
    for record in records:
        name = record.get('name', '')
        if not name:
            plans.append({'record': record, 'action': 'skip'})
            continue
        if name in existing or name in seen_in_batch:
            if policy == 'update':
                plans.append({'record': record, 'action': 'update'})
            elif policy == 'error':
                plans.append({'record': record, 'action': 'error'})
            else:
                plans.append({'record': record, 'action': 'skip'})
        else:
            seen_in_batch.add(name)
            plans.append({'record': record, 'action': 'add'})
    return plans


def apply_update_to_config(config, record, proxy_type=DEFAULT_PROXY_TYPE):
    """Áp dụng bản ghi import lên config profile hiện có.

    - name không được ghi đè.
    - Giá trị rỗng trong bản ghi giữ dữ liệu cũ.
    - proxy_type chỉ thay đổi khi bản ghi có proxy mới.
    """
    updated = dict(config)
    for key, value in record.items():
        if key == 'name' or value == '':
            continue
        updated[key] = value
    updated['use_proxy'] = bool(updated.get('proxy_string', ''))
    if record.get('proxy_string'):
        updated['proxy_type'] = proxy_type
    return updated


def _needs_quote(value):
    return any(ch in value for ch in ('|', '"', '\n', '\r'))


def _quote(value):
    if _needs_quote(value):
        return '"' + value.replace('"', '""') + '"'
    return value


def serialize_record(fields, record):
    values = []
    for field in fields:
        if field == 'name':
            values.append(str(record.get('name', '')))
        else:
            internal = INTERNAL_FIELD_MAP.get(field)
            values.append(str(record.get(internal, '')) if internal else '')
    return '|'.join(_quote(value) for value in values)


def serialize_records(fields, records):
    return '\n'.join(serialize_record(fields, record) for record in records)


def record_from_config(name, config):
    record = {'name': name}
    for internal in INTERNAL_FIELD_MAP.values():
        if internal:
            record[internal] = config.get(internal, '')
    return record


def masked_record(record, max_len=12):
    masked = dict(record)
    for key in ('password', 'cookie_str', 'auth2fa', 'passmail', 'pass_mail_backup', 'proxy_string'):
        value = str(masked.get(key, ''))
        if not value:
            continue
        if len(value) <= max_len:
            masked[key] = '***'
        else:
            masked[key] = value[:3] + '***'
    return masked


def preview_columns():
    return (
        ('name', 'Name'),
        ('email', 'Email'),
        ('tiktok_id', 'TikTok ID'),
        ('proxy_string', 'Proxy'),
    )