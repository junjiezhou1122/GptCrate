import json
import os
import re
import sys
import time
import uuid
import math
import random
import string
import secrets
import hashlib
import base64
import threading
import argparse
import concurrent.futures
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, quote
from dataclasses import dataclass
from typing import Any, Dict, Optional, List
import urllib.parse
import ssl
import urllib.request
import urllib.error

from curl_cffi import requests

# ==========================================
# Cloudflare Temp Email API
# ==========================================


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key or key in os.environ:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'\"', "'"}:
                    value = value[1:-1]
                os.environ[key] = value
    except Exception:
        pass


_load_dotenv()

MAIL_DOMAIN =os.getenv("MAIL_DOMAIN", "")
MAIL_WORKER_BASE = os.getenv("MAIL_WORKER_BASE", "").rstrip("/")
MAIL_ADMIN_PASSWORD = os.getenv("MAIL_ADMIN_PASSWORD", "")
TOKEN_OUTPUT_DIR = (os.getenv("TOKEN_OUTPUT_DIR") or "./tokens").strip()
CLI_PROXY_AUTHS_DIR = os.getenv("CLI_PROXY_AUTHS_DIR", "").strip()

PROXY_FILE = os.getenv("PROXY_FILE", "").strip()
SINGLE_PROXY = os.getenv("PROXY", "").strip()
BATCH_COUNT = os.getenv("BATCH_COUNT", "").strip()
BATCH_THREADS = os.getenv("BATCH_THREADS", "").strip()

EMAIL_MODE = os.getenv("EMAIL_MODE", "cf").strip().lower()
HOTMAIL007_API_URL = os.getenv("HOTMAIL007_API_URL", "https://gapi.hotmail007.com").rstrip("/")
HOTMAIL007_API_KEY = os.getenv("HOTMAIL007_API_KEY", "").strip()
HOTMAIL007_MAIL_TYPE = os.getenv("HOTMAIL007_MAIL_TYPE", "outlook Trusted Graph").strip()
HOTMAIL007_MAIL_MODE = os.getenv("HOTMAIL007_MAIL_MODE", "graph").strip().lower()

LUCKMAIL_API_KEY = os.getenv("LUCKMAIL_API_KEY", "").strip()
LUCKMAIL_API_URL = os.getenv("LUCKMAIL_API_URL", "https://mails.luckyous.com/api/v1/openapi").rstrip("/")
LUCKMAIL_AUTO_BUY = os.getenv("LUCKMAIL_AUTO_BUY", "true").strip().lower() == "true"
LUCKMAIL_EMAIL_TYPE = os.getenv("LUCKMAIL_EMAIL_TYPE", "ms_imap").strip().lower()
try:
    LUCKMAIL_MAX_RETRY = int(os.getenv("LUCKMAIL_MAX_RETRY", "3").strip())
except ValueError:
    LUCKMAIL_MAX_RETRY = 3

ACCOUNTS_FILE = os.getenv("ACCOUNTS_FILE", "accounts.txt").strip()


def _load_proxies(filepath: str) -> List[str]:
    proxies_list = []
    if not filepath or not os.path.exists(filepath):
        return proxies_list
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                proxies_list.append(line)
    except Exception as e:
        print(f"[Error] 加载代理文件失败 ({filepath}): {e}")
    return proxies_list


class ProxyRotator:
    """线程安全的代理轮换器 (round-robin)"""

    def __init__(self, proxy_list: List[str]):
        self._proxies = list(proxy_list) if proxy_list else []
        self._index = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._proxies)

    def next(self) -> Optional[str]:
        if not self._proxies:
            return None
        with self._lock:
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1
            return proxy


class EmailQueue:
    """线程安全的邮箱队列，从文件逐行读取并消费"""

    def __init__(self, filepath: str):
        self._filepath = filepath
        self._emails: List[str] = []
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if not os.path.exists(self._filepath):
            return
        with open(self._filepath, "r", encoding="utf-8") as f:
            for line in f:
                addr = line.strip()
                if not addr or addr.startswith("#"):
                    continue
                if "----" in addr:
                    addr = addr.split("----")[0].strip()
                if addr and "@" in addr:
                    self._emails.append(addr)

    def pop(self) -> Optional[str]:
        with self._lock:
            if not self._emails:
                return None
            email = self._emails.pop(0)
            self._save_unlocked()
            return email

    def _save_unlocked(self):
        try:
            with open(self._filepath, "w", encoding="utf-8") as f:
                for email in self._emails:
                    f.write(email + "\n")
        except Exception:
            pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._emails)


_email_queue: Optional[EmailQueue] = None

# 预检测线程状态标志
_prefetch_no_stock = False  # 是否无库存
_prefetch_lock = threading.Lock()


class ActiveEmailQueue:
    """线程安全的活跃邮箱队列，存储预检测的活跃邮箱"""

    def __init__(self):
        self._emails: List[dict] = []  # [{email, token, id}, ...]
        self._lock = threading.Lock()

    def add_batch(self, emails: list):
        """批量添加活跃邮箱"""
        with self._lock:
            self._emails.extend(emails)

    def pop(self) -> Optional[dict]:
        """取出一个活跃邮箱"""
        with self._lock:
            if not self._emails:
                return None
            return self._emails.pop(0)

    def __len__(self) -> int:
        with self._lock:
            return len(self._emails)

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._emails) == 0


_active_email_queue: Optional[ActiveEmailQueue] = None


def _ssl_verify() -> bool:
    return True


def _skip_net_check() -> bool:
    return False


def _prefetch_active_emails(rotator: ProxyRotator, min_pool_size: int = 10, batch_size: int = 20):
    """后台线程：预检测邮箱池补充
    当活跃邮箱数量低于 min_pool_size 时，优先检查已购邮箱，不足时批量购买
    """
    global _active_email_queue, _prefetch_no_stock
    if _active_email_queue is None:
        _active_email_queue = ActiveEmailQueue()

    # 首先检查已购邮箱
    print(f"\n[*] [预检测] 首先检查已购邮箱...")
    proxy = rotator.next() if len(rotator) > 0 else None
    proxies = {"http": proxy, "https": proxy} if proxy else None
    purchased_active = luckmail_check_purchased_emails(proxies=proxies, max_workers=5)
    if purchased_active:
        _active_email_queue.add_batch(purchased_active)
        print(f"[*] [预检测] ✓ 已从已购邮箱中添加 {len(purchased_active)} 个活跃邮箱 | 队列: {len(_active_email_queue)} 个")

    while True:
        try:
            current_size = len(_active_email_queue)
            if current_size < min_pool_size:
                need_count = batch_size
                print(f"\n{'='*50}")
                print(f"[*] [预检测] 活跃邮箱池不足 ({current_size}/{min_pool_size})，批量购买 {need_count} 个...")
                print(f"{'='*50}")

                proxy = rotator.next() if len(rotator) > 0 else None
                proxies = {"http": proxy, "https": proxy} if proxy else None

                # 使用配置的邮箱类型
                active_emails, error_msg = luckmail_batch_buy_and_check(
                    quantity=need_count,
                    max_workers=5,
                    proxies=proxies,
                    email_type=LUCKMAIL_EMAIL_TYPE
                )

                if active_emails:
                    _active_email_queue.add_batch(active_emails)
                    # 如果有库存了，重置无库存标志
                    with _prefetch_lock:
                        if _prefetch_no_stock:
                            _prefetch_no_stock = False
                            print(f"[*] [预检测] 库存恢复，继续预检测模式")
                    print(f"[*] [预检测] ✓ 已补充 {len(active_emails)} 个活跃邮箱 | 队列: {len(_active_email_queue)} 个")
                else:
                    # 检查是否是无库存错误
                    if error_msg and ("库存" in error_msg or "stock" in error_msg.lower()):
                        with _prefetch_lock:
                            _prefetch_no_stock = True
                        print(f"[*] [预检测] ✗ 无库存，自动切换回接码模式")
                        print(f"[*] [预检测] 预检测线程退出")
                        return  # 退出预检测线程
                    else:
                        print(f"[*] [预检测] ✗ 未获取到活跃邮箱，5秒后重试...")

            time.sleep(2)  # 每2秒检查一次，更频繁地补充
        except Exception as e:
            print(f"\n[*] [预检测] 异常: {e}")
            time.sleep(5)


