import concurrent.futures
import re
import threading
import time
from typing import Any

from curl_cffi import requests

from . import context as ctx
from .cf_mail import extract_otp_code
from .ui import rich_print as print


def _store_luckmail_credential(email: str, **credential_data) -> tuple[str, str]:
    ctx._luckmail_credentials[email] = credential_data
    return email, email


def _create_order_email(proxies: Any = None) -> tuple[str, str]:
    order_no, order_data = luckmail_create_order("", proxies=proxies)
    if not order_no:
        print(f"[Error] 创建接码订单失败: {order_data}")
        return "", ""
    email = order_data.get("email_address")
    if not email:
        print("[Error] 未获取到邮箱地址")
        return "", ""
    print(f"[*] 接码订单创建成功: {order_no}")
    print(f"[*] 自动分配邮箱: {email}")
    return _store_luckmail_credential(email, order_no=order_no)


def _poll_for_code(fetch_code, label: str, proxies: Any = None, timeout: int = 120) -> str:
    print(label, end="", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        code = fetch_code(proxies=proxies)
        if code:
            print(f" 抓到啦! 验证码: {code}")
            return code
        print(".", end="", flush=True)
        time.sleep(3)
    print(" 超时，未收到验证码")
    return ""


def _is_hotmail_address(email: str) -> bool:
    return email.strip().lower().endswith("@hotmail.com")


def _filter_hotmail_purchases(mails: list[dict]) -> list[dict]:
    return [
        mail_item
        for mail_item in mails
        if _is_hotmail_address(str(mail_item.get("email_address") or ""))
    ]


def _push_active_email(active_queue: ctx.ActiveEmailQueue | None, email_data: dict) -> None:
    if active_queue is not None:
        active_queue.add_batch([email_data])


def _extract_email_address(email_data: dict) -> str:
    return str(email_data.get("email") or email_data.get("address") or email_data.get("email_address") or "").strip()


def _luckmail_api_request(method: str, endpoint: str, proxies: Any = None, **kwargs) -> dict:
    try:
        headers = {"X-API-Key": ctx.LUCKMAIL_API_KEY, "Content-Type": "application/json"}
        url = f"{ctx.LUCKMAIL_API_URL}/{endpoint.lstrip('/')}"
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, params=kwargs, proxies=proxies, timeout=15)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, json=kwargs, proxies=proxies, timeout=15)
        else:
            return {"code": 9999, "message": "不支持的请求方法", "data": None}
        return response.json()
    except Exception as exc:
        print(f"[Error] LuckMail API 调用失败: {exc}")
        return {"code": 9999, "message": str(exc), "data": None}


def luckmail_get_purchases(proxies: Any = None) -> tuple:
    data = _luckmail_api_request("GET", "email/purchases", proxies=proxies)
    if data.get("code") == 0:
        all_mails = data.get("data", {}).get("list", [])
        return _filter_hotmail_purchases(all_mails), None
    return [], data.get("message", "获取已购邮箱失败")


def luckmail_buy_email(proxies: Any = None, email_type: str = "ms_imap") -> tuple:
    data = _luckmail_api_request(
        "POST",
        "email/purchase",
        proxies=proxies,
        email_type=email_type,
        project_code="openai",
        domain="hotmail.com",
        quantity=1,
        variant_mode="",
    )
    if data.get("code") == 0:
        purchases = data.get("data", {}).get("purchases", [])
        if purchases:
            return purchases[0], None
        return None, "API返回数据中没有购买记录"
    return None, data.get("message", "购买邮箱失败")


