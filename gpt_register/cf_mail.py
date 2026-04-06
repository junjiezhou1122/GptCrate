import random
import re
import string
import time
from typing import Any, Optional, Set

from curl_cffi import requests

from . import context as ctx


def generate_email() -> tuple[str, str]:
    prefix = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    email = f"{prefix}@{ctx.MAIL_DOMAIN}"
    return email, email


def extract_otp_code(content: str) -> str:
    if not content:
        return ""

    patterns = [
        r"Your ChatGPT code is\s*(\d{6})",
        r"ChatGPT code is\s*(\d{6})",
        r"verification code to continue:\s*(\d{6})",
        r"Subject:.*?(\d{6})",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)

    fallback = re.search(r"(?<!\d)(\d{6})(?!\d)", content)
    return fallback.group(1) if fallback else ""


def get_oai_code(email: str, proxies: Any = None, seen_ids: Optional[Set[str]] = None) -> str:
    headers = {
        "x-admin-auth": ctx.MAIL_ADMIN_PASSWORD,
        "Content-Type": "application/json",
    }
    seen_ids = seen_ids or set()
    print(f"[*] 正在等待邮箱 {email} 的验证码...", end="", flush=True)

    for _ in range(40):
        print(".", end="", flush=True)
        try:
            response = requests.get(
                f"{ctx.MAIL_WORKER_BASE}/admin/mails",
                params={"limit": 5, "offset": 0, "address": email},
                headers=headers,
                proxies=proxies,
                impersonate="safari",
                verify=ctx._ssl_verify(),
                timeout=15,
            )
            if response.status_code == 200:
                results = response.json().get("results") or []
                for mail in results:
                    mail_id = mail.get("id")
                    if mail_id in seen_ids:
                        continue
                    seen_ids.add(mail_id)
                    raw = mail.get("raw") or ""
                    content = raw
                    subject_match = re.search(r"^Subject:\s*(.+)$", raw, re.MULTILINE)
                    if subject_match:
                        content = subject_match.group(1) + "\n" + raw
                    code = extract_otp_code(content)
                    if code:
                        print(" 抓到啦! 验证码:", code)
                        return code
        except Exception:
            pass

        time.sleep(3)

    print(" 超时，未收到验证码")
    return ""


def delete_temp_email(email: str, proxies: Any = None) -> None:
    headers = {
        "x-admin-auth": ctx.MAIL_ADMIN_PASSWORD,
        "Content-Type": "application/json",
    }
    try:
        response = requests.get(
            f"{ctx.MAIL_WORKER_BASE}/admin/mails",
            params={"limit": 50, "offset": 0, "address": email},
            headers=headers,
            proxies=proxies,
            impersonate="safari",
            verify=ctx._ssl_verify(),
            timeout=15,
        )
        if response.status_code == 200:
            for mail in response.json().get("results") or []:
                mail_id = mail.get("id")
                if mail_id:
                    requests.delete(
                        f"{ctx.MAIL_WORKER_BASE}/admin/mails/{mail_id}",
                        headers=headers,
                        proxies=proxies,
                        impersonate="safari",
                        verify=ctx._ssl_verify(),
                        timeout=10,
                    )
        print(f"[*] 临时邮箱 {email} 的邮件已清理")
    except Exception as exc:
        print(f"[*] 清理临时邮箱时出错: {exc}")