def get_email_and_token(proxies: Any = None) -> tuple:
    """根据 EMAIL_MODE 获取邮箱: file=从accounts.txt读取, cf=自有域名随机生成, hotmail007=API拉取微软邮箱, luckmail=API拉取已购邮箱"""
    if EMAIL_MODE == "file":
        if _email_queue is None:
            print("[Error] 邮箱队列未初始化")
            return "", ""
        email = _email_queue.pop()
        if not email:
            print("[Error] accounts.txt 中没有可用的邮箱了")
            return "", ""
        print(f"[*] 从文件读取邮箱: {email} (剩余: {len(_email_queue)})")
        return email, email
    if EMAIL_MODE == "hotmail007":
        if not HOTMAIL007_API_KEY:
            print("[Error] HOTMAIL007_API_KEY 未配置")
            return "", ""
        mails, err = hotmail007_get_mail(quantity=1, proxies=proxies)
        if err or not mails:
            print(f"[Error] Hotmail007 拉取邮箱失败: {err}")
            return "", ""
        mail_info = mails[0]
        email = mail_info["email"]
        _hotmail007_credentials[email] = {
            "client_id": mail_info["client_id"],
            "refresh_token": mail_info["refresh_token"],
            "ms_password": mail_info["password"],
        }
        print(f"[*] Hotmail007 预获取已有邮件ID...")
        known_ids = _outlook_get_known_ids(
            email, mail_info["client_id"], mail_info["refresh_token"], proxies
        )
        _hotmail007_credentials[email]["known_ids"] = known_ids
        return email, email
    if EMAIL_MODE == "luckmail":
        if not LUCKMAIL_API_KEY:
            print("[Error] LUCKMAIL_API_KEY 未配置")
            return "", ""

        # 如果不启用自动购买，直接使用接码模式
        if not LUCKMAIL_AUTO_BUY:
            print(f"[*] LuckMail 接码模式: 创建 openai 项目订单")
            order_no, order_data = luckmail_create_order("", proxies=proxies)
            if not order_no:
                print(f"[Error] 创建接码订单失败: {order_data}")
                return "", ""
            email = order_data.get("email_address")
            if not email:
                print(f"[Error] 未获取到邮箱地址")
                return "", ""
            print(f"[*] 接码订单创建成功: {order_no}")
            print(f"[*] 自动分配邮箱: {email}")
            _luckmail_credentials[email] = {
                "order_no": order_no,
            }
            return email, email

        # 检查是否有预检测的活跃邮箱队列
        global _active_email_queue
        if _active_email_queue is not None and not _active_email_queue.is_empty():
            email_data = _active_email_queue.pop()
            if email_data:
                email = email_data["email"]
                token = email_data["token"]
                purchase_id = email_data["id"]
                remaining = len(_active_email_queue)
                print(f"[*] ✓ 使用预检测活跃邮箱: {email}")
                print(f"[*] 活跃邮箱池: {remaining} 个待使用")
                _luckmail_credentials[email] = {
                    "token": token,
                    "purchase_id": purchase_id,
                    "email_data": email_data,
                }
                return email, email

        # 自动购买模式：购买 -> 检测活跃度 -> 使用
        max_retries = LUCKMAIL_MAX_RETRY
        for attempt in range(1, max_retries + 1):
            print(f"[*] LuckMail 自动购买模式 (尝试 {attempt}/{max_retries}): 购买 {LUCKMAIL_EMAIL_TYPE} 邮箱...")
            purchase_data, err = luckmail_buy_email(proxies=proxies, email_type=LUCKMAIL_EMAIL_TYPE)
            if err or not purchase_data:
                print(f"[Error] 购买邮箱失败: {err}")
                if attempt == max_retries:
                    # 最后一次尝试失败，回退到接码模式
                    print(f"[*] 购买多次失败，回退到接码模式")
                    order_no, order_data = luckmail_create_order("", proxies=proxies)
                    if not order_no:
                        print(f"[Error] 创建接码订单失败: {order_data}")
                        return "", ""
                    email = order_data.get("email_address")
                    if not email:
                        print(f"[Error] 未获取到邮箱地址")
                        return "", ""
                    print(f"[*] 接码订单创建成功: {order_no}")
                    print(f"[*] 自动分配邮箱: {email}")
                    _luckmail_credentials[email] = {
                        "order_no": order_no,
                    }
                    return email, email
                time.sleep(2)
                continue

            # 获取购买的邮箱信息
            # purchase_data 现在是 purchases[0] 的单条记录
            email = purchase_data.get("email_address")
            token = purchase_data.get("token")
            purchase_id = purchase_data.get("id")

            if not email or not token:
                print(f"[Error] 购买的邮箱信息不完整: email={email}, token={'有' if token else '无'}")
                print(f"[*] 完整数据: {purchase_data}")
                if attempt == max_retries:
                    return "", ""
                continue

            print(f"[*] 成功购买邮箱: {email}")
            print(f"[*] 邮箱 Token: {token[:20]}...")

            # 检测邮箱活跃度
            print(f"[*] 检测邮箱活跃度...")
            is_alive, msg = luckmail_check_email_alive(token, proxies=proxies)
            print(f"[*] 检测结果: {msg}")

            if not is_alive:
                print(f"[Warning] 邮箱不活跃，禁用该邮箱并重新购买...")
                # 禁用不活跃的邮箱
                if luckmail_disable_email(purchase_id, disabled=True, proxies=proxies):
                    print(f"[*] 已禁用不活跃邮箱: {email}")
                else:
                    print(f"[Warning] 禁用邮箱失败: {email}")

                if attempt < max_retries:
                    time.sleep(2)
                    continue
                else:
                    print(f"[Error] 已达到最大重试次数，无法获取活跃邮箱")
                    return "", ""

            # 邮箱活跃，保存凭据
            print(f"[*] 邮箱活跃，可以使用!")
            _luckmail_credentials[email] = {
                "token": token,
                "purchase_id": purchase_id,
                "email_data": purchase_data,
            }
            return email, email

        return "", ""
    prefix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    email = f"{prefix}@{MAIL_DOMAIN}"
    return email, email



def _extract_otp_code(content: str) -> str:
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


def get_oai_code(token: str, email: str, proxies: Any = None, seen_ids: set = None) -> str:
    """根据 EMAIL_MODE 获取 OpenAI 验证码: cf=Cloudflare Worker, hotmail007=Outlook Graph/IMAP, luckmail=LuckMail API"""
    if EMAIL_MODE == "hotmail007":
        creds = _hotmail007_credentials.get(email, {})
        if not creds:
            print(f"[Error] 未找到 {email} 的 Hotmail007 凭据")
            return ""
        known_ids = creds.get("known_ids", set())
        return _outlook_fetch_otp(
            email, creds["client_id"], creds["refresh_token"],
            known_ids=known_ids, proxies=proxies, timeout=120,
        )
    if EMAIL_MODE == "luckmail":
        creds = _luckmail_credentials.get(email, {})
        if not creds:
            print(f"[Error] 未找到 {email} 的 LuckMail 凭据")
            return ""

        # 检查是否是已购邮箱模式（有token）还是接码模式（有order_no）
        email_token = creds.get("token")
        order_no = creds.get("order_no")

        if email_token:
            # 已购邮箱模式：使用token直接获取验证码
            print(f"[*] 使用已购邮箱Token获取验证码...", end="", flush=True)
            start_time = time.time()
            while time.time() - start_time < 120:
                code = luckmail_get_code_by_token(email_token, proxies=proxies)
                if code:
                    print(f" 抓到啦! 验证码: {code}")
                    return code
                print(".", end="", flush=True)
                time.sleep(3)
            print(" 超时，未收到验证码")
            return ""
        elif order_no:
            # 接码模式：使用订单号获取验证码
            print(f"[*] 轮询获取验证码 (订单: {order_no})...", end="", flush=True)
            start_time = time.time()
            while time.time() - start_time < 120:
                code = luckmail_get_code(order_no, proxies=proxies)
                if code:
                    print(f" 抓到啦! 验证码: {code}")
                    return code
                print(".", end="", flush=True)
                time.sleep(3)
            print(" 超时，未收到验证码")
            return ""
        else:
            # 没有token也没有order_no，尝试创建接码订单
            print(f"[*] 创建验证码订单...")
            new_order_no, err = luckmail_create_order(email, proxies=proxies)
            if err or not new_order_no:
                print(f"[Error] 创建验证码订单失败: {err}")
                return ""
            order_no = new_order_no
            _luckmail_credentials[email]["order_no"] = order_no
            print(f"[*] 验证码订单创建成功: {order_no}")
            print(f"[*] 轮询获取验证码 (订单: {order_no})...", end="", flush=True)
            start_time = time.time()
            while time.time() - start_time < 120:
                code = luckmail_get_code(order_no, proxies=proxies)
                if code:
                    print(f" 抓到啦! 验证码: {code}")
                    return code
                print(".", end="", flush=True)
                time.sleep(3)
            print(" 超时，未收到验证码")
            return ""
    headers = {
        "x-admin-auth": MAIL_ADMIN_PASSWORD,
        "Content-Type": "application/json",
    }
    if seen_ids is None:
        seen_ids = set()
    print(f"[*] 正在等待邮箱 {email} 的验证码...", end="", flush=True)

    for _ in range(40):
        print(".", end="", flush=True)
        try:
            res = requests.get(
                f"{MAIL_WORKER_BASE}/admin/mails",
                params={"limit": 5, "offset": 0, "address": email},
                headers=headers,
                proxies=proxies,
                impersonate="safari",
                verify=_ssl_verify(),
                timeout=15,
            )
            if res.status_code == 200:
                j = res.json()
                results = j.get("results") or []
                for mail in results:
                    mail_id = mail.get("id")
                    if mail_id in seen_ids:
                        continue
                    seen_ids.add(mail_id)
                    raw = mail.get("raw") or ""
                    content = raw
                    subj_match = re.search(
                        r"^Subject:\s*(.+)$", raw, re.MULTILINE
                    )
                    if subj_match:
                        content = subj_match.group(1) + "\n" + raw
                    code = _extract_otp_code(content)
                    if code:
                        print(" 抓到啦! 验证码:", code)
                        return code
        except Exception:
            pass

        time.sleep(3)

    print(" 超时，未收到验证码")
    return ""