def luckmail_check_email_alive(token: str, proxies: Any = None) -> tuple:
    try:
        headers = {"X-API-Key": ctx.LUCKMAIL_API_KEY, "Content-Type": "application/json"}
        url = f"{ctx.LUCKMAIL_API_URL}/email/token/{token}/alive"
        response = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        data = response.json()

        if data.get("code") == 0:
            result = data.get("data", {})
            is_alive = result.get("alive", False)
            email_addr = result.get("email_address", "未知")
            status_msg = result.get("message", "")
            mail_count = result.get("mail_count", 0)
            if is_alive:
                return True, f"邮箱活跃 ({email_addr}, 邮件数: {mail_count}, {status_msg})"
            return False, f"邮箱不活跃 ({email_addr}, {status_msg})"
        return False, data.get("message", "检测失败")
    except Exception as exc:
        return False, f"检测异常: {exc}"


def luckmail_disable_email(purchase_id: int, disabled: bool = True, proxies: Any = None) -> bool:
    try:
        headers = {"X-API-Key": ctx.LUCKMAIL_API_KEY, "Content-Type": "application/json"}
        url = f"{ctx.LUCKMAIL_API_URL}/email/purchases/{purchase_id}/disabled"
        response = requests.put(
            url,
            headers=headers,
            json={"disabled": 1 if disabled else 0},
            proxies=proxies,
            timeout=15,
        )
        return response.json().get("code") == 0
    except Exception as exc:
        print(f"[Error] 禁用邮箱失败: {exc}")
        return False


def luckmail_batch_buy_and_check(
    quantity: int = 10,
    max_workers: int | None = None,
    proxies: Any = None,
    email_type: str = "ms_imap",
    active_queue: ctx.ActiveEmailQueue | None = None,
) -> tuple:
    if max_workers is None:
        max_workers = ctx.LUCKMAIL_CHECK_WORKERS
    print(f"[*] 批量购买 {quantity} 个邮箱 (类型: {email_type})...")
    data = _luckmail_api_request(
        "POST",
        "email/purchase",
        proxies=proxies,
        email_type=email_type,
        project_code="openai",
        domain="hotmail.com",
        quantity=quantity,
        variant_mode="",
    )
    if data.get("code") != 0:
        error_msg = data.get("message", "未知错误")
        print(f"[Error] 批量购买失败: {error_msg}")
        return [], error_msg

    purchases = data.get("data", {}).get("purchases", [])
    if not purchases:
        print("[Error] 没有购买到任何邮箱")
        return [], "没有购买到任何邮箱"

    print(f"[*] 成功购买 {len(purchases)} 个邮箱，开始并行检测活跃度...")
    active_emails = []
    disabled_count = 0
    lock = threading.Lock()

    def check_single_email(purchase):
        nonlocal disabled_count
        email = purchase.get("email_address")
        token = purchase.get("token")
        purchase_id = purchase.get("id")
        if not email or not token:
            return None, None
        is_alive, _ = luckmail_check_email_alive(token, proxies)
        if is_alive:
            return {"email": email, "token": token, "id": purchase_id}, None
        disabled_ok = False
        if luckmail_disable_email(purchase_id, disabled=True, proxies=proxies):
            with lock:
                disabled_count += 1
            disabled_ok = True
        return None, {"email": email, "disabled_ok": disabled_ok}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_single_email, purchase): purchase for purchase in purchases}
        for future in concurrent.futures.as_completed(futures):
            active_result, _ = future.result()
            if active_result:
                active_emails.append(active_result)
                _push_active_email(active_queue, active_result)

    inactive_count = len(purchases) - len(active_emails)
    print(f"[*] 检测完成: ✓活跃 {len(active_emails)} 个, ✗不活跃 {inactive_count} 个(已禁用{disabled_count}个)")
    if active_emails:
        print("[*] 活跃邮箱列表:")
        for email_data in active_emails:
            print(f"    ✓ {email_data['email']}")
    return active_emails, None


