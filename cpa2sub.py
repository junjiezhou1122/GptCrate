"""将当前 token JSON / sub 账号 JSON 统一整理为 sub2api 导出文件。"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

AUTH_CLAIM = "https://api.openai.com/auth"
PROFILE_CLAIM = "https://api.openai.com/profile"
DEFAULT_EXPIRES_IN = 864000


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """解码 JWT payload；失败时返回空字典。"""
    if not token:
        return {}
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return {}
        payload = parts[1] + ('=' * (-len(parts[1]) % 4))
        decoded = base64.urlsafe_b64decode(payload)
        data = json.loads(decoded)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def parse_expired_time(value: Any) -> int:
    """解析 RFC3339/ISO8601 时间为 Unix 时间戳。"""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip()
    if not text:
        return 0

    try:
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        return int(datetime.fromisoformat(text).timestamp())
    except Exception:
        return 0


def _auth_claims(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get(AUTH_CLAIM, {})
    return data if isinstance(data, dict) else {}


def _profile_claims(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get(PROFILE_CLAIM, {})
    return data if isinstance(data, dict) else {}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}, ()):  # noqa: PLC1901
            return value
    return ""


def _default_name(index: int, token_type: str) -> str:
    return f"{token_type or 'unknown'}-普号-{index:04d}"


def _name_from_email(email: str, fallback: str) -> str:
    email = str(email or "").strip()
    return email or fallback


def normalize_sub_account(account: dict[str, Any], index: int | None = None) -> dict[str, Any]:
    """整理单个 sub 账号，补齐常用默认字段。"""
    normalized = copy.deepcopy(account)
    normalized.setdefault('platform', 'openai')
    normalized.setdefault('type', 'oauth')
    normalized.setdefault('concurrency', 10)
    normalized.setdefault('priority', 1)
    normalized.setdefault('rate_multiplier', 1)
    normalized.setdefault('auto_pause_on_expired', True)

    credentials = normalized.setdefault('credentials', {})
    extra = normalized.setdefault('extra', {})
    if not isinstance(credentials, dict):
        raise ValueError('sub 账号的 credentials 必须是对象')
    if not isinstance(extra, dict):
        raise ValueError('sub 账号的 extra 必须是对象')

    access_payload = decode_jwt_payload(str(credentials.get('access_token') or ''))
    auth_info = _auth_claims(access_payload)
    profile_info = _profile_claims(access_payload)

    expires_at = parse_expired_time(credentials.get('expires_at')) or int(access_payload.get('exp') or 0)

    credentials.setdefault('access_token', '')
    credentials.setdefault('refresh_token', '')
    credentials.setdefault('chatgpt_account_id', _first_non_empty(auth_info.get('chatgpt_account_id')))
    credentials.setdefault(
        'chatgpt_user_id',
        _first_non_empty(
            auth_info.get('chatgpt_user_id'),
            auth_info.get('user_id'),
            access_payload.get('sub'),
        ),
    )
    credentials.setdefault('organization_id', '')
    credentials.setdefault('expires_at', expires_at)
    credentials.setdefault('expires_in', DEFAULT_EXPIRES_IN if expires_at else 0)

    extra.setdefault('email', _first_non_empty(profile_info.get('email'), access_payload.get('email')))
    extra.setdefault('sub', _first_non_empty(access_payload.get('sub')))
    normalized.setdefault(
        'name',
        _name_from_email(str(extra.get('email') or ''), _default_name(index or 1, 'openai')),
    )

    return normalized


def convert_current_token_to_sub(source_data: dict[str, Any], index: int = 1) -> dict[str, Any]:
    """将当前项目的 token JSON 转为 sub 账号格式。"""
    access_payload = decode_jwt_payload(str(source_data.get('access_token') or ''))
    access_auth = _auth_claims(access_payload)
    access_profile = _profile_claims(access_payload)

    id_payload = decode_jwt_payload(str(source_data.get('id_token') or ''))
    id_auth = _auth_claims(id_payload)

    organizations = _first_non_empty(id_auth.get('organizations'), access_auth.get('organizations'), [])
    organization_id = ''
    if isinstance(organizations, list) and organizations:
        first_org = organizations[0]
        if isinstance(first_org, dict):
            organization_id = str(first_org.get('id') or '')

    expires_at = parse_expired_time(source_data.get('expired')) or int(access_payload.get('exp') or 0)
    issued_at = int(access_payload.get('iat') or 0)
    expires_in = max(expires_at - issued_at, 0) if expires_at and issued_at else DEFAULT_EXPIRES_IN

    token_type = str(
        _first_non_empty(
            source_data.get('type'),
            access_auth.get('chatgpt_plan_type'),
            'unknown',
        )
    )

    account_email = str(
        _first_non_empty(
            source_data.get('email'),
            access_profile.get('email'),
            id_payload.get('email'),
        )
    )

    account = {
        'name': _name_from_email(account_email, _default_name(index, token_type)),
        'platform': 'openai',
        'type': 'oauth',
        'credentials': {
            'access_token': str(source_data.get('access_token') or ''),
            'chatgpt_account_id': str(
                _first_non_empty(
                    source_data.get('account_id'),
                    access_auth.get('chatgpt_account_id'),
                )
            ),
            'chatgpt_user_id': str(
                _first_non_empty(
                    access_auth.get('chatgpt_user_id'),
                    access_auth.get('user_id'),
                    access_payload.get('sub'),
                    id_payload.get('sub'),
                )
            ),
            'expires_at': expires_at,
            'expires_in': expires_in,
            'organization_id': organization_id,
            'refresh_token': str(source_data.get('refresh_token') or ''),
        },
        'extra': {
            'email': account_email,
            'sub': str(_first_non_empty(access_payload.get('sub'), id_payload.get('sub'))),
        },
        'concurrency': 10,
        'priority': 1,
        'rate_multiplier': 1,
        'auto_pause_on_expired': True,
    }
    return account


def is_current_token_format(data: dict[str, Any]) -> bool:
    return bool(data.get('access_token')) and (
        'account_id' in data or 'refresh_token' in data or 'expired' in data or 'id_token' in data
    )


def is_sub_account_format(data: dict[str, Any]) -> bool:
    return isinstance(data.get('credentials'), dict) and data.get('platform') == 'openai'


def is_sub_export_format(data: dict[str, Any]) -> bool:
    return isinstance(data.get('accounts'), list)


def load_accounts_from_file(path: str | Path, start_index: int = 1, type_filter: str | None = None) -> list[dict[str, Any]]:
    """从文件中读取并归一化为 sub 账号列表。"""
    file_path = Path(path)
    with file_path.open('r', encoding='utf-8') as handle:
        data = json.load(handle)

    if not isinstance(data, dict):
        raise ValueError(f'{file_path} 顶层不是 JSON 对象')

    if is_sub_export_format(data):
        accounts: list[dict[str, Any]] = []
        for offset, account in enumerate(data.get('accounts', []), start=0):
            if not isinstance(account, dict):
                raise ValueError(f'{file_path} 的 accounts[{offset}] 不是对象')
            accounts.append(normalize_sub_account(account, start_index + offset))
        return accounts

    if is_sub_account_format(data):
        return [normalize_sub_account(data, start_index)]

    if is_current_token_format(data):
        if type_filter and str(data.get('type') or '') != type_filter:
            return []
        return [convert_current_token_to_sub(data, start_index)]

    raise ValueError(f'{file_path} 不是支持的 token/sub JSON 格式')


def build_export(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        'exported_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'proxies': [],
        'accounts': accounts,
    }


def write_export(accounts: list[dict[str, Any]], output_file: str | Path | None = None) -> str:
    if not accounts:
        raise ValueError('未找到可导出的账号')

    now = datetime.now(timezone.utc)
    if output_file is None:
        output_file = f"sub2api-account-{now.strftime('%Y%m%d%H%M%S')}.json"

    export = build_export(accounts)
    output_path = Path(output_file)
    with output_path.open('w', encoding='utf-8') as handle:
        json.dump(export, handle, ensure_ascii=False, indent=2)

    return str(output_path)


def collect_input_files(targets: Iterable[str | Path], output_file: str | Path | None = None) -> list[Path]:
    """收集输入文件；目录默认只扫描当前层级的 JSON。"""
    output_path = Path(output_file).resolve() if output_file else None
    files: list[Path] = []
    seen: set[Path] = set()

    for target in targets:
        path = Path(target)
        if not path.exists():
            raise FileNotFoundError(f'{path} 不存在')

        candidates = sorted(path.glob('*.json')) if path.is_dir() else [path]
        for candidate in candidates:
            if candidate.suffix.lower() != '.json':
                continue
            resolved = candidate.resolve()
            if output_path and resolved == output_path:
                continue
            if candidate.is_dir():
                continue
            if candidate.name.startswith('sub2api-account-'):
                continue
            if resolved not in seen:
                files.append(candidate)
                seen.add(resolved)

    return files


def convert_files(
    input_files: list[str | Path],
    output_file: str | Path | None = None,
    type_filter: str | None = None,
) -> str:
    """转换多个文件并写出单个 sub 导出文件。"""
    accounts: list[dict[str, Any]] = []
    next_index = 1

    for input_file in input_files:
        loaded = load_accounts_from_file(input_file, start_index=next_index, type_filter=type_filter)
        if not loaded:
            continue
        accounts.extend(loaded)
        next_index += len(loaded)

    return write_export(accounts, output_file)


def convert_targets(
    targets: list[str | Path],
    output_file: str | Path | None = None,
    type_filter: str | None = None,
) -> str:
    input_files = collect_input_files(targets, output_file=output_file)
    if not input_files:
        raise ValueError('未找到可用的 JSON 输入文件')
    return convert_files(input_files, output_file=output_file, type_filter=type_filter)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='把当前 token JSON 或 sub 账号 JSON 整理成 sub2api 导出文件',
    )
    parser.add_argument(
        'inputs',
        nargs='*',
        help='输入文件或目录；留空时默认读取 ./tokens',
    )
    parser.add_argument(
        '-d',
        '--directory',
        action='append',
        default=[],
        help='额外追加目录输入，可重复传入',
    )
    parser.add_argument('-t', '--type', dest='type_filter', help='只筛选当前 token 格式里的指定 type')
    parser.add_argument('-o', '--output', help='输出文件路径')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    targets = list(args.inputs) + list(args.directory)
    if not targets:
        targets = ['tokens']

    try:
        output_path = convert_targets(targets, output_file=args.output, type_filter=args.type_filter)
    except Exception as exc:
        print(f'错误: {exc}', file=sys.stderr)
        return 1

    print(f'转换完成! 输出文件: {output_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