def delete_temp_email(email: str, proxies: Any = None) -> None:
    """注册成功后清理邮箱: hotmail007模式仅清理本地凭据, cf模式删除Worker邮件, luckmail模式清理本地凭据并禁用邮箱"""
    if EMAIL_MODE == "hotmail007":
        _hotmail007_credentials.pop(email, None)
        print(f"[*] Hotmail007 邮箱 {email} 本地凭据已清理")
        return
    if EMAIL_MODE == "luckmail":
        creds = _luckmail_credentials.pop(email, None)
        # 注册成功后禁用邮箱
        if creds and "purchase_id" in creds:
            purchase_id = creds["purchase_id"]
            try:
                if luckmail_disable_email(purchase_id, disabled=True, proxies=proxies):
                    print(f"[*] LuckMail 邮箱 {email} 已禁用 (注册成功)")
                else:
                    print(f"[Warning] LuckMail 邮箱 {email} 禁用失败")
            except Exception as e:
                print(f"[Warning] 禁用邮箱 {email} 时出错: {e}")
        else:
            print(f"[*] LuckMail 邮箱 {email} 本地凭据已清理")
        return
    headers = {
        "x-admin-auth": MAIL_ADMIN_PASSWORD,
        "Content-Type": "application/json",
    }
    try:
        res = requests.get(
            f"{MAIL_WORKER_BASE}/admin/mails",
            params={"limit": 50, "offset": 0, "address": email},
            headers=headers,
            proxies=proxies,
            impersonate="safari",
            verify=_ssl_verify(),
            timeout=15,
        )
        if res.status_code == 200:
            for mail in (res.json().get("results") or []):
                mail_id = mail.get("id")
                if mail_id:
                    requests.delete(
                        f"{MAIL_WORKER_BASE}/admin/mails/{mail_id}",
                        headers=headers,
                        proxies=proxies,
                        impersonate="safari",
                        verify=_ssl_verify(),
                        timeout=10,
                    )
        print(f"[*] 临时邮箱 {email} 的邮件已清理")
    except Exception as e:
        print(f"[*] 清理临时邮箱时出错: {e}")


# ==========================================
# Hotmail007 API & Outlook OTP
# ==========================================

_hotmail007_credentials: Dict[str, dict] = {}
_luckmail_credentials: Dict[str, dict] = {}


def _hotmail007_api_get(path: str, proxies: Any = None, **params) -> dict:
    url = f"{HOTMAIL007_API_URL}/{path.lstrip('/')}"
    if params:
        qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items() if v)
        url = f"{url}?{qs}"
    try:
        r = requests.get(url, proxies=proxies, verify=_ssl_verify(), timeout=15, impersonate="safari")
        return r.json()
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}


def hotmail007_get_balance(proxies: Any = None) -> tuple:
    data = _hotmail007_api_get("api/user/balance", proxies=proxies, clientKey=HOTMAIL007_API_KEY)
    if data.get("success") and data.get("code") == 0:
        return data.get("data"), None
    return None, data.get("message", "查询余额失败")


def hotmail007_get_stock(proxies: Any = None) -> tuple:
    params = {"clientKey": HOTMAIL007_API_KEY}
    if HOTMAIL007_MAIL_TYPE:
        params["mailType"] = HOTMAIL007_MAIL_TYPE
    data = _hotmail007_api_get("api/mail/getStock", proxies=proxies, **params)
    if data.get("success") and data.get("code") == 0:
        raw = data.get("data")
        if isinstance(raw, (int, float)):
            return int(raw), None
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    t = (item.get("type") or "").strip().lower()
                    if t == HOTMAIL007_MAIL_TYPE.strip().lower():
                        return int(item.get("stock", 0)), None
            total = sum(int(item.get("stock", 0)) for item in raw if isinstance(item, dict))
            return total, None
        return 0, None
    return None, data.get("message", "查询库存失败")


def hotmail007_get_mail(quantity: int = 1, proxies: Any = None) -> tuple:
    data = _hotmail007_api_get(
        "api/mail/getMail", proxies=proxies,
        clientKey=HOTMAIL007_API_KEY,
        mailType=HOTMAIL007_MAIL_TYPE,
        quantity=quantity,
    )
    if not data.get("success") or data.get("code") != 0:
        return [], data.get("message", "拉取邮箱失败")
    raw_list = data.get("data") or []
    out = []
    for raw in raw_list:
        if not isinstance(raw, str):
            continue
        parts = raw.split(":")
        if len(parts) < 4:
            continue
        email_addr = parts[0].strip()
        pwd = parts[1].strip()
        cid = parts[-1].strip()
        rtk = ":".join(parts[2:-1]).strip()
        if email_addr:
            out.append({"email": email_addr, "password": pwd, "refresh_token": rtk, "client_id": cid})
    if not out:
        return [], "API 返回数据解析为空"
    return out, ""


# ==========================================
# LuckMail API
# ==========================================

def _luckmail_api_request(method: str, endpoint: str, proxies: Any = None, **kwargs) -> dict:
    """通用 LuckMail API 请求"""
    try:
        headers = {"X-API-Key": LUCKMAIL_API_KEY, "Content-Type": "application/json"}
        url = f"{LUCKMAIL_API_URL}/{endpoint.lstrip('/')}"
        
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, params=kwargs, proxies=proxies, timeout=15)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, json=kwargs, proxies=proxies, timeout=15)
        else:
            return {"code": 9999, "message": "不支持的请求方法", "data": None}
            
        return response.json()
    except Exception as e:
        print(f"[Error] LuckMail API 调用失败: {e}")
        return {"code": 9999, "message": str(e), "data": None}


def luckmail_get_purchases(proxies: Any = None) -> tuple:
    """获取已购 hotmail 邮箱列表"""
    data = _luckmail_api_request("GET", "email/purchases", proxies=proxies)
    if data.get("code") == 0:
        all_mails = data.get("data", {}).get("list", [])
        hotmail_mails = [mail for mail in all_mails if "hotmail.com" in mail.get("email_address", "")]
        return hotmail_mails, None
    return [], data.get("message", "获取已购邮箱失败")


def luckmail_buy_email(proxies: Any = None, email_type: str = "ms_imap") -> tuple:
    """购买 hotmail/outlook 邮箱"""
    data = _luckmail_api_request("POST", "email/purchase",
                                proxies=proxies,
                                email_type=email_type,
                                project_code="openai",
                                domain="hotmail.com",
                                quantity=1,
                                variant_mode="")
    if data.get("code") == 0:
        # API返回: data.purchases 列表
        response_data = data.get("data", {})
        purchases = response_data.get("purchases", [])
        if purchases and len(purchases) > 0:
            return purchases[0], None  # 返回第一条购买记录
        return None, "API返回数据中没有购买记录"
    return None, data.get("message", "购买邮箱失败")


def luckmail_check_email_alive(token: str, proxies: Any = None) -> tuple:
    """检测已购邮箱活跃度/可用性
    返回: (is_alive: bool, error_message: str)
    """
    try:
        headers = {"X-API-Key": LUCKMAIL_API_KEY, "Content-Type": "application/json"}
        url = f"{LUCKMAIL_API_URL}/email/token/{token}/alive"
        response = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        data = response.json()

        if data.get("code") == 0:
            result = data.get("data", {})
            # API返回字段是 "alive": true/false
            is_alive = result.get("alive", False)
            email_addr = result.get("email_address", "未知")
            status_msg = result.get("message", "")
            mail_count = result.get("mail_count", 0)

            if is_alive:
                return True, f"邮箱活跃 ({email_addr}, 邮件数: {mail_count}, {status_msg})"
            else:
                return False, f"邮箱不活跃 ({email_addr}, {status_msg})"
        else:
            return False, data.get("message", "检测失败")
    except Exception as e:
        return False, f"检测异常: {e}"


def luckmail_disable_email(purchase_id: int, disabled: bool = True, proxies: Any = None) -> bool:
    """设置已购邮箱的禁用状态
    disabled=True 表示禁用, False 表示启用
    """
    try:
        headers = {"X-API-Key": LUCKMAIL_API_KEY, "Content-Type": "application/json"}
        url = f"{LUCKMAIL_API_URL}/email/purchases/{purchase_id}/disabled"
        payload = {"disabled": 1 if disabled else 0}
        response = requests.put(url, headers=headers, json=payload, proxies=proxies, timeout=15)
        data = response.json()
        return data.get("code") == 0
    except Exception as e:
        print(f"[Error] 禁用邮箱失败: {e}")
        return False