def luckmail_get_purchased_emails(proxies: Any = None, page: int = 1, page_size: int = 50, user_disabled: int = 0) -> tuple:
    try:
        headers = {"X-API-Key": ctx.LUCKMAIL_API_KEY, "Content-Type": "application/json"}
        url = f"{ctx.LUCKMAIL_API_URL}/email/purchases"
        response = requests.get(
            url,
            headers=headers,
            params={"page": page, "page_size": page_size, "user_disabled": user_disabled},
            proxies=proxies,
            timeout=15,
        )
        data = response.json()
        if data.get("code") == 0:
            payload = data.get("data", {})
            return payload.get("list", []), None, payload.get("total", 0)
        return [], data.get("message", "获取已购邮箱失败"), 0
    except Exception as exc:
        return [], f"获取已购邮箱异常: {exc}", 0


def luckmail_get_all_purchased_emails(proxies: Any = None, user_disabled: int = 0) -> tuple:
    all_mails = []
    page = 1
    page_size = 50
    while True:
        mails, err, total = luckmail_get_purchased_emails(
            proxies=proxies, page=page, page_size=page_size, user_disabled=user_disabled
        )
        if err:
            return all_mails, err
        if not mails:
            break
        all_mails.extend(mails)
        if len(all_mails) >= total or len(mails) < page_size:
            break
        page += 1
    return all_mails, None


def luckmail_get_private_emails(
    proxies: Any = None,
    page: int = 1,
    page_size: int = 50,
    keyword: str | None = None,
    status: int | None = 1,
) -> tuple:
    params = {"page": page, "page_size": page_size}
    if keyword:
        params["keyword"] = keyword
    if status is not None:
        params["status"] = status
    data = _luckmail_api_request("GET", "emails", proxies=proxies, **params)
    if data.get("code") == 0:
        payload = data.get("data", {})
        return payload.get("list", []), None, payload.get("total", 0)
    return [], data.get("message", "获取我的邮箱失败"), 0


def luckmail_get_all_private_emails(
    proxies: Any = None,
    keyword: str | None = None,
    status: int | None = 1,
) -> tuple:
    all_mails = []
    page = 1
    page_size = 50
    while True:
        mails, err, total = luckmail_get_private_emails(
            proxies=proxies,
            page=page,
            page_size=page_size,
            keyword=keyword,
            status=status,
        )
        if err:
            return all_mails, err
        if not mails:
            break
        all_mails.extend(mails)
        if len(all_mails) >= total or len(mails) < page_size:
            break
        page += 1
    return all_mails, None


def luckmail_get_private_email_mails(
    email_id: int,
    proxies: Any = None,
    page: int = 1,
    page_size: int = 50,
    keyword: str | None = None,
) -> tuple[list, str | None, int]:
    params = {"page": page, "page_size": page_size}
    if keyword:
        params["keyword"] = keyword
    data = _luckmail_api_request("GET", f"emails/{email_id}/mails", proxies=proxies, **params)
    if data.get("code") == 0:
        payload = data.get("data", {})
        return payload.get("list", []) or [], None, payload.get("total", 0)
    return [], data.get("message", "获取邮箱邮件列表失败"), 0


def luckmail_get_private_email_mail_detail(email_id: int, message_id: str, proxies: Any = None) -> tuple[dict, str | None]:
    data = _luckmail_api_request("GET", f"emails/{email_id}/mails/{message_id}", proxies=proxies)
    if data.get("code") == 0:
        return data.get("data", {}) or {}, None
    return {}, data.get("message", "获取邮件详情失败")


def luckmail_collect_private_emails(
    proxies: Any = None,
    active_queue: ctx.ActiveEmailQueue | None = None,
) -> list:
    print("[*] 获取我的邮箱列表...")
    mails, err = luckmail_get_all_private_emails(proxies=proxies, status=1)
    if err:
        print(f"[Error] 获取我的邮箱失败: {err}")
        return []
    if not mails:
        print("[*] 没有可用的我的邮箱")
        return []

    active_emails = []
    for mail_item in mails:
        email = _extract_email_address(mail_item)
        email_id = mail_item.get("id")
        if not email or email_id is None:
            continue
        normalized = {"email": email, "id": email_id, "source": "private", "type": mail_item.get("type")}
        active_emails.append(normalized)
        _push_active_email(active_queue, normalized)

    print(f"[*] 我的邮箱加载完成: ✓ 可用 {len(active_emails)}/{len(mails)} 个")
    return active_emails


