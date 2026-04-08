import json
import random
import re
import string
import time
import urllib.parse
from datetime import datetime
from typing import Any, Optional

from curl_cffi import requests

from . import context as ctx
from . import mail, oauth
from .ui import rich_print as print


_FIRST_NAMES = [
    "James", "John", "Robert", "Michael", "David", "William", "Richard",
    "Joseph", "Thomas", "Christopher", "Daniel", "Matthew", "Anthony",
    "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara",
    "Sarah", "Jessica", "Karen", "Emily", "Olivia", "Emma", "Sophia",
]

_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Wilson", "Anderson", "Taylor",
    "Thomas", "Moore", "Jackson", "Martin", "Lee", "Harris", "Clark",
]


def _is_phone_challenge_response(payload: dict) -> bool:
    continue_url = str(payload.get("continue_url") or "").lower()
    page_type = str((payload.get("page") or {}).get("type") or "").lower()
    return "add-phone" in continue_url or page_type == "add_phone"

def _random_user_info() -> dict:
    name = f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"
    year = random.randint(datetime.now().year - 45, datetime.now().year - 18)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return {"name": name, "birthdate": f"{year}-{month:02d}-{day:02d}"}

def _generate_password(length: int = 16) -> str:
    """生成符合 OpenAI 要求的随机强密码（大小写+数字+特殊字符）"""
    upper = random.choices(string.ascii_uppercase, k=2)
    lower = random.choices(string.ascii_lowercase, k=2)
    digits = random.choices(string.digits, k=2)
    specials = random.choices("!@#$%&*", k=2)
    rest_len = length - 8
    pool = string.ascii_letters + string.digits + "!@#$%&*"
    rest = random.choices(pool, k=rest_len)
    chars = upper + lower + digits + specials + rest
    random.shuffle(chars)
    return "".join(chars)