def luckmail_batch_buy_and_check(quantity: int = 10, max_workers: int = 5, proxies: Any = None, email_type: str = "ms_imap") -> tuple:
    """批量购买邮箱并并行检测活跃度
    返回: (活跃邮箱列表, 错误信息)  ([{email, token, id}, ...], error_msg)
    """
    print(f"[*] 批量购买 {quantity} 个邮箱 (类型: {email_type})...")

    # 1. 批量购买
    data = _luckmail_api_request("POST", "email/purchase",
                                proxies=proxies,
                                email_type=email_type,
                                project_code="openai",
                                domain="hotmail.com",
                                quantity=quantity,
                                variant_mode="")

    if data.get("code") != 0:
        error_msg = data.get('message', '未知错误')
        print(f"[Error] 批量购买失败: {error_msg}")
        return [], error_msg

    response_data = data.get("data", {})
    purchases = response_data.get("purchases", [])

    if not purchases:
        print("[Error] 没有购买到任何邮箱")
        return [], "没有购买到任何邮箱"

    print(f"[*] 成功购买 {len(purchases)} 个邮箱，开始并行检测活跃度...")

    # 2. 并行检测活跃度
    active_emails = []
    inactive_emails = []
    disabled_count = 0
    lock = threading.Lock()

    def check_single_email(purchase):
        nonlocal disabled_count
        email = purchase.get("email_address")
        token = purchase.get("token")
        pid = purchase.get("id")

        if not email or not token:
            return None, None

        is_alive, msg = luckmail_check_email_alive(token, proxies)

        if is_alive:
            return {"email": email, "token": token, "id": pid}, None
        else:
            # 禁用不活跃的邮箱
            disabled_ok = False
            if luckmail_disable_email(pid, disabled=True, proxies=proxies):
                with lock:
                    disabled_count += 1
                disabled_ok = True
            return None, {"email": email, "disabled_ok": disabled_ok}

    # 使用线程池并行检测
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_single_email, p): p for p in purchases}
        for future in concurrent.futures.as_completed(futures):
            active_result, inactive_result = future.result()
            if active_result:
                active_emails.append(active_result)
            elif inactive_result:
                inactive_emails.append(inactive_result)

    # 简洁的输出
    inactive_count = len(purchases) - len(active_emails)
    print(f"[*] 检测完成: ✓活跃 {len(active_emails)} 个, ✗不活跃 {inactive_count} 个(已禁用{disabled_count}个)")

    # 显示活跃的邮箱
    if active_emails:
        print(f"[*] 活跃邮箱列表:")
        for email_data in active_emails:
            print(f"    ✓ {email_data['email']}")

    return active_emails, None


def luckmail_get_purchased_emails(proxies: Any = None, page: int = 1, page_size: int = 50, user_disabled: int = 0) -> tuple:
    """获取已购邮箱列表
    user_disabled: 0=正常(非禁用), 1=已禁用
    返回: (邮箱列表, 错误信息)
    """
    try:
        headers = {"X-API-Key": LUCKMAIL_API_KEY, "Content-Type": "application/json"}
        url = f"{LUCKMAIL_API_URL}/email/purchases"
        params = {
            "page": page,
            "page_size": page_size,
            "user_disabled": user_disabled
        }
        response = requests.get(url, headers=headers, params=params, proxies=proxies, timeout=15)
        data = response.json()

        if data.get("code") == 0:
            mails = data.get("data", {}).get("list", [])
            return mails, None
        return [], data.get("message", "获取已购邮箱失败")
    except Exception as e:
        return [], f"获取已购邮箱异常: {e}"


def luckmail_check_purchased_emails(proxies: Any = None, max_workers: int = 5) -> list:
    """检查已购邮箱活跃度，返回活跃邮箱列表
    只检查非禁用的邮箱，不活跃的自动禁用
    """
    print(f"[*] 获取已购邮箱列表...")
    mails, err = luckmail_get_purchased_emails(proxies=proxies, user_disabled=0)
    if err:
        print(f"[Error] 获取已购邮箱失败: {err}")
        return []

    if not mails:
        print(f"[*] 没有已购的非禁用邮箱")
        return []

    print(f"[*] 获取到 {len(mails)} 个已购邮箱，开始检测活跃度...")

    active_emails = []
    disabled_count = 0
    lock = threading.Lock()

    def check_single_email(mail):
        nonlocal disabled_count
        email = mail.get("email_address")
        token = mail.get("token")
        pid = mail.get("id")

        if not email or not token:
            return None

        is_alive, msg = luckmail_check_email_alive(token, proxies)

        if is_alive:
            return {"email": email, "token": token, "id": pid}
        else:
            # 禁用不活跃的邮箱
            if luckmail_disable_email(pid, disabled=True, proxies=proxies):
                with lock:
                    disabled_count += 1
            return None

    # 使用线程池并行检测
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_single_email, m): m for m in mails}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                active_emails.append(result)

    print(f"[*] 已购邮箱检测完成: ✓活跃 {len(active_emails)}/{len(mails)} 个, 已禁用 {disabled_count} 个不活跃邮箱")
    return active_emails


def luckmail_create_order(email: str, proxies: Any = None) -> tuple:
    """创建验证码订单"""
    data = _luckmail_api_request("POST", "order/create",
                                proxies=proxies,
                                project_code="openai",
                                email_type="ms_imap",
                                domain="hotmail.com",
                                specified_email="",
                                variant_mode="")
    if data.get("code") == 0:
        return data.get("data", {}).get("order_no"), data.get("data", {})
    return None, data.get("message", "创建订单失败")


def luckmail_get_code(order_no: str, proxies: Any = None) -> str:
    """获取验证码"""
    data = _luckmail_api_request("GET", f"order/{order_no}/code", proxies=proxies)
    if data.get("code") == 0:
        result = data.get("data", {})
        if result.get("status") == "success":
            return result.get("verification_code", "")
    return ""


def luckmail_get_code_by_token(token: str, proxies: Any = None) -> str:
    """通过 Token 获取最新验证码（用于已购邮箱）"""
    try:
        headers = {"X-API-Key": LUCKMAIL_API_KEY, "Content-Type": "application/json"}
        url = f"{LUCKMAIL_API_URL}/email/token/{token}/code"
        response = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        data = response.json()

        if data.get("code") == 0:
            result = data.get("data", {})
            # 检查是否有验证码
            code = result.get("code", "")
            if code:
                return code
            # 或者检查 verification_code 字段
            verification_code = result.get("verification_code", "")
            if verification_code:
                return verification_code
        return ""
    except Exception as e:
        print(f"[Error] 通过Token获取验证码失败: {e}")
        return ""


def _outlook_get_graph_token(client_id: str, refresh_token: str, proxies: Any = None) -> str:
    url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "https://graph.microsoft.com/.default",
    }
    r = requests.post(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        proxies=proxies, verify=_ssl_verify(), timeout=30, impersonate="safari",
    )
    j = r.json()
    if not j.get("access_token"):
        err = j.get("error_description", j.get("error", str(j)))
        if "service abuse" in (err or "").lower():
            raise Exception(f"账号被封禁: {err}")
        raise Exception(f"Graph token 失败: {err[:150]}")
    return j["access_token"]


def _outlook_get_imap_token(client_id: str, refresh_token: str, proxies: Any = None,
                            email_addr: str = "") -> tuple:
    import imaplib as _imaplib
    methods = [
        {
            "url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            "data": {
                "client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh_token,
                "scope": "https://outlook.office365.com/IMAP.AccessAsUser.All offline_access",
            },
            "imap_server": "outlook.office365.com",
        },
        {
            "url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            "data": {
                "client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh_token,
                "scope": "https://outlook.office365.com/IMAP.AccessAsUser.All offline_access",
            },
            "imap_server": "outlook.office365.com",
        },
        {
            "url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            "data": {
                "client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh_token,
                "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
            },
            "imap_server": "outlook.live.com",
        },
        {
            "url": "https://login.live.com/oauth20_token.srf",
            "data": {"client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh_token},
            "imap_server": "outlook.office365.com",
        },
    ]
    last_err = ""
    for idx, m in enumerate(methods):
        try:
            r = requests.post(
                m["url"], data=m["data"],
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                proxies=proxies, verify=_ssl_verify(), timeout=30, impersonate="safari",
            )
            j = r.json()
            if not j.get("access_token"):
                last_err = j.get("error_description", j.get("error", str(j)))
                if "service abuse" in (last_err or "").lower():
                    raise Exception(f"账号被封禁: {last_err}")
                continue
            token = j["access_token"]
            server = m["imap_server"]
            if email_addr:
                try:
                    imap_test = _imaplib.IMAP4_SSL(server, 993)
                    auth_str = f"user={email_addr}auth=Bearer {token}"
                    imap_test.authenticate("XOAUTH2", lambda x: auth_str.encode("utf-8"))
                    imap_test.select("INBOX")
                    imap_test.logout()
                    print(f"[IMAP] 方法{idx+1}验证通过: {server}")
                    return token, server
                except Exception as ve:
                    last_err = f"方法{idx+1} SELECT失败({server}): {ve}"
                    print(f"[IMAP] {last_err}")
                    continue
            else:
                return token, server
        except Exception as e:
            if "封禁" in str(e):
                raise
            last_err = str(e)
    raise Exception(f"IMAP 所有方法均失败: {last_err[:200]}")

def _outlook_graph_get_openai_messages(access_token: str, proxies: Any = None, top: int = 10) -> list:
    all_items = []
    headers_dict = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    params = {
        "$select": "id,subject,body,from,receivedDateTime",
        "$orderby": "receivedDateTime desc",
        "$top": str(top * 5),
    }
    for folder in ["inbox", "junkemail"]:
        url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"
        try:
            r = requests.get(
                url, params=params,
                headers=headers_dict,
                proxies=proxies, verify=_ssl_verify(), timeout=30, impersonate="safari",
            )
            if r.status_code == 200:
                all_items.extend(r.json().get("value", []))
        except Exception:
            pass
    if not all_items:
        url_all = "https://graph.microsoft.com/v1.0/me/messages"
        try:
            r = requests.get(
                url_all, params=params,
                headers=headers_dict,
                proxies=proxies, verify=_ssl_verify(), timeout=30, impersonate="safari",
            )
            if r.status_code == 200:
                all_items = r.json().get("value", [])
        except Exception:
            pass
    return [
        m for m in all_items
        if "openai.com" in (m.get("from") or {}).get("emailAddress", {}).get("address", "").lower()
    ]