def luckmail_check_purchased_emails(
    proxies: Any = None,
    max_workers: int | None = None,
    active_queue: ctx.ActiveEmailQueue | None = None,
) -> list:
    if max_workers is None:
        max_workers = ctx.LUCKMAIL_CHECK_WORKERS
    print("[*] 获取已购邮箱列表...")
    mails, err = luckmail_get_all_purchased_emails(proxies=proxies, user_disabled=0)
    if err:
        print(f"[Error] 获取已购邮箱失败: {err}")
        return []
    if not mails:
        print("[*] 没有已购的非禁用邮箱")
        return []

    filtered_mails = _filter_hotmail_purchases(mails)
    skipped_count = len(mails) - len(filtered_mails)
    if skipped_count > 0:
        print(f"[*] 已过滤 {skipped_count} 个非 Hotmail 已购邮箱，仅检测 Hotmail")
    if not filtered_mails:
        print("[*] 没有可检测的 Hotmail 已购邮箱")
        return []

    print(f"[*] 获取到 {len(filtered_mails)} 个 Hotmail 已购邮箱，开始检测活跃度... (并发: {max_workers})")
    active_emails = []
    disabled_count = 0
    lock = threading.Lock()

    def check_single_email(mail):
        nonlocal disabled_count
        email = mail.get("email_address")
        token = mail.get("token")
        purchase_id = mail.get("id")
        if not email or not token:
            return None
        is_alive, _ = luckmail_check_email_alive(token, proxies)
        if is_alive:
            return {"email": email, "token": token, "id": purchase_id}
        if luckmail_disable_email(purchase_id, disabled=True, proxies=proxies):
            with lock:
                disabled_count += 1
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_single_email, mail): mail for mail in filtered_mails}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                active_emails.append(result)
                _push_active_email(active_queue, result)

    print(f"[*] 已购邮箱检测完成: ✓活跃 {len(active_emails)}/{len(filtered_mails)} 个, 已禁用 {disabled_count} 个不活跃邮箱")
    return active_emails


def luckmail_create_order(email: str, proxies: Any = None) -> tuple:
    del email
    data = _luckmail_api_request(
        "POST",
        "order/create",
        proxies=proxies,
        project_code="openai",
        email_type="ms_imap",
        domain="hotmail.com",
        specified_email="",
        variant_mode="",
    )
    if data.get("code") == 0:
        return data.get("data", {}).get("order_no"), data.get("data", {})
    return None, data.get("message", "创建订单失败")


def luckmail_get_code(order_no: str, proxies: Any = None) -> str:
    data = _luckmail_api_request("GET", f"order/{order_no}/code", proxies=proxies)
    if data.get("code") == 0:
        result = data.get("data", {})
        if result.get("status") == "success":
            return result.get("verification_code", "")
    return ""


def luckmail_get_code_by_token(token: str, proxies: Any = None) -> str:
    try:
        headers = {"X-API-Key": ctx.LUCKMAIL_API_KEY, "Content-Type": "application/json"}
        url = f"{ctx.LUCKMAIL_API_URL}/email/token/{token}/code"
        response = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        data = response.json()
        if data.get("code") == 0:
            result = data.get("data", {})
            code = result.get("code", "")
            if code:
                return code
            verification_code = result.get("verification_code", "")
            if verification_code:
                return verification_code
        return ""
    except Exception as exc:
        print(f"[Error] 通过Token获取验证码失败: {exc}")
        return ""


