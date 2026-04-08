import base64
import json
import tempfile
import unittest
from pathlib import Path

import cpa2sub


def make_jwt(payload: dict) -> str:
    def encode(data: dict) -> str:
        raw = json.dumps(data, separators=(',', ':')).encode('utf-8')
        return base64.urlsafe_b64encode(raw).decode('utf-8').rstrip('=')

    header = encode({'alg': 'none', 'typ': 'JWT'})
    body = encode(payload)
    return f'{header}.{body}.sig'


class Cpa2SubTests(unittest.TestCase):
    def test_convert_current_token_to_sub_uses_current_token_fields(self):
        access_token = make_jwt(
            {
                'iat': 1700000000,
                'exp': 1700003600,
                'sub': 'auth0|sub-user',
                cpa2sub.AUTH_CLAIM: {
                    'chatgpt_account_id': 'acc-from-claim',
                    'chatgpt_user_id': 'user-123',
                    'chatgpt_plan_type': 'codex',
                },
                cpa2sub.PROFILE_CLAIM: {'email': 'claim@example.com'},
            }
        )
        id_token = make_jwt(
            {
                'email': 'id@example.com',
                'sub': 'auth0|id-sub',
                cpa2sub.AUTH_CLAIM: {
                    'organizations': [
                        {'id': 'org-001', 'is_default': True},
                    ]
                },
            }
        )

        account = cpa2sub.convert_current_token_to_sub(
            {
                'access_token': access_token,
                'refresh_token': 'refresh-123',
                'id_token': id_token,
                'account_id': 'acc-top-level',
                'email': 'top@example.com',
                'type': 'codex',
                'expired': '2024-01-01T00:00:00Z',
            },
            index=3,
        )

        self.assertEqual(account['name'], 'top@example.com')
        self.assertEqual(account['credentials']['chatgpt_account_id'], 'acc-top-level')
        self.assertEqual(account['credentials']['chatgpt_user_id'], 'user-123')
        self.assertEqual(account['credentials']['organization_id'], 'org-001')
        self.assertEqual(account['credentials']['refresh_token'], 'refresh-123')
        self.assertEqual(account['extra']['email'], 'top@example.com')
        self.assertEqual(account['extra']['sub'], 'auth0|sub-user')
        self.assertEqual(account['credentials']['expires_at'], 1704067200)

    def test_convert_files_supports_mixed_current_token_and_sub_export(self):
        access_token = make_jwt(
            {
                'iat': 1700000000,
                'exp': 1700003600,
                'sub': 'auth0|mixed-sub',
                cpa2sub.AUTH_CLAIM: {
                    'chatgpt_account_id': 'acc-mixed',
                    'chatgpt_user_id': 'user-mixed',
                    'chatgpt_plan_type': 'codex',
                },
                cpa2sub.PROFILE_CLAIM: {'email': 'mixed@example.com'},
            }
        )

        sub_export = {
            'exported_at': '2026-01-01T00:00:00Z',
            'proxies': [],
            'accounts': [
                {
                    'name': '已有-sub',
                    'platform': 'openai',
                    'type': 'oauth',
                    'credentials': {
                        'access_token': access_token,
                        'chatgpt_account_id': 'acc-existing',
                        'chatgpt_user_id': 'user-existing',
                        'expires_at': 1700003600,
                        'expires_in': 3600,
                        'organization_id': 'org-existing',
                        'refresh_token': 'refresh-existing',
                    },
                    'extra': {'email': 'existing@example.com'},
                }
            ],
        }

        current_token = {
            'access_token': access_token,
            'refresh_token': 'refresh-current',
            'account_id': 'acc-current',
            'email': 'current@example.com',
            'type': 'codex',
            'expired': '2024-01-01T00:00:00Z',
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            current_path = tmp / 'token_current.json'
            export_path = tmp / 'already-sub.json'
            output_path = tmp / 'merged.json'
            current_path.write_text(json.dumps(current_token), encoding='utf-8')
            export_path.write_text(json.dumps(sub_export), encoding='utf-8')

            written = cpa2sub.convert_files([current_path, export_path], output_file=output_path)

            with Path(written).open('r', encoding='utf-8') as handle:
                data = json.load(handle)

        self.assertEqual(len(data['accounts']), 2)
        self.assertEqual(data['accounts'][0]['credentials']['chatgpt_account_id'], 'acc-current')
        self.assertEqual(data['accounts'][1]['name'], '已有-sub')
        self.assertEqual(data['accounts'][1]['credentials']['chatgpt_account_id'], 'acc-existing')


if __name__ == '__main__':
    unittest.main()