def _outlook_graph_extract_otp(message: dict) -> str:
    subject = message.get("subject", "")
    body_content = (message.get("body") or {}).get("content", "")
    text = subject + "\n" + body_content
    for pat in [r'>\s*(\d{6})\s*<', r'code[:\s]+(\d{6})', r'(\d{6})\s*\n', r'(?<!\d)(\d{6})(?!\d)']:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
    return ""


def _outlook_get_known_ids(email_addr: str, client_id: str, refresh_token: str,
                           proxies: Any = None) -> set:
    try:
        token = _outlook_get_graph_token(client_id, refresh_token, proxies)
        messages = _outlook_graph_get_openai_messages(token, proxies)
        known = {m["id"] for m in messages}
        print(f"[Graph] 已有 {len(known)} 封 OpenAI 邮件")
        return known
    except Exception as e:
        print(f"[Graph] 获取已有邮件失败: {e}")
        return set()


def _outlook_fetch_otp_graph(email_addr: str, client_id: str, refresh_token: str,
                             known_ids: set, proxies: Any = None, timeout: int = 120) -> str:
    try:
        access_token = _outlook_get_graph_token(client_id, refresh_token, proxies)
    except Exception as e:
        print(f"[Graph] access token 失败: {e}")
        return ""
    _graph_debug_done = False
    print(f"[Graph] 轮询收件箱(最多{timeout}s, 已知{len(known_ids)}封)...", end="", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        print(".", end="", flush=True)
        try:
            messages = _outlook_graph_get_openai_messages(access_token, proxies)
            if not _graph_debug_done:
                _graph_debug_done = True
                headers_dict = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
                for dbg_folder in ["inbox", "junkemail"]:
                    try:
                        dbg_r = requests.get(
                            f"https://graph.microsoft.com/v1.0/me/mailFolders/{dbg_folder}/messages",
                            params={"$top": "3", "$select": "id,subject,from,receivedDateTime"},
                            headers=headers_dict, proxies=proxies, verify=_ssl_verify(), timeout=15, impersonate="safari",
                        )
                        if dbg_r.status_code == 200:
                            dbg_msgs = dbg_r.json().get("value", [])
                            print(f"\n[Graph调试] {dbg_folder}: {len(dbg_msgs)}封邮件", end="", flush=True)
                            for dm in dbg_msgs[:3]:
                                fr = (dm.get("from") or {}).get("emailAddress", {}).get("address", "?")
                                subj = (dm.get("subject") or "")[:40]
                                print(f"\n  - from={fr} subj={subj}", end="", flush=True)
                        else:
                            print(f"\n[Graph调试] {dbg_folder}: HTTP {dbg_r.status_code}", end="", flush=True)
                    except Exception as de:
                        print(f"\n[Graph调试] {dbg_folder}异常: {de}", end="", flush=True)
            all_ids = {m["id"] for m in messages}
            new_ids = all_ids - known_ids
            for msg in [m for m in messages if m["id"] in new_ids]:
                code = _outlook_graph_extract_otp(msg)
                if code:
                    print(f" 抓到啦! 验证码: {code}")
                    return code
        except Exception as e:
            print(f"\n[Graph] 轮询出错: {e}", end="", flush=True)
        time.sleep(3)
    print(" 超时，未收到验证码")
    return ""


def _outlook_fetch_otp_imap(email_addr: str, client_id: str, refresh_token: str,
                            known_ids: set, proxies: Any = None, timeout: int = 120) -> str:
    import imaplib
    import email as email_lib
    try:
        access_token, imap_server = _outlook_get_imap_token(client_id, refresh_token, proxies, email_addr=email_addr)
    except Exception as e:
        print(f"[IMAP] access token 失败: {e}")
        return ""
    print(f"[IMAP] 轮询收件箱(最多{timeout}s, 已知{len(known_ids)}封)...", end="", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        print(".", end="", flush=True)
        try:
            imap = imaplib.IMAP4_SSL(imap_server, 993)
            auth_str = f"user={email_addr}\x01auth=Bearer {access_token}\x01\x01"
            imap.authenticate("XOAUTH2", lambda x: auth_str.encode("utf-8"))
            try:
                imap.select("INBOX")
                status, msg_ids = imap.search(None, '(FROM "noreply@tm.openai.com")')
                if status != "OK" or not msg_ids[0]:
                    status, msg_ids = imap.search(None, '(FROM "openai.com")')
                if status == "OK" and msg_ids[0]:
                    all_ids = set(msg_ids[0].split())
                    new_ids = all_ids - known_ids
                    for mid in sorted(new_ids, key=lambda x: int(x), reverse=True):
                        st, msg_data = imap.fetch(mid, "(RFC822)")
                        if st != "OK":
                            continue
                        msg = email_lib.message_from_bytes(msg_data[0][1])
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() in ("text/plain", "text/html"):
                                    try:
                                        body += (part.get_payload(decode=True) or b"").decode(
                                            part.get_content_charset() or "utf-8", errors="ignore")
                                    except Exception:
                                        pass
                        else:
                            try:
                                body = (msg.get_payload(decode=True) or b"").decode(
                                    msg.get_content_charset() or "utf-8", errors="ignore")
                            except Exception:
                                pass
                        code = _extract_otp_code(body)
                        if code:
                            print(f" 抓到啦! 验证码: {code}")
                            return code
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass
        except Exception as e:
            err_str = str(e)
            print(f"\n[IMAP] 轮询出错: {e}", end="", flush=True)
            if "not connected" in err_str.lower() or "authenticated but not connected" in err_str.lower():
                try:
                    access_token, imap_server = _outlook_get_imap_token(client_id, refresh_token, proxies, email_addr=email_addr)
                    time.sleep(1)
                    continue
                except Exception:
                    pass
        time.sleep(3)
    print(" 超时，未收到验证码")
    return ""


def _outlook_fetch_otp(email_addr: str, client_id: str, refresh_token: str,
                       known_ids: set = None, proxies: Any = None, timeout: int = 120) -> str:
    if known_ids is None:
        known_ids = set()
    return _outlook_fetch_otp_graph(email_addr, client_id, refresh_token, known_ids, proxies, timeout)


# ==========================================
# OAuth 授权与辅助函数
# ==========================================

AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_REDIRECT_URI = f"http://localhost:1455/auth/callback"
DEFAULT_SCOPE = "openid email profile offline_access"


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())


def _random_state(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _parse_callback_url(callback_url: str) -> Dict[str, Any]:
    candidate = callback_url.strip()
    if not candidate:
        return {"code": "", "state": "", "error": "", "error_description": ""}

    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = f"http://localhost{candidate}"
        elif any(ch in candidate for ch in "/?#") or ":" in candidate:
            candidate = f"http://{candidate}"
        elif "=" in candidate:
            candidate = f"http://localhost/?{candidate}"

    parsed = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)

    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values

    def get1(k: str) -> str:
        v = query.get(k, [""])
        return (v[0] or "").strip()

    code = get1("code")
    state = get1("state")
    error = get1("error")
    error_description = get1("error_description")

    if code and not state and "#" in code:
        code, state = code.split("#", 1)

    if not error and error_description:
        error, error_description = error_description, ""

    return {
        "code": code,
        "state": state,
        "error": error,
        "error_description": error_description,
    }


def _jwt_claims_no_verify(id_token: str) -> Dict[str, Any]:
    if not id_token or id_token.count(".") < 2:
        return {}
    payload_b64 = id_token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        payload = base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii"))
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return {}


def _decode_jwt_segment(seg: str) -> Dict[str, Any]:
    raw = (seg or "").strip()
    if not raw:
        return {}
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        decoded = base64.urlsafe_b64decode((raw + pad).encode("ascii"))
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _post_form(url: str, data: Dict[str, str], timeout: int = 30) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        context = None
        if not _ssl_verify():
            context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            raw = resp.read()
            if resp.status != 200:
                raise RuntimeError(
                    f"token exchange failed: {resp.status}: {raw.decode('utf-8', 'replace')}"
                )
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise RuntimeError(
            f"token exchange failed: {exc.code}: {raw.decode('utf-8', 'replace')}"
        ) from exc


def _post_with_retry(
    session: requests.Session,
    url: str,
    *,
    headers: Dict[str, Any],
    data: Any = None,
    json_body: Any = None,
    proxies: Any = None,
    timeout: int = 30,
    retries: int = 2,
) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            if json_body is not None:
                return session.post(
                    url,
                    headers=headers,
                    json=json_body,
                    proxies=proxies,
                    verify=_ssl_verify(),
                    timeout=timeout,
                )
            return session.post(
                url,
                headers=headers,
                data=data,
                proxies=proxies,
                verify=_ssl_verify(),
                timeout=timeout,
            )
        except Exception as e:
            last_error = e
            if attempt >= retries:
                break
            time.sleep(2 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("Request failed without exception")


@dataclass(frozen=True)
class OAuthStart:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str


def generate_oauth_url(
    *, redirect_uri: str = DEFAULT_REDIRECT_URI, scope: str = DEFAULT_SCOPE
) -> OAuthStart:
    state = _random_state()
    code_verifier = _pkce_verifier()
    code_challenge = _sha256_b64url_no_pad(code_verifier)

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return OAuthStart(
        auth_url=auth_url,
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )


def submit_callback_url(
    *,
    callback_url: str,
    expected_state: str,
    code_verifier: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
) -> str:
    cb = _parse_callback_url(callback_url)
    if cb["error"]:
        desc = cb["error_description"]
        raise RuntimeError(f"oauth error: {cb['error']}: {desc}".strip())

    if not cb["code"]:
        raise ValueError("callback url missing ?code=")
    if not cb["state"]:
        raise ValueError("callback url missing ?state=")
    if cb["state"] != expected_state:
        raise ValueError("state mismatch")

    token_resp = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": cb["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
    )

    access_token = (token_resp.get("access_token") or "").strip()
    refresh_token = (token_resp.get("refresh_token") or "").strip()
    id_token = (token_resp.get("id_token") or "").strip()
    expires_in = _to_int(token_resp.get("expires_in"))

    claims = _jwt_claims_no_verify(id_token)
    email = str(claims.get("email") or "").strip()
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()

    now = int(time.time())
    expired_rfc3339 = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0))
    )
    now_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    config = {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
        "last_refresh": now_rfc3339,
        "email": email,
        "type": "codex",
        "expired": expired_rfc3339,
    }

    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