def luckmail_get_token_mails(token: str, proxies: Any = None) -> tuple[list, str | None]:
    try:
        headers = {"X-API-Key": ctx.LUCKMAIL_API_KEY, "Content-Type": "application/json"}
        url = f"{ctx.LUCKMAIL_API_URL}/email/token/{token}/mails"
        response = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        data = response.json()
        if data.get("code") == 0:
            result = data.get("data", {})
            return result.get("mails", []) or [], None
        return [], data.get("message", "获取邮件列表失败")
    except Exception as exc:
        return [], str(exc)


def _mail_message_id(mail_item: dict) -> str:
    return str(mail_item.get("message_id") or "").strip()


def _extract_code_from_mail_item(mail_item: dict) -> str:
    verification_code = str(mail_item.get("verification_code") or "").strip()
    if verification_code:
        return verification_code

    subject = str(mail_item.get("subject") or "")
    body = str(mail_item.get("body") or "")
    html_body = str(mail_item.get("html_body") or "")
    text = "\n".join(part for part in [subject, body, html_body] if part)
    return extract_otp_code(text)


def _snapshot_known_message_ids(token: str, proxies: Any = None) -> set[str]:
    mails, _ = luckmail_get_token_mails(token, proxies=proxies)
    return {_mail_message_id(mail_item) for mail_item in mails if _mail_message_id(mail_item)}


def _snapshot_private_email_message_ids(email_id: int, proxies: Any = None) -> set[str]:
    mails, _, _ = luckmail_get_private_email_mails(email_id, proxies=proxies, page=1, page_size=100)
    return {_mail_message_id(mail_item) for mail_item in mails if _mail_message_id(mail_item)}


def _mail_debug_summary(mail_item: dict) -> str:
    message_id = _mail_message_id(mail_item) or "-"
    received_at = str(mail_item.get("received_at") or "-")
    subject = str(mail_item.get("subject") or "").replace("\n", " ").strip()
    code = _extract_code_from_mail_item(mail_item)
    return f"id={message_id} time={received_at} code={'yes' if code else 'no'} subject={subject[:80]}"


def _print_token_mail_diagnostics(
    *,
    email: str,
    poll_index: int,
    mails: list[dict],
    mails_error: str | None,
    seen_ids: set[str],
) -> None:
    if not ctx.LUCKMAIL_MAIL_DEBUG:
        return

    print(f"\n[Debug][LuckMail] poll={poll_index} email={email} mails={len(mails)} seen={len(seen_ids)}", end="")
    if mails_error:
        print(f" error={mails_error}", end="")

    if mails:
        newest = sorted(
            mails,
            key=lambda item: (str(item.get("received_at") or ""), _mail_message_id(item)),
            reverse=True,
        )[:3]
        for index, mail_item in enumerate(newest, start=1):
            print(f"\n  [Debug][LuckMail] recent#{index} {_mail_debug_summary(mail_item)}", end="")
    else:
        print("\n  [Debug][LuckMail] no mails returned", end="")


def _select_latest_unseen_code(mails: list[dict], seen_ids: set[str] | None = None) -> tuple[str, str]:
    seen = seen_ids or set()
    ordered = sorted(
        mails,
        key=lambda item: (
            str(item.get("received_at") or ""),
            _mail_message_id(item),
        ),
        reverse=True,
    )
    for mail_item in ordered:
        message_id = _mail_message_id(mail_item)
        if message_id and message_id in seen:
            continue
        code = _extract_code_from_mail_item(mail_item)
        if code:
            return code, message_id
    return "", ""