def run(proxy: Optional[str]) -> tuple:
    """运行注册流程，返回 (token_json, password, email, fail_reason)
    失败时返回 (None/特殊标记, None, email, fail_reason)
    fail_reason: 403_forbidden, signup_form_error, password_error, otp_timeout,
                 account_create_error, callback_error, network_error, other_error
    """
    proxies: Any = ctx.build_proxies(proxy)

    s = requests.Session(proxies=proxies, impersonate="safari")

    if not ctx._skip_net_check():
        try:
            trace = s.get(
                "https://cloudflare.com/cdn-cgi/trace",
                proxies=proxies,
                verify=ctx._ssl_verify(),
                timeout=10,
            )
            trace = trace.text
            loc_re = re.search(r"^loc=(.+)$", trace, re.MULTILINE)
            loc = loc_re.group(1) if loc_re else None
            print(f"[*] 当前 IP 所在地: {loc}")
            if loc == "CN" or loc == "HK":
                raise RuntimeError("检查代理哦w - 所在地不支持")
        except Exception as e:
            print(f"[Error] 网络连接检查失败: {e}")
            return None, None, None, "network_error"

    email, dev_token = mail.get_email_and_token(proxies)
    if not email or not dev_token:
        return None, None, email, "other_error"
    print(f"[*] 成功获取临时邮箱与授权: {email}")
    masked = dev_token[:8] + "..." if dev_token else ""
    print(f"[*] 临时邮箱 JWT: {masked}")

    oauth_start = oauth.generate_oauth_url()
    url = oauth_start.auth_url

    try:
        resp = s.get(url, proxies=proxies, verify=True, timeout=15)
        did = s.cookies.get("oai-did")
        print(f"[*] Device ID: {did}")

        signup_body = f'{{"username":{{"value":"{email}","kind":"email"}},"screen_hint":"signup"}}'
        sen_req_body = f'{{"p":"","id":"{did}","flow":"authorize_continue"}}'

        sen_resp = requests.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=sen_req_body,
            proxies=proxies,
            impersonate="safari",
            verify=ctx._ssl_verify(),
            timeout=15,
        )

        if sen_resp.status_code != 200:
            print(f"[Error] Sentinel 异常拦截，状态码: {sen_resp.status_code}")
            return None, None

        sen_token = sen_resp.json()["token"]
        sentinel = f'{{"p": "", "t": "", "c": "{sen_token}", "id": "{did}", "flow": "authorize_continue"}}'

        signup_resp = s.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers={
                "referer": "https://auth.openai.com/create-account",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": sentinel,
            },
            data=signup_body,
            proxies=proxies,
            verify=ctx._ssl_verify(),
        )
        signup_status = signup_resp.status_code
        print(f"[*] 提交注册表单状态: {signup_status}")

        if signup_status == 403:
            print("[Error] 提交注册表单返回 403，中断本次运行，将在10秒后重试...")
            return "retry_403", None, email, "403_forbidden"
        if signup_status != 200:
            print("[Error] 提交注册表单失败，跳过本次流程")
            print(signup_resp.text)
            return None, None, email, "signup_form_error"

        password = _generate_password()
        register_body = json.dumps({"password": password, "username": email})
        print(f"[*] 生成随机密码: {password[:4]}****")

        pwd_resp = s.post(
            "https://auth.openai.com/api/accounts/user/register",
            headers={
                "referer": "https://auth.openai.com/create-account/password",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": sentinel,
            },
            data=register_body,
            proxies=proxies,
            verify=ctx._ssl_verify(),
        )
        print(f"[*] 提交注册(密码)状态: {pwd_resp.status_code}")
        if pwd_resp.status_code != 200:
            print(pwd_resp.text)
            return None, None, email, "password_error"

        try:
            register_json = pwd_resp.json()
            register_continue = register_json.get("continue_url", "")
            register_page = (register_json.get("page") or {}).get("type", "")
            print(f"[*] 注册响应 continue_url: {register_continue}")
            print(f"[*] 注册响应 page.type: {register_page}")
        except Exception:
            register_continue = ""
            register_page = ""
            print(f"[*] 注册响应(raw): {pwd_resp.text[:300]}")

        need_otp = "email-verification" in register_continue or "verify" in register_continue
        if not need_otp and register_page:
            need_otp = "verification" in register_page or "otp" in register_page

        if need_otp:
            print("[*] 需要邮箱验证，开始等待验证码...")

            if register_continue:
                otp_send_url = register_continue
                if not otp_send_url.startswith("http"):
                    otp_send_url = f"https://auth.openai.com{otp_send_url}"
                print(f"[*] 触发发送 OTP: {otp_send_url}")
                otp_send_resp = oauth._post_with_retry(
                    s,
                    otp_send_url,
                    headers={
                        "referer": "https://auth.openai.com/create-account/password",
                        "accept": "application/json",
                        "content-type": "application/json",
                        "openai-sentinel-token": sentinel,
                    },
                    json_body={},
                    proxies=proxies,
                    timeout=30,
                    retries=2,
                )
                print(f"[*] OTP 发送状态: {otp_send_resp.status_code}")
                if otp_send_resp.status_code != 200:
                    print(otp_send_resp.text)

            processed_mails = set()
            code = ""
            for otp_attempt in range(5):
                if otp_attempt > 0:
                    print(f"\n[*] OTP 重试 {otp_attempt}/5，重新发送验证码...")
                    try:
                        oauth._post_with_retry(
                            s,
                            "https://auth.openai.com/api/accounts/email-otp/resend",
                            headers={
                                "openai-sentinel-token": sentinel,
                                "content-type": "application/json",
                            },
                            json_body={},
                            proxies=proxies,
                            timeout=15,
                            retries=1,
                        )
                        time.sleep(2)
                    except Exception as e:
                        print(f"[*] 重发 OTP 异常: {e}")
                code = mail.get_oai_code(token=dev_token, email=email, proxies=proxies, seen_ids=processed_mails)
                if code:
                    break
            if not code:
                print("[Error] 多次重试后仍未收到验证码，跳过")
                return None, None, email

            print("[*] 开始校验验证码...")
            code_resp = oauth._post_with_retry(
                s,
                "https://auth.openai.com/api/accounts/email-otp/validate",
                headers={
                    "referer": "https://auth.openai.com/email-verification",
                    "accept": "application/json",
                    "content-type": "application/json",
                    "openai-sentinel-token": sentinel,
                },
                json_body={"code": code},
                proxies=proxies,
                timeout=30,
                retries=2,
            )
            print(f"[*] 验证码校验状态: {code_resp.status_code}")
            if code_resp.status_code != 200:
                print(code_resp.text)
        else:
            print("[*] 密码注册无需邮箱验证，跳过 OTP 步骤")

        user_info = _random_user_info()
        print(f"[*] 开始创建账户 (昵称: {user_info['name']})...")
        create_account_resp = oauth._post_with_retry(
            s,
            "https://auth.openai.com/api/accounts/create_account",
            headers={
                "referer": "https://auth.openai.com/about-you",
                "accept": "application/json",
                "content-type": "application/json",
            },
            json_body=user_info,
            proxies=proxies,
            timeout=30,
            retries=2,
        )
        create_account_status = create_account_resp.status_code
        print(f"[*] 账户创建状态: {create_account_status}")

        if create_account_status != 200:
            print(create_account_resp.text)
            return None, None, email
        try:
            create_account_json = create_account_resp.json()
        except Exception:
            create_account_json = {}

        if _is_phone_challenge_response(create_account_json):
            print("[*] 账户创建后进入手机号验证步骤，尝试跳过...")
            print(create_account_resp.text)
            # 尝试跳过手机号验证，继续后续流程
            # 有时服务端虽然返回 add_phone，但静默重登录后仍能获取 token
            print("[*] 尝试继续静默重登录流程...")
        else:
            print("[*] 账户创建完毕，执行静默重登录...")
        s.cookies.clear()

        oauth_start = oauth.generate_oauth_url()
        s.get(oauth_start.auth_url, proxies=proxies, verify=True, timeout=15)
        new_did = s.cookies.get("oai-did") or did

        sen_req_body2 = f'{{"p":"","id":"{new_did}","flow":"authorize_continue"}}'
        sen_resp2 = requests.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=sen_req_body2,
            proxies=proxies,
            impersonate="safari",
            verify=ctx._ssl_verify(),
            timeout=15,
        )
        sen_token2 = sen_resp2.json().get("token", "") if sen_resp2.status_code == 200 else ""
        sentinel2 = f'{{"p": "", "t": "", "c": "{sen_token2}", "id": "{new_did}", "flow": "authorize_continue"}}'

        oauth._post_with_retry(
            s,
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers={
                "openai-sentinel-token": sentinel2,
                "content-type": "application/json",
            },
            json_body={"username": {"value": email, "kind": "email"}, "screen_hint": "login"},
            proxies=proxies,
        )

        pwd_login_resp = oauth._post_with_retry(
            s,
            "https://auth.openai.com/api/accounts/password/verify",
            headers={
                "openai-sentinel-token": sentinel2,
                "content-type": "application/json",
            },
            json_body={"password": password},
            proxies=proxies,
        )
        print(f"[*] 密码登录状态: {pwd_login_resp.status_code}")

        if pwd_login_resp.status_code == 200:
            try:
                pwd_json = pwd_login_resp.json()
                pwd_page = (pwd_json.get("page") or {}).get("type", "")
                if "otp" in pwd_page or "verify" in str(pwd_json.get("continue_url", "")):
                    print("[*] 登录触发二次邮箱验证，尝试使用第一次的验证码...")
                    # 二次验证码通常和第一次相同，直接复用
                    code2 = code
                    if not code2:
                        print("[Error] 第一次验证码为空，无法复用")
                        return None, None, email
                    print(f"[*] 使用第一次的验证码: {code2}")
                    code2_resp = oauth._post_with_retry(
                        s,
                        "https://auth.openai.com/api/accounts/email-otp/validate",
                        headers={
                            "openai-sentinel-token": sentinel2,
                            "content-type": "application/json",
                        },
                        json_body={"code": code2},
                        proxies=proxies,
                    )
                    print(f"[*] 二次验证码校验状态: {code2_resp.status_code}")
                    if code2_resp.status_code != 200:
                        print(code2_resp.text)
                        return None, None, email
            except Exception:
                pass

        auth_cookie = s.cookies.get("oai-client-auth-session")
        if not auth_cookie:
            print("[Error] 重登录后未能获取授权 Cookie")
            return None, None, email

        auth_json = {}
        raw_val = auth_cookie.strip()
        try:
            decoded_val = urllib.parse.unquote(raw_val)
            if decoded_val != raw_val:
                raw_val = decoded_val
        except Exception:
            pass
        for part in raw_val.split("."):
            decoded = oauth._decode_jwt_segment(part)
            if isinstance(decoded, dict) and "workspaces" in decoded:
                auth_json = decoded
                break

        workspaces = auth_json.get("workspaces") or []
        if not workspaces:
            print("[Error] 重登录后 Cookie 里仍没有 workspace 信息")
            return None, None, email
        workspace_id = str((workspaces[0] or {}).get("id") or "").strip()
        if not workspace_id:
            print("[Error] 无法解析 workspace_id")
            return None, None, email

        select_body = f'{{"workspace_id":"{workspace_id}"}}'
        print("[*] 开始选择 workspace...")
        select_resp = oauth._post_with_retry(
            s,
            "https://auth.openai.com/api/accounts/workspace/select",
            headers={
                "referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "content-type": "application/json",
            },
            data=select_body,
            proxies=proxies,
            timeout=30,
            retries=2,
        )

        if select_resp.status_code != 200:
            print(f"[Error] 选择 workspace 失败，状态码: {select_resp.status_code}")
            print(select_resp.text)
            return None, None, email

        continue_url = str((select_resp.json() or {}).get("continue_url") or "").strip()
        if not continue_url:
            print("[Error] workspace/select 响应里缺少 continue_url")
            return None, None, email

        try:
            select_data = select_resp.json()
            orgs = (select_data.get("data") or {}).get("orgs") or []
            if orgs:
                org_id = str((orgs[0] or {}).get("id") or "").strip()
                if org_id:
                    org_body = {"org_id": org_id}
                    projects = (orgs[0] or {}).get("projects") or []
                    if projects:
                        org_body["project_id"] = str((projects[0] or {}).get("id") or "").strip()
                    print(f"[*] 选择组织: {org_id}")
                    org_resp = oauth._post_with_retry(
                        s,
                        "https://auth.openai.com/api/accounts/organization/select",
                        headers={
                            "content-type": "application/json",
                            "openai-sentinel-token": sentinel2,
                        },
                        json_body=org_body,
                        proxies=proxies,
                    )
                    if org_resp.status_code in [301, 302, 303, 307, 308]:
                        continue_url = org_resp.headers.get("Location", continue_url)
                    elif org_resp.status_code == 200:
                        try:
                            continue_url = org_resp.json().get("continue_url", continue_url)
                        except Exception:
                            pass
        except Exception as e:
            print(f"[*] 组织选择异常(非致命): {e}")

        current_url = continue_url
        for _ in range(15):
            final_resp = s.get(
                current_url,
                allow_redirects=False,
                proxies=proxies,
                verify=ctx._ssl_verify(),
                timeout=15,
            )

            if final_resp.status_code in [301, 302, 303, 307, 308]:
                next_url = urllib.parse.urljoin(
                    current_url, final_resp.headers.get("Location") or ""
                )
            elif final_resp.status_code == 200:
                if "consent_challenge=" in current_url:
                    c_resp = s.post(
                        current_url,
                        data={"action": "accept"},
                        allow_redirects=False,
                        proxies=proxies,
                        verify=ctx._ssl_verify(),
                        timeout=15,
                    )
                    next_url = (
                        urllib.parse.urljoin(
                            current_url, c_resp.headers.get("Location") or ""
                        )
                        if c_resp.status_code in [301, 302, 303, 307, 308]
                        else ""
                    )
                else:
                    meta_match = re.search(
                        r'content=["\']?\d+;\s*url=([^"\'>\s]+)',
                        final_resp.text,
                        re.IGNORECASE,
                    )
                    next_url = (
                        urllib.parse.urljoin(current_url, meta_match.group(1))
                        if meta_match
                        else ""
                    )
                if not next_url:
                    break
            else:
                break

            if "code=" in next_url and "state=" in next_url:
                token_json = oauth.submit_callback_url(
                    callback_url=next_url,
                    code_verifier=oauth_start.code_verifier,
                    redirect_uri=oauth_start.redirect_uri,
                    expected_state=oauth_start.state,
                )
                return token_json, password, email
            current_url = next_url

        print("[Error] 未能在重定向链中捕获到最终 Callback URL")
        return None, None, email

    except Exception as e:
        print(f"[Error] 运行时发生错误: {e}")
        return None, None, email