# ==========================================
# 核心注册逻辑
# ==========================================


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
    proxies: Any = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}

    s = requests.Session(proxies=proxies, impersonate="safari")

    if not _skip_net_check():
        try:
            trace = s.get(
                "https://cloudflare.com/cdn-cgi/trace",
                proxies=proxies,
                verify=_ssl_verify(),
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
            return None, None

    email, dev_token = get_email_and_token(proxies)
    if not email or not dev_token:
        return None, None
    print(f"[*] 成功获取临时邮箱与授权: {email}")
    masked = dev_token[:8] + "..." if dev_token else ""
    print(f"[*] 临时邮箱 JWT: {masked}")

    oauth = generate_oauth_url()
    url = oauth.auth_url

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
            verify=_ssl_verify(),
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
            verify=_ssl_verify(),
        )
        signup_status = signup_resp.status_code
        print(f"[*] 提交注册表单状态: {signup_status}")

        if signup_status == 403:
            print("[Error] 提交注册表单返回 403，中断本次运行，将在10秒后重试...")
            return "retry_403", None
        if signup_status != 200:
            print("[Error] 提交注册表单失败，跳过本次流程")
            print(signup_resp.text)
            return None, None

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
            verify=_ssl_verify(),
        )
        print(f"[*] 提交注册(密码)状态: {pwd_resp.status_code}")
        if pwd_resp.status_code != 200:
            print(pwd_resp.text)
            return None, None

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
                otp_send_resp = _post_with_retry(
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
                        _post_with_retry(
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
                code = get_oai_code(token=dev_token, email=email, proxies=proxies, seen_ids=processed_mails)
                if code:
                    break
            if not code:
                print("[Error] 多次重试后仍未收到验证码，跳过")
                return None, None

            print("[*] 开始校验验证码...")
            code_resp = _post_with_retry(
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
        create_account_resp = _post_with_retry(
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
            return None, None

        print("[*] 账户创建完毕，执行静默重登录...")
        s.cookies.clear()

        oauth = generate_oauth_url()
        s.get(oauth.auth_url, proxies=proxies, verify=True, timeout=15)
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
            verify=_ssl_verify(),
            timeout=15,
        )
        sen_token2 = sen_resp2.json().get("token", "") if sen_resp2.status_code == 200 else ""
        sentinel2 = f'{{"p": "", "t": "", "c": "{sen_token2}", "id": "{new_did}", "flow": "authorize_continue"}}'

        _post_with_retry(
            s,
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers={
                "openai-sentinel-token": sentinel2,
                "content-type": "application/json",
            },
            json_body={"username": {"value": email, "kind": "email"}, "screen_hint": "login"},
            proxies=proxies,
        )

        pwd_login_resp = _post_with_retry(
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
                    print("[*] 登录触发二次邮箱验证，等待验证码...")
                    code2 = ""
                    for otp2_attempt in range(5):
                        if otp2_attempt > 0:
                            print(f"\n[*] 二次 OTP 重试 {otp2_attempt}/5，重新发送...")
                            try:
                                _post_with_retry(
                                    s,
                                    "https://auth.openai.com/api/accounts/email-otp/resend",
                                    headers={
                                        "openai-sentinel-token": sentinel2,
                                        "content-type": "application/json",
                                    },
                                    json_body={},
                                    proxies=proxies,
                                    timeout=15,
                                    retries=1,
                                )
                                time.sleep(2)
                            except Exception as e:
                                print(f"[*] 重发异常: {e}")
                        code2 = get_oai_code(token=dev_token, email=email, proxies=proxies, seen_ids=processed_mails)
                        if code2:
                            break
                    if not code2:
                        print("[Error] 二次验证码获取失败")
                        return None, None
                    code2_resp = _post_with_retry(
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
                        return None, None
            except Exception:
                pass

        auth_cookie = s.cookies.get("oai-client-auth-session")
        if not auth_cookie:
            print("[Error] 重登录后未能获取授权 Cookie")
            return None, None

        auth_json = {}
        raw_val = auth_cookie.strip()
        try:
            decoded_val = urllib.parse.unquote(raw_val)
            if decoded_val != raw_val:
                raw_val = decoded_val
        except Exception:
            pass
        for part in raw_val.split("."):
            decoded = _decode_jwt_segment(part)
            if isinstance(decoded, dict) and "workspaces" in decoded:
                auth_json = decoded
                break

        workspaces = auth_json.get("workspaces") or []
        if not workspaces:
            print("[Error] 重登录后 Cookie 里仍没有 workspace 信息")
            return None, None
        workspace_id = str((workspaces[0] or {}).get("id") or "").strip()
        if not workspace_id:
            print("[Error] 无法解析 workspace_id")
            return None, None

        select_body = f'{{"workspace_id":"{workspace_id}"}}'
        print("[*] 开始选择 workspace...")
        select_resp = _post_with_retry(
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
            return None, None

        continue_url = str((select_resp.json() or {}).get("continue_url") or "").strip()
        if not continue_url:
            print("[Error] workspace/select 响应里缺少 continue_url")
            return None, None

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
                    org_resp = _post_with_retry(
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
                verify=_ssl_verify(),
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
                        verify=_ssl_verify(),
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
                token_json = submit_callback_url(
                    callback_url=next_url,
                    code_verifier=oauth.code_verifier,
                    redirect_uri=oauth.redirect_uri,
                    expected_state=oauth.state,
                )
                return token_json, password
            current_url = next_url
            time.sleep(0.5)

        print("[Error] 未能在重定向链中捕获到最终 Callback URL")
        return None, None

    except Exception as e:
        print(f"[Error] 运行时发生错误: {e}")
        return None, None


# ==========================================
# Token 检测与刷新
# ==========================================

AUTO_REGISTER_THRESHOLD = 10

_INVALID_ERRORS = {
    "account_deactivated", "invalid_api_key", "user_deactivated",
    "account_banned", "invalid_grant",
}


def _refresh_token(refresh_tok: str, proxies: Any = None) -> Dict[str, Any]:
    """用 refresh_token 换取新的 access_token"""
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_tok,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            impersonate="safari",
            verify=_ssl_verify(),
            proxies=proxies,
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            now = int(time.time())
            expires_in = max(int(data.get("expires_in", 3600)), 0)
            return {
                "ok": True,
                "access_token": data.get("access_token", ""),
                "refresh_token": data.get("refresh_token", refresh_tok),
                "id_token": data.get("id_token", ""),
                "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "expired": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + expires_in)),
            }
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _test_token(access_token: str, account_id: str = "", proxies: Any = None) -> Dict[str, Any]:
    """调用 ChatGPT API 测试 token 是否有效，返回 {valid, reason}"""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    if account_id:
        headers["Chatgpt-Account-Id"] = account_id
    try:
        resp = requests.get(
            "https://chatgpt.com/backend-api/me",
            headers=headers,
            proxies=proxies,
            impersonate="safari",
            verify=_ssl_verify(),
            timeout=20,
        )
        if resp.status_code == 200:
            try:
                me = resp.json()
                if me.get("id"):
                    return {"valid": True, "reason": "正常"}
            except Exception:
                pass
            return {"valid": True, "reason": "正常"}

        try:
            err_data = resp.json()
            err_detail = err_data.get("detail", "")
            if isinstance(err_detail, dict):
                err_msg = err_detail.get("message", str(err_detail))
            else:
                err_msg = str(err_detail)
        except Exception:
            err_msg = resp.text[:200]

        if any(kw in err_msg.lower() for kw in ("deactivat", "banned", "suspended")):
            return {"valid": False, "reason": f"账号停用/无效 ({err_msg})"}
        if resp.status_code == 401:
            return {"valid": False, "reason": f"认证失败 (401)"}
        if resp.status_code == 403:
            return {"valid": False, "reason": f"禁止访问 (403: {err_msg})"}
        return {"valid": False, "reason": f"HTTP {resp.status_code}: {err_msg}"}
    except Exception as e:
        return {"valid": False, "reason": f"请求异常: {e}"}


def check_codex_tokens(proxies: Any = None) -> Dict[str, int]:
    """扫描 auths 目录下所有 codex token，检测状态并处理"""
    if not os.path.isdir(CLI_PROXY_AUTHS_DIR):
        print(f"[Error] 目录不存在: {CLI_PROXY_AUTHS_DIR}")
        return {"total": 0, "valid": 0, "refreshed": 0, "deleted": 0}

    files = sorted(f for f in os.listdir(CLI_PROXY_AUTHS_DIR) if f.startswith("codex-") and f.endswith(".json"))
    if not files:
        print("[*] 没有找到 codex token 文件")
        return {"total": 0, "valid": 0, "refreshed": 0, "deleted": 0}

    print(f"[*] 共发现 {len(files)} 个 codex token，开始检测...\n")
    valid_count = 0
    refreshed_count = 0
    deleted_count = 0

    for i, fname in enumerate(files, 1):
        fpath = os.path.join(CLI_PROXY_AUTHS_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                token_data = json.loads(f.read())
        except Exception as e:
            print(f"  [{i}/{len(files)}] {fname} - 读取失败: {e}")
            continue

        email = token_data.get("email", fname)
        access_token = token_data.get("access_token", "")
        refresh_tok = token_data.get("refresh_token", "")
        account_id = token_data.get("account_id", "")

        is_expired = False
        claims = _jwt_claims_no_verify(access_token)
        exp_ts = claims.get("exp", 0)
        if exp_ts and int(time.time()) >= exp_ts:
            is_expired = True

        if is_expired:
            print(f"  [{i}/{len(files)}] {email} - access_token 已过期，尝试刷新...", end="")
            result = _refresh_token(refresh_tok, proxies=proxies)
            if result.get("ok"):
                token_data["access_token"] = result["access_token"]
                token_data["refresh_token"] = result["refresh_token"]
                token_data["id_token"] = result.get("id_token", token_data.get("id_token", ""))
                token_data["last_refresh"] = result["last_refresh"]
                token_data["expired"] = result["expired"]
                access_token = result["access_token"]
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(json.dumps(token_data, ensure_ascii=False, separators=(",", ":")))
                print(" 刷新成功!")
                refreshed_count += 1
            else:
                err = result.get("error", "")
                if any(kw in err.lower() for kw in ("deactivat", "invalid_grant", "banned")):
                    os.remove(fpath)
                    print(f" 刷新失败(账号无效)，已删除")
                    deleted_count += 1
                    continue
                else:
                    print(f" 刷新失败: {err}")
                    continue

        test = _test_token(access_token, account_id=account_id, proxies=proxies)
        if test["valid"]:
            print(f"  [{i}/{len(files)}] {email} - 状态正常 ✓")
            valid_count += 1
        else:
            reason = test["reason"]
            if "停用" in reason or "无效" in reason or "deactivat" in reason.lower():
                os.remove(fpath)
                print(f"  [{i}/{len(files)}] {email} - {reason}，已删除")
                deleted_count += 1
            elif "认证失败" in reason or "401" in reason:
                print(f"  [{i}/{len(files)}] {email} - {reason}，尝试刷新...", end="")
                result = _refresh_token(refresh_tok, proxies=proxies)
                if result.get("ok"):
                    token_data["access_token"] = result["access_token"]
                    token_data["refresh_token"] = result["refresh_token"]
                    token_data["id_token"] = result.get("id_token", token_data.get("id_token", ""))
                    token_data["last_refresh"] = result["last_refresh"]
                    token_data["expired"] = result["expired"]
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(json.dumps(token_data, ensure_ascii=False, separators=(",", ":")))
                    print(" 刷新成功!")
                    refreshed_count += 1
                    valid_count += 1
                else:
                    os.remove(fpath)
                    print(f" 刷新失败，已删除")
                    deleted_count += 1
            else:
                print(f"  [{i}/{len(files)}] {email} - {reason}")

    print(f"\n[*] 检测完毕: 有效 {valid_count} / 刷新 {refreshed_count} / 删除 {deleted_count} / 共 {len(files)}")
    return {"total": len(files), "valid": valid_count, "refreshed": refreshed_count, "deleted": deleted_count}


_file_write_lock = threading.Lock()
_success_counter_lock = threading.Lock()
_success_counter = 0


def _save_result(token_json: str, password: str, proxy_str: Optional[str]) -> None:
    """线程安全地保存注册结果"""
    try:
        t_data = json.loads(token_json)
        fname_email = t_data.get("email", "unknown").replace("@", "_")
        account_email = t_data.get("email", "")
    except Exception:
        fname_email = "unknown"
        account_email = ""

    file_name = f"token_{fname_email}_{int(time.time())}.json"
    if TOKEN_OUTPUT_DIR:
        os.makedirs(TOKEN_OUTPUT_DIR, exist_ok=True)
        file_name = os.path.join(TOKEN_OUTPUT_DIR, file_name)

    with _file_write_lock:
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(token_json)

    print(f"[*] 成功! Token 已保存至: {file_name}")

    if os.path.isdir(CLI_PROXY_AUTHS_DIR) and account_email:
        dest = os.path.join(CLI_PROXY_AUTHS_DIR, f"codex-{account_email}.json")
        with _file_write_lock:
            with open(dest, "w", encoding="utf-8") as df:
                df.write(token_json)
        print(f"[*] Token 已拷贝至: {dest}")
        if os.path.exists(file_name):
            os.remove(file_name)
            print(f"[*] 本地 token 文件已删除: {file_name}")

    if account_email and password:
        accounts_file = os.path.join(TOKEN_OUTPUT_DIR, "accounts.txt") if TOKEN_OUTPUT_DIR else "./tokens/accounts.txt"
        with _file_write_lock:
            os.makedirs(os.path.dirname(accounts_file), exist_ok=True)
            with open(accounts_file, "a", encoding="utf-8") as af:
                af.write(f"{account_email}----{password}\n")
        print(f"[*] 账号密码已追加至: {accounts_file}")

    if account_email:
        proxies_cleanup = {"http": proxy_str, "https": proxy_str} if proxy_str else None
        delete_temp_email(account_email, proxies=proxies_cleanup)


def _worker(
    worker_id: int,
    rotator: ProxyRotator,
    single_proxy: Optional[str],
    sleep_min: int,
    sleep_max: int,
    count_target: Optional[int],
    remaining: Optional[list],
    stop_event: threading.Event,
) -> int:
    """单个注册工作线程，返回本线程成功注册数"""
    global _success_counter
    local_success = 0
    local_round = 0

    while not stop_event.is_set():
        if EMAIL_MODE == "file" and _email_queue is not None and len(_email_queue) == 0:
            print(f"[T{worker_id}] 邮箱队列已用完，停止线程")
            break

        if remaining is not None:
            with _success_counter_lock:
                if remaining[0] <= 0:
                    break
                remaining[0] -= 1

        local_round += 1
        proxy_str = rotator.next() if len(rotator) > 0 else single_proxy
        tag = f"[T{worker_id}#{local_round}]"

        print(f"\n{tag} [{datetime.now().strftime('%H:%M:%S')}] 开始注册 (代理: {proxy_str or '直连'})")

        try:
            token_json, password = run(proxy_str)

            if token_json == "retry_403":
                print(f"{tag} 检测到 403，等待10秒后重试...")
                if remaining is not None:
                    with _success_counter_lock:
                        remaining[0] += 1
                time.sleep(10)
                continue

            if token_json:
                _save_result(token_json, password, proxy_str)
                local_success += 1
                with _success_counter_lock:
                    _success_counter += 1
                print(f"{tag} 注册成功! (本线程累计: {local_success})")
            else:
                print(f"{tag} 本次注册失败")
                if EMAIL_MODE == "file" and _email_queue is not None and len(_email_queue) == 0:
                    print(f"{tag} 邮箱队列已用完，停止线程")
                    break

        except Exception as e:
            print(f"{tag} [Error] 未捕获异常: {e}")

        if count_target == 1 and remaining is None:
            break

        if remaining is not None:
            with _success_counter_lock:
                if remaining[0] <= 0:
                    break

        if not stop_event.is_set():
            wait_time = random.randint(sleep_min, sleep_max)
            print(f"{tag} 休息 {wait_time} 秒...")
            for _ in range(wait_time):
                if stop_event.is_set():
                    break
                time.sleep(1)

    return local_success


def main() -> None:
    global EMAIL_MODE, HOTMAIL007_API_KEY, HOTMAIL007_MAIL_TYPE, HOTMAIL007_MAIL_MODE, _email_queue, LUCKMAIL_API_KEY, LUCKMAIL_AUTO_BUY

    parser = argparse.ArgumentParser(description="OpenAI 自动注册脚本")
    parser.add_argument(
        "--proxy", default=None, help="单个代理地址，如 http://127.0.0.1:7890"
    )
    parser.add_argument(
        "--proxy-file", default=None,
        help="代理列表文件路径 (每行一个代理)，批量注册时自动轮换"
    )
    parser.add_argument("--once", action="store_true", help="只运行一次")
    parser.add_argument(
        "--count", type=int, default=None,
        help="批量注册数量，如 --count 10 注册10个账号"
    )
    parser.add_argument(
        "--threads", type=int, default=1,
        help="并发线程数 (默认1)，配合 --count 或循环模式使用"
    )
    parser.add_argument("--check", action="store_true", help="检测 auths 目录下 codex token 状态")
    parser.add_argument("--sleep-min", type=int, default=5, help="循环模式最短等待秒数")
    parser.add_argument(
        "--sleep-max", type=int, default=30, help="循环模式最长等待秒数"
    )
    parser.add_argument(
        "--email-mode", default=None, choices=["cf", "hotmail007", "file", "luckmail"],
        help="邮箱模式: file=从accounts.txt读取, cf=Cloudflare自有域名, hotmail007=API拉取微软邮箱, luckmail=API拉取已购邮箱 (默认读.env EMAIL_MODE)"
    )
    parser.add_argument(
        "--accounts-file", default=None,
        help="邮箱列表文件路径 (每行一个邮箱)，配合 --email-mode file 使用 (默认 accounts.txt)"
    )
    parser.add_argument("--hotmail007-key", default=None, help="Hotmail007 API Key (覆盖.env)")
    parser.add_argument(
        "--hotmail007-type", default=None,
        help="Hotmail007 邮箱类型，如 'outlook Trusted Graph' (覆盖.env)"
    )
    parser.add_argument(
        "--hotmail007-mail-mode", default=None, choices=["graph", "imap"],
        help="Hotmail007 收信模式: graph=Microsoft Graph API, imap=IMAP协议 (默认graph)"
    )
    parser.add_argument("--luckmail-key", default=None, help="LuckMail API Key (覆盖.env)")
    parser.add_argument("--luckmail-auto-buy", action="store_true", help="LuckMail 自动购买邮箱")
    parser.add_argument("--luckmail-max-retry", type=int, default=None, help="LuckMail 购买邮箱时的最大重试次数 (默认3)")
    args = parser.parse_args()

    if args.email_mode:
        EMAIL_MODE = args.email_mode.strip().lower()
    if args.accounts_file:
        ACCOUNTS_FILE = args.accounts_file.strip()
    if EMAIL_MODE == "file":
        _email_queue = EmailQueue(ACCOUNTS_FILE)
        if len(_email_queue) == 0:
            print(f"[Error] 邮箱文件 {ACCOUNTS_FILE} 为空或不存在，请先填入邮箱地址（一行一个）")
            return
        print(f"[*] 从 {ACCOUNTS_FILE} 加载了 {len(_email_queue)} 个邮箱")
    if args.hotmail007_key:
        HOTMAIL007_API_KEY = args.hotmail007_key.strip()
    if args.hotmail007_type:
        HOTMAIL007_MAIL_TYPE = args.hotmail007_type.strip()
    if args.hotmail007_mail_mode:
        HOTMAIL007_MAIL_MODE = args.hotmail007_mail_mode.strip().lower()
    if args.luckmail_key:
        LUCKMAIL_API_KEY = args.luckmail_key.strip()
    if args.luckmail_auto_buy:
        LUCKMAIL_AUTO_BUY = True
    if args.luckmail_max_retry is not None and args.luckmail_max_retry > 0:
        LUCKMAIL_MAX_RETRY = args.luckmail_max_retry

    proxy_file_path = args.proxy_file or PROXY_FILE
    proxy_list = _load_proxies(proxy_file_path)
    rotator = ProxyRotator(proxy_list)

    effective_single_proxy = args.proxy or SINGLE_PROXY or None

    thread_count = args.threads
    if BATCH_THREADS and thread_count == 1:
        try:
            thread_count = int(BATCH_THREADS)
        except ValueError:
            pass
    thread_count = max(1, thread_count)

    batch_count = args.count
    if batch_count is None and BATCH_COUNT:
        try:
            batch_count = int(BATCH_COUNT)
        except ValueError:
            pass

    if args.check:
        check_proxy = effective_single_proxy
        if not check_proxy and len(rotator) > 0:
            check_proxy = rotator.next()
        proxies_dict = {"http": check_proxy, "https": check_proxy} if check_proxy else None
        stats = check_codex_tokens(proxies=proxies_dict)
        valid_count = stats.get("valid", 0)
        if valid_count >= AUTO_REGISTER_THRESHOLD:
            print(f"[*] 当前可用 token {valid_count} 个，已达到阈值 {AUTO_REGISTER_THRESHOLD}，不执行自动注册")
            return
        need_count = AUTO_REGISTER_THRESHOLD - valid_count
        print(f"[*] 当前可用 token {valid_count} 个，低于阈值 {AUTO_REGISTER_THRESHOLD}，开始自动注册，目标补足 {need_count} 个")
        batch_count = need_count

    sleep_min = max(1, args.sleep_min)
    sleep_max = max(sleep_min, args.sleep_max)

    print("[Info] Yasal's Seamless OpenAI Auto-Registrar Started for ZJH")
    print()
    print("=" * 60)
    print("  20260330更新hotamil邮箱支持")
    print("  QQ群382446")
    print("=" * 60)
    if EMAIL_MODE == "file":
        mode_label = f"文件读取 ({ACCOUNTS_FILE}, 剩余 {len(_email_queue)} 个)"
    elif EMAIL_MODE == "cf":
        mode_label = "Cloudflare Worker (自有域名)"
    elif EMAIL_MODE == "luckmail":
        mode_label = "LuckMail API (hotmail邮箱)"
    else:
        mode_label = "Hotmail007 API (微软邮箱)"
    print(f"  邮箱模式: {mode_label}")
    if len(rotator) > 0:
        print(f"  代理模式: 文件轮换 ({len(rotator)} 个代理)")
    elif effective_single_proxy:
        print(f"  代理模式: 单代理 ({effective_single_proxy})")
    else:
        print(f"  代理模式: 直连 (未配置代理)")
    if batch_count:
        print(f"  批量数量: {batch_count}")
    print(f"  并发线程: {thread_count}")
    if EMAIL_MODE == "hotmail007":
        print(f"  API 地址: {HOTMAIL007_API_URL}")
        print(f"  邮箱类型: {HOTMAIL007_MAIL_TYPE}")
        print(f"  收信模式: {HOTMAIL007_MAIL_MODE.upper()}")
        check_proxy_str = effective_single_proxy
        if not check_proxy_str and len(rotator) > 0:
            check_proxy_str = rotator.next()
        proxies_check = {"http": check_proxy_str, "https": check_proxy_str} if check_proxy_str else None
        bal, bal_err = hotmail007_get_balance(proxies=proxies_check)
        if bal is not None:
            print(f"  账户余额: {bal}")
        else:
            print(f"  账户余额: 查询失败 ({bal_err})")
        stk, stk_err = hotmail007_get_stock(proxies=proxies_check)
        if stk is not None:
            print(f"  当前库存: {stk}")
        else:
            print(f"  当前库存: 查询失败 ({stk_err})")
    print("=" * 60)
    print()

    # 如果是 LuckMail 模式且启用了自动购买，启动预检测后台线程
    prefetch_thread = None
    if EMAIL_MODE == "luckmail" and LUCKMAIL_AUTO_BUY:
        print("[*] 启动预检测后台线程，维护活跃邮箱池...")
        global _active_email_queue
        if _active_email_queue is None:
            _active_email_queue = ActiveEmailQueue()
        prefetch_thread = threading.Thread(
            target=_prefetch_active_emails,
            args=(rotator, 10, 20),  # 最小池大小10，批量购买20
            daemon=True
        )
        prefetch_thread.start()
        # 等待预检测线程准备第一批邮箱
        print("[*] 等待预检测线程准备活跃邮箱...")
        wait_count = 0
        while len(_active_email_queue) < 3 and wait_count < 30:  # 最多等30秒
            time.sleep(1)
            wait_count += 1
        print(f"[*] 当前活跃邮箱池: {len(_active_email_queue)} 个")

    if EMAIL_MODE == "file" and _email_queue is not None and not batch_count:
        batch_count = len(_email_queue)
        print(f"[*] file 模式自动设置批量数量: {batch_count}")

    if args.once and not batch_count:
        batch_count = 1

    if batch_count and batch_count > 0:
        remaining = [batch_count]
        stop_event = threading.Event()
        actual_threads = min(thread_count, batch_count)

        if actual_threads <= 1:
            _worker(
                worker_id=1,
                rotator=rotator,
                single_proxy=effective_single_proxy,
                sleep_min=sleep_min,
                sleep_max=sleep_max,
                count_target=batch_count,
                remaining=remaining,
                stop_event=stop_event,
            )
        else:
            print(f"[*] 启动 {actual_threads} 个并发线程...")
            threads = []
            for tid in range(1, actual_threads + 1):
                t = threading.Thread(
                    target=_worker,
                    args=(tid, rotator, effective_single_proxy, sleep_min, sleep_max,
                          batch_count, remaining, stop_event),
                    daemon=True,
                )
                threads.append(t)
                t.start()
                time.sleep(1)

            try:
                for t in threads:
                    t.join()
            except KeyboardInterrupt:
                print("\n[*] 收到中断信号，正在停止所有线程...")
                stop_event.set()
                for t in threads:
                    t.join(timeout=5)

        print(f"\n[*] 批量注册完毕! 共成功: {_success_counter} / 目标: {batch_count}")

    else:
        stop_event = threading.Event()

        if thread_count <= 1:
            try:
                _worker(
                    worker_id=1,
                    rotator=rotator,
                    single_proxy=effective_single_proxy,
                    sleep_min=sleep_min,
                    sleep_max=sleep_max,
                    count_target=None,
                    remaining=None,
                    stop_event=stop_event,
                )
            except KeyboardInterrupt:
                print("\n[*] 收到中断信号，停止运行")
        else:
            print(f"[*] 启动 {thread_count} 个并发线程 (循环模式)...")
            threads = []
            for tid in range(1, thread_count + 1):
                t = threading.Thread(
                    target=_worker,
                    args=(tid, rotator, effective_single_proxy, sleep_min, sleep_max,
                          None, None, stop_event),
                    daemon=True,
                )
                threads.append(t)
                t.start()
                time.sleep(1)

            try:
                while any(t.is_alive() for t in threads):
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n[*] 收到中断信号，正在停止所有线程...")
                stop_event.set()
                for t in threads:
                    t.join(timeout=5)


if __name__ == "__main__":
    main()