def _prefetch_active_emails(rotator: ctx.ProxyRotator, min_pool_size: int = 10, batch_size: int = 20):
    if ctx._active_email_queue is None:
        ctx._active_email_queue = ctx.ActiveEmailQueue()

    if ctx._luckmail_own_only:
        print("\n[*] [预检测] 只加载我的邮箱...")
        proxy = rotator.next() if len(rotator) > 0 else None
        proxies = ctx.build_proxies(proxy)
        own_active = luckmail_collect_private_emails(
            proxies=proxies,
            active_queue=ctx._active_email_queue,
        )
        if own_active:
            print(f"[*] [预检测] ✓ 已从我的邮箱中添加 {len(own_active)} 个可用邮箱 | 队列: {len(ctx._active_email_queue)} 个")
        print("[*] [预检测] 我的邮箱模式：不购买新邮箱")
        print("[*] [预检测] 预检测线程退出")
        return

    if ctx._luckmail_skip_purchased:
        print("\n[*] [预检测] 跳过已购邮箱检查，直接购买新邮箱...")
    else:
        print("\n[*] [预检测] 首先检查已购邮箱...")
        proxy = rotator.next() if len(rotator) > 0 else None
        proxies = ctx.build_proxies(proxy)
        purchased_active = luckmail_check_purchased_emails(
            proxies=proxies,
            max_workers=ctx.LUCKMAIL_CHECK_WORKERS,
            active_queue=ctx._active_email_queue,
        )
        if purchased_active:
            print(f"[*] [预检测] ✓ 已从已购邮箱中添加 {len(purchased_active)} 个活跃邮箱 | 队列: {len(ctx._active_email_queue)} 个")

    if ctx._luckmail_purchased_only:
        print("[*] [预检测] 已购邮箱模式：只使用已购邮箱，不购买新邮箱")
        print("[*] [预检测] 预检测线程退出")
        return

    while True:
        try:
            current_size = len(ctx._active_email_queue)
            if current_size < min_pool_size:
                print(f"\n{'=' * 50}")
                print(f"[*] [预检测] 活跃邮箱池不足 ({current_size}/{min_pool_size})，批量购买 {batch_size} 个...")
                print(f"{'=' * 50}")
                proxy = rotator.next() if len(rotator) > 0 else None
                proxies = ctx.build_proxies(proxy)
                active_emails, error_msg = luckmail_batch_buy_and_check(
                    quantity=batch_size,
                    max_workers=ctx.LUCKMAIL_CHECK_WORKERS,
                    proxies=proxies,
                    email_type=ctx.LUCKMAIL_EMAIL_TYPE,
                    active_queue=ctx._active_email_queue,
                )
                if active_emails:
                    with ctx._prefetch_lock:
                        if ctx._prefetch_no_stock:
                            ctx._prefetch_no_stock = False
                            print("[*] [预检测] 库存恢复，继续预检测模式")
                    print(f"[*] [预检测] ✓ 已补充 {len(active_emails)} 个活跃邮箱 | 队列: {len(ctx._active_email_queue)} 个")
                elif error_msg and ("库存" in error_msg or "stock" in error_msg.lower()):
                    with ctx._prefetch_lock:
                        ctx._prefetch_no_stock = True
                    print("[*] [预检测] ✗ 无库存，自动切换回接码模式")
                    print("[*] [预检测] 预检测线程退出")
                    return
                else:
                    print("[*] [预检测] ✗ 未获取到活跃邮箱，5秒后重试...")
            time.sleep(2)
        except Exception as exc:
            print(f"\n[*] [预检测] 异常: {exc}")
            time.sleep(5)


def get_email_and_token(proxies: Any = None) -> tuple:
    if not ctx.LUCKMAIL_API_KEY:
        print("[Error] ctx.LUCKMAIL_API_KEY 未配置")
        return "", ""

    if not ctx.LUCKMAIL_AUTO_BUY:
        print("[*] LuckMail 接码模式: 创建 openai 项目订单")
        return _create_order_email(proxies=proxies)

    if ctx._active_email_queue is not None and not ctx._active_email_queue.is_empty():
        email_data = ctx._active_email_queue.pop()
        if email_data:
            email = email_data["email"]
            source = email_data.get("source", "purchased")
            print(f"[*] ✓ 使用预检测活跃邮箱: {email}")
            print(f"[*] 活跃邮箱池: {len(ctx._active_email_queue)} 个待使用")
            if source == "private":
                email_id = email_data["id"]
                return _store_luckmail_credential(
                    email,
                    email_id=email_id,
                    source=source,
                    email_data=email_data,
                    known_message_ids=_snapshot_private_email_message_ids(email_id, proxies=proxies),
                )

            token = email_data["token"]
            purchase_id = email_data["id"]
            return _store_luckmail_credential(
                email,
                token=token,
                purchase_id=purchase_id,
                source=source,
                email_data=email_data,
                known_message_ids=_snapshot_known_message_ids(token, proxies=proxies),
            )

    if ctx._luckmail_own_only:
        print("[*] 我的邮箱已用完，停止注册")
        return "", ""

    if ctx._luckmail_purchased_only:
        print("[*] 已购邮箱已用完，停止注册")
        return "", ""

    max_retries = ctx.LUCKMAIL_MAX_RETRY
    for attempt in range(1, max_retries + 1):
        print(f"[*] LuckMail 自动购买模式 (尝试 {attempt}/{max_retries}): 购买 {ctx.LUCKMAIL_EMAIL_TYPE} 邮箱...")
        purchase_data, err = luckmail_buy_email(proxies=proxies, email_type=ctx.LUCKMAIL_EMAIL_TYPE)
        if err or not purchase_data:
            print(f"[Error] 购买邮箱失败: {err}")
            if attempt == max_retries:
                print("[*] 购买多次失败，回退到接码模式")
                return _create_order_email(proxies=proxies)
            time.sleep(2)
            continue

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
        print("[*] 检测邮箱活跃度...")
        is_alive, message = luckmail_check_email_alive(token, proxies=proxies)
        print(f"[*] 检测结果: {message}")
        if not is_alive:
            print("[Warning] 邮箱不活跃，禁用该邮箱并重新购买...")
            if luckmail_disable_email(purchase_id, disabled=True, proxies=proxies):
                print(f"[*] 已禁用不活跃邮箱: {email}")
            else:
                print(f"[Warning] 禁用邮箱失败: {email}")
            if attempt < max_retries:
                time.sleep(2)
                continue
            print("[Error] 已达到最大重试次数，无法获取活跃邮箱")
            return "", ""

        print("[*] 邮箱活跃，可以使用!")
        return _store_luckmail_credential(
            email,
            token=token,
            purchase_id=purchase_id,
            email_data=purchase_data,
            known_message_ids=_snapshot_known_message_ids(token, proxies=proxies),
        )

    return "", ""


def get_oai_code(email: str, proxies: Any = None, seen_ids: set | None = None) -> str:
    creds = ctx._luckmail_credentials.get(email, {})
    if not creds:
        print(f"[Error] 未找到 {email} 的 LuckMail 凭据")
        return ""

    email_id = creds.get("email_id")
    email_token = creds.get("token")
    order_no = creds.get("order_no")
    if email_id is not None:
        print("[*] 使用我的邮箱列表获取验证码...", end="", flush=True)
        combined_seen_ids = {
            str(message_id)
            for message_id in (creds.get("known_message_ids") or set())
            if str(message_id).strip()
        }
        if seen_ids is not None:
            combined_seen_ids.update(str(message_id) for message_id in seen_ids if str(message_id).strip())
        start = time.time()
        while time.time() - start < 120:
            mails, mails_error, _ = luckmail_get_private_email_mails(email_id, proxies=proxies)
            code, message_id = _select_latest_unseen_code(mails, combined_seen_ids)
            if not code:
                ordered = sorted(
                    mails,
                    key=lambda item: (
                        str(item.get("received_at") or ""),
                        _mail_message_id(item),
                    ),
                    reverse=True,
                )
                for mail_item in ordered:
                    candidate_id = _mail_message_id(mail_item)
                    if candidate_id and candidate_id in combined_seen_ids:
                        continue
                    if not candidate_id:
                        continue
                    detail, _ = luckmail_get_private_email_mail_detail(email_id, candidate_id, proxies=proxies)
                    merged = dict(mail_item)
                    merged.update(detail)
                    code = _extract_code_from_mail_item(merged)
                    if code:
                        message_id = candidate_id
                        break
            if code:
                if message_id:
                    combined_seen_ids.add(message_id)
                    creds.setdefault("known_message_ids", set()).add(message_id)
                    if seen_ids is not None:
                        seen_ids.add(message_id)
                print(f" 抓到啦! 验证码: {code}")
                return code
            if mails_error and ctx.LUCKMAIL_MAIL_DEBUG:
                print(f"\n[Debug][LuckMail] own/email mails error for {email}: {mails_error}", end="")
            print(".", end="", flush=True)
            time.sleep(3)
        print(" 超时，未收到验证码")
        return ""

    if email_token:
        print("[*] 使用已购邮箱Token获取验证码...", end="", flush=True)
        combined_seen_ids = {
            str(message_id)
            for message_id in (creds.get("known_message_ids") or set())
            if str(message_id).strip()
        }
        if seen_ids is not None:
            combined_seen_ids.update(str(message_id) for message_id in seen_ids if str(message_id).strip())
        start = time.time()
        poll_index = 0
        while time.time() - start < 120:
            poll_index += 1
            mails, mails_error = luckmail_get_token_mails(email_token, proxies=proxies)
            _print_token_mail_diagnostics(
                email=email,
                poll_index=poll_index,
                mails=mails,
                mails_error=mails_error,
                seen_ids=combined_seen_ids,
            )
            code, message_id = _select_latest_unseen_code(mails, combined_seen_ids)
            if code:
                if message_id:
                    combined_seen_ids.add(message_id)
                    creds.setdefault("known_message_ids", set()).add(message_id)
                    if seen_ids is not None:
                        seen_ids.add(message_id)
                print(f" 抓到啦! 验证码: {code}")
                return code
            if mails_error:
                fallback_code = luckmail_get_code_by_token(email_token, proxies=proxies)
                if fallback_code:
                    print(f" 抓到啦! 验证码: {fallback_code}")
                    return fallback_code
                if ctx.LUCKMAIL_MAIL_DEBUG:
                    print(f"\n[Debug][LuckMail] token/code fallback empty for {email}", end="")
            print(".", end="", flush=True)
            time.sleep(3)
        print(" 超时，未收到验证码")
        return ""

    if order_no:
        return _poll_for_code(
            lambda **kwargs: luckmail_get_code(order_no, **kwargs),
            f"[*] 轮询获取验证码 (订单: {order_no})...",
            proxies=proxies,
        )

    print("[*] 创建验证码订单...")
    new_order_no, err = luckmail_create_order(email, proxies=proxies)
    if err or not new_order_no:
        print(f"[Error] 创建验证码订单失败: {err}")
        return ""
    ctx._luckmail_credentials[email]["order_no"] = new_order_no
    print(f"[*] 验证码订单创建成功: {new_order_no}")
    return _poll_for_code(
        lambda **kwargs: luckmail_get_code(new_order_no, **kwargs),
        f"[*] 轮询获取验证码 (订单: {new_order_no})...",
        proxies=proxies,
    )


def delete_temp_email(email: str, proxies: Any = None) -> None:
    creds = ctx._luckmail_credentials.pop(email, None)
    if creds and "purchase_id" in creds:
        purchase_id = creds["purchase_id"]
        try:
            if luckmail_disable_email(purchase_id, disabled=True, proxies=proxies):
                print(f"[*] LuckMail 邮箱 {email} 已禁用 (注册成功)")
            else:
                print(f"[Warning] LuckMail 邮箱 {email} 禁用失败")
        except Exception as exc:
            print(f"[Warning] 禁用邮箱 {email} 时出错: {exc}")
    else:
        print(f"[*] LuckMail 邮箱 {email} 本地凭据已清理")
