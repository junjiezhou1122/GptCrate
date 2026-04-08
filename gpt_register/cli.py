import argparse
import json
import os
import random
import sys
import threading
import time
from datetime import datetime
from typing import Optional

import cpa2sub

from . import context as ctx
from . import mail, oauth, register


_ANSI_RESET = "\033[0m"
_ANSI = {
    "dim": "\033[2m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    "bright_black": "\033[90m",
}


def _supports_color() -> bool:
    return bool(
        hasattr(sys.stdout, "isatty")
        and sys.stdout.isatty()
        and os.getenv("TERM", "").lower() != "dumb"
        and not os.getenv("NO_COLOR")
    )


def _style(text: str, *styles: str) -> str:
    if not _supports_color() or not text:
        return text
    prefix = "".join(_ANSI.get(style, "") for style in styles if style in _ANSI)
    return f"{prefix}{text}{_ANSI_RESET}" if prefix else text


def _kv(label: str, value: str, *, label_style: str = "bright_black", value_style: str = "white") -> str:
    return f"{_style(label, label_style)} {_style(value, value_style)}"


def _panel(title: str, rows: list[str], tone: str = "cyan") -> str:
    width = max(len(title) + 4, *(len(row) for row in rows), 44)
    top = f"┌{'─' * (width - 2)}┐"
    mid = f"├{'─' * (width - 2)}┤"
    bottom = f"└{'─' * (width - 2)}┘"
    header = f"│ {title.ljust(width - 4)} │"
    body = [f"│ {row.ljust(width - 4)} │" for row in rows]
    lines = [top, header, mid, *body, bottom]
    return "\n".join(_style(line, tone) for line in lines)


def _status_line() -> str:
    if not ctx._reg_stats:
        return ""
    stats = ctx._reg_stats.get_stats()
    elapsed = stats["elapsed_time"]
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    seconds = int(elapsed % 60)
    parts = [
        _style("● 实时状态", "bold", "cyan"),
        _kv("运行", f"{hours:02d}:{minutes:02d}:{seconds:02d}", value_style="white"),
        _kv("尝试", str(stats["total_attempts"]), value_style="blue"),
        _kv("成功", str(stats["success_count"]), value_style="green"),
        _kv("失败", str(stats["fail_count"]), value_style="red"),
        _kv("总率", f"{stats['overall_success_rate']:.1f}%", value_style="yellow"),
        _kv("近10次", f"{stats['recent_success_rate']:.1f}%", value_style="magenta"),
        _kv("速度", f"{stats['speed_per_hour']:.1f}/h", value_style="cyan"),
    ]
    return "  ".join(parts)


def _print_final_stats() -> None:
    if not ctx._reg_stats:
        return
    stats = ctx._reg_stats.get_stats()
    elapsed = stats["elapsed_time"]
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    seconds = int(elapsed % 60)
    rows = [
        _kv("运行时间", f"{hours:02d}:{minutes:02d}:{seconds:02d}", value_style="white"),
        _kv("总尝试数", str(stats["total_attempts"]), value_style="blue"),
        _kv("成功", str(stats["success_count"]), value_style="green")
        + "    "
        + _kv("失败", str(stats["fail_count"]), value_style="red"),
        _kv("总体成功率", f"{stats['overall_success_rate']:.1f}%", value_style="yellow"),
        _kv("最近10次成功率", f"{stats['recent_success_rate']:.1f}%", value_style="magenta"),
        _kv("速度", f"{stats['speed_per_hour']:.1f} 个/小时", value_style="cyan"),
    ]
    fail_reason_items = [f"{reason}: {count}" for reason, count in stats["fail_reasons"].items() if count > 0]
    if fail_reason_items:
        rows.append(_style("失败原因", "bright_black") + " " + _style(" / ".join(fail_reason_items), "red"))
    _safe_print(_panel("注册统计面板", rows, tone="magenta"))


def _safe_print(message: str = "") -> None:
    print(message, flush=True)


def _disable_email_on_failure(email: str, tag: str = "") -> None:
    """注册失败时禁用邮箱"""
    creds = ctx._luckmail_credentials.get(email)
    if creds and "purchase_id" in creds:
        purchase_id = creds["purchase_id"]
        try:
            if mail.luckmail_disable_email(purchase_id, disabled=True):
                _safe_print(f"{tag} [*] 注册失败，已禁用邮箱: {email}")
            else:
                _safe_print(f"{tag} [Warning] 禁用邮箱失败: {email}")
        except Exception as e:
            _safe_print(f"{tag} [Warning] 禁用邮箱时出错: {email}, {e}")
    else:
        # 如果本地没有凭据，尝试从已购邮箱列表中查找
        try:
            purchased_mails, err = mail.luckmail_get_all_purchased_emails(user_disabled=0)
            if not err and purchased_mails:
                for purchase in purchased_mails:
                    if purchase.get("email_address") == email:
                        purchase_id = purchase.get("id")
                        if purchase_id:
                            if mail.luckmail_disable_email(purchase_id, disabled=True):
                                _safe_print(f"{tag} [*] 注册失败，已禁用邮箱: {email}")
                            else:
                                _safe_print(f"{tag} [Warning] 禁用邮箱失败: {email}")
                        break
        except Exception as e:
            _safe_print(f"{tag} [Warning] 查找并禁用邮箱时出错: {email}, {e}")

def _save_result(token_json: str, password: str, proxy_str: Optional[str]) -> None:
    """线程安全地保存注册结果"""
    try:
        t_data = json.loads(token_json)
        fname_email = t_data.get("email", "unknown").replace("@", "_")
        account_email = t_data.get("email", "")
    except Exception:
        t_data = {}
        fname_email = "unknown"
        account_email = ""

    timestamp = int(time.time())
    file_name = f"token_{fname_email}_{timestamp}.json"
    sub_file_name = f"sub_{fname_email}_{timestamp}.json"
    if ctx.TOKEN_OUTPUT_DIR:
        os.makedirs(ctx.TOKEN_OUTPUT_DIR, exist_ok=True)
        file_name = os.path.join(ctx.TOKEN_OUTPUT_DIR, file_name)
        sub_file_name = os.path.join(ctx.TOKEN_OUTPUT_DIR, sub_file_name)

    with ctx._file_write_lock:
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(token_json)

    _safe_print(f"[*] 成功! Token 已保存至: {file_name}")

    if t_data:
        try:
            sub_account = cpa2sub.convert_current_token_to_sub(t_data, index=1)
            sub_export = cpa2sub.build_export([sub_account])
            with ctx._file_write_lock:
                with open(sub_file_name, "w", encoding="utf-8") as sf:
                    json.dump(sub_export, sf, ensure_ascii=False, indent=2)
            _safe_print(f"[*] Sub 格式已保存至: {sub_file_name}")
        except Exception as exc:
            _safe_print(f"[Warning] 保存 Sub 格式失败: {exc}")

    if os.path.isdir(ctx.CLI_PROXY_AUTHS_DIR) and account_email:
        dest = os.path.join(ctx.CLI_PROXY_AUTHS_DIR, f"codex-{account_email}.json")
        with ctx._file_write_lock:
            with open(dest, "w", encoding="utf-8") as df:
                df.write(token_json)
        _safe_print(f"[*] Token 已拷贝至: {dest}")
        if os.path.exists(file_name):
            os.remove(file_name)
            _safe_print(f"[*] 本地 token 文件已删除: {file_name}")

    if account_email and password:
        accounts_file = os.path.join(ctx.TOKEN_OUTPUT_DIR, "accounts.txt") if ctx.TOKEN_OUTPUT_DIR else "./tokens/accounts.txt"
        with ctx._file_write_lock:
            os.makedirs(os.path.dirname(accounts_file), exist_ok=True)
            with open(accounts_file, "a", encoding="utf-8") as af:
                af.write(f"{account_email}----{password}\n")
        _safe_print(f"[*] 账号密码已追加至: {accounts_file}")

    if account_email:
        mail.delete_temp_email(account_email, proxies=ctx.build_proxies(proxy_str))

def _print_with_stats_clear(message: str, tag: str = ""):
    """统一日志输出。"""
    styled_message = message
    lowered = message.lower()
    if "[error]" in lowered or "失败" in message:
        styled_message = _style(message, "red")
    elif "成功" in message or "抓到啦" in message:
        styled_message = _style(message, "green")
    elif "warning" in lowered or "等待" in message or "休息" in message:
        styled_message = _style(message, "yellow")
    elif "开始注册" in message or "代理:" in message:
        styled_message = _style(message, "cyan")

    if tag:
        _safe_print(f"{_style(tag, 'bright_black')} {styled_message}")
    else:
        _safe_print(styled_message)


def _print_status_snapshot(force: bool = False) -> None:
    if not ctx._reg_stats:
        return
    status_line = _status_line()
    if not force and status_line == ctx._stats_last_line:
        return
    ctx._stats_last_line = status_line
    _safe_print(status_line)


def _resolve_mode_label() -> str:
    if ctx.EMAIL_MODE == "file":
        return f"文件读取 ({ctx.ACCOUNTS_FILE}, 剩余 {len(ctx._email_queue)} 个)"
    if ctx.EMAIL_MODE == "local_outlook":
        return f"本地 Outlook 导入 ({ctx.ACCOUNTS_FILE}, 剩余 {len(ctx._email_queue)} 个)"
    if ctx.EMAIL_MODE == "cf":
        return "Cloudflare Worker (自有域名)"
    if ctx.EMAIL_MODE == "luckmail":
        if ctx.LUCKMAIL_OWN_ONLY:
            return "LuckMail API (我的邮箱)"
        if ctx.LUCKMAIL_PURCHASED_ONLY:
            return "LuckMail API (已购邮箱)"
        return "LuckMail API (hotmail邮箱)"
    return "Hotmail007 API (微软邮箱)"


def _print_runtime_summary(
    rotator: ctx.ProxyRotator,
    effective_single_proxy: Optional[str],
    thread_count: int,
    batch_count: Optional[int],
) -> None:
    rows = [
        _kv("邮箱模式", _resolve_mode_label(), value_style="cyan"),
    ]
    if len(rotator) > 0:
        rows.append(_kv("代理模式", f"文件轮换 ({len(rotator)} 个代理)", value_style="blue"))
    elif effective_single_proxy:
        rows.append(_kv("代理模式", f"单代理 ({effective_single_proxy})", value_style="blue"))
    else:
        rows.append(_kv("代理模式", "直连 (未配置代理)", value_style="blue"))
    if batch_count:
        rows.append(_kv("批量数量", str(batch_count), value_style="yellow"))
    rows.append(_kv("并发线程", str(thread_count), value_style="yellow"))
    if ctx.EMAIL_MODE == "hotmail007":
        rows.append(_kv("API 地址", ctx.HOTMAIL007_API_URL, value_style="white"))
        rows.append(_kv("邮箱类型", ctx.HOTMAIL007_MAIL_TYPE, value_style="magenta"))
        rows.append(_kv("收信模式", ctx.HOTMAIL007_MAIL_MODE.upper(), value_style="magenta"))
        check_proxy_str = effective_single_proxy or (rotator.next() if len(rotator) > 0 else None)
        proxies_check = ctx.build_proxies(check_proxy_str)
        bal, bal_err = mail.hotmail007_get_balance(proxies=proxies_check)
        if bal is not None:
            rows.append(_kv("账户余额", str(bal), value_style="green"))
        else:
            rows.append(_kv("账户余额", f"查询失败 ({bal_err})", value_style="red"))
        stk, stk_err = mail.hotmail007_get_stock(proxies=proxies_check)
        if stk is not None:
            rows.append(_kv("当前库存", str(stk), value_style="green"))
        else:
            rows.append(_kv("当前库存", f"查询失败 ({stk_err})", value_style="red"))
    elif ctx.EMAIL_MODE == "local_outlook":
        rows.append(_kv("收信模式", ctx.LOCAL_OUTLOOK_MAIL_MODE.upper(), value_style="magenta"))
        rows.append(_kv("坏号记录", ctx.LOCAL_OUTLOOK_BAD_FILE, value_style="white"))
    elif ctx.EMAIL_MODE == "cf":
        rows.append(_kv("Worker 域名", ctx.MAIL_WORKER_BASE or "-", value_style="white"))
        rows.append(_kv("收信域名", ctx.MAIL_DOMAIN or "-", value_style="magenta"))
    _safe_print("")
    _safe_print(_panel("OpenAI Auto Registrar", rows))
    _safe_print("")


def _prepare_file_email_queue() -> None:
    if ctx.EMAIL_MODE not in {"file", "local_outlook"}:
        return
    if ctx.EMAIL_MODE == "local_outlook":
        ctx._email_queue = ctx.LocalOutlookAccountQueue(ctx.ACCOUNTS_FILE)
    else:
        ctx._email_queue = ctx.EmailQueue(ctx.ACCOUNTS_FILE)
    if len(ctx._email_queue) == 0:
        if ctx.EMAIL_MODE == "local_outlook":
            print(f"[Error] 本地 Outlook 账号文件 {ctx.ACCOUNTS_FILE} 为空或不存在，请按 邮箱----密码----client_id----refresh_token 填写")
        else:
            print(f"[Error] 邮箱文件 {ctx.ACCOUNTS_FILE} 为空或不存在，请先填入邮箱地址（一行一个）")
        raise SystemExit(0)
    if ctx.EMAIL_MODE == "local_outlook":
        print(f"[*] 从 {ctx.ACCOUNTS_FILE} 加载了 {len(ctx._email_queue)} 个本地 Outlook 账号")
    else:
        print(f"[*] 从 {ctx.ACCOUNTS_FILE} 加载了 {len(ctx._email_queue)} 个邮箱")


def _apply_cli_overrides(args: argparse.Namespace) -> None:
    if args.email_mode:
        ctx.EMAIL_MODE = args.email_mode.strip().lower()
    if args.accounts_file:
        ctx.ACCOUNTS_FILE = args.accounts_file.strip()
    _prepare_file_email_queue()
    if args.hotmail007_key:
        ctx.HOTMAIL007_API_KEY = args.hotmail007_key.strip()
    if args.hotmail007_type:
        ctx.HOTMAIL007_MAIL_TYPE = args.hotmail007_type.strip()
    if args.hotmail007_mail_mode:
        ctx.HOTMAIL007_MAIL_MODE = args.hotmail007_mail_mode.strip().lower()
    if args.local_outlook_mail_mode:
        ctx.LOCAL_OUTLOOK_MAIL_MODE = args.local_outlook_mail_mode.strip().lower()
    if args.luckmail_key:
        ctx.LUCKMAIL_API_KEY = args.luckmail_key.strip()
    if args.luckmail_auto_buy:
        ctx.LUCKMAIL_AUTO_BUY = True
    if args.luckmail_max_retry is not None and args.luckmail_max_retry > 0:
        ctx.LUCKMAIL_MAX_RETRY = args.luckmail_max_retry


def _resolve_thread_count(cli_thread_count: int) -> int:
    thread_count = cli_thread_count
    if ctx.BATCH_THREADS and thread_count == 1:
        try:
            thread_count = int(ctx.BATCH_THREADS)
        except ValueError:
            pass
    return max(1, thread_count)


def _resolve_batch_count(cli_batch_count: Optional[int]) -> Optional[int]:
    batch_count = cli_batch_count
    if batch_count is None and ctx.BATCH_COUNT:
        try:
            batch_count = int(ctx.BATCH_COUNT)
        except ValueError:
            pass
    return batch_count


def _apply_check_mode_batch_target(
    *,
    enabled: bool,
    rotator: ctx.ProxyRotator,
    effective_single_proxy: Optional[str],
    batch_count: Optional[int],
) -> Optional[int]:
    if not enabled:
        return batch_count
    check_proxy = effective_single_proxy or (rotator.next() if len(rotator) > 0 else None)
    stats = oauth.check_codex_tokens(proxies=ctx.build_proxies(check_proxy))
    valid_count = stats.get("valid", 0)
    if valid_count >= ctx.AUTO_REGISTER_THRESHOLD:
        print(f"[*] 当前可用 token {valid_count} 个，已达到阈值 {ctx.AUTO_REGISTER_THRESHOLD}，不执行自动注册")
        raise SystemExit(0)
    need_count = ctx.AUTO_REGISTER_THRESHOLD - valid_count
    print(f"[*] 当前可用 token {valid_count} 个，低于阈值 {ctx.AUTO_REGISTER_THRESHOLD}，开始自动注册，目标补足 {need_count} 个")
    return need_count


def _start_luckmail_prefetch(rotator: ctx.ProxyRotator) -> Optional[threading.Thread]:
    if ctx.EMAIL_MODE != "luckmail" or not ctx.LUCKMAIL_AUTO_BUY:
        return None

    ctx._luckmail_purchased_only = ctx.LUCKMAIL_PURCHASED_ONLY
    ctx._luckmail_skip_purchased = ctx.LUCKMAIL_SKIP_PURCHASED
    ctx._luckmail_own_only = ctx.LUCKMAIL_OWN_ONLY

    if ctx._luckmail_own_only:
        print("[*] 我的邮箱模式：只使用自己导入到 LuckMail 的邮箱")
    elif ctx._luckmail_purchased_only:
        print("[*] 已购邮箱模式：只使用已购邮箱，不购买新邮箱")
    elif ctx._luckmail_skip_purchased:
        print("[*] 预检测模式：跳过已购邮箱，直接购买新邮箱")
    print("[*] 启动预检测后台线程，维护活跃邮箱池...")
    if ctx._active_email_queue is None:
        ctx._active_email_queue = ctx.ActiveEmailQueue()
    prefetch_thread = threading.Thread(
        target=mail._prefetch_active_emails,
        args=(rotator, 10, 20),
        daemon=True,
    )
    prefetch_thread.start()
    if ctx._luckmail_own_only:
        print("[*] 我的邮箱模式：等待首轮自有邮箱加载完成...")
        prefetch_thread.join()
    elif ctx._luckmail_purchased_only:
        print("[*] 已购邮箱模式：等待首轮 Hotmail 检测完成...")
        prefetch_thread.join()
    else:
        print("[*] 等待预检测线程准备活跃邮箱...")
        wait_count = 0
        max_wait = 30
        while len(ctx._active_email_queue) < 3 and wait_count < max_wait:
            time.sleep(1)
            wait_count += 1
    print(f"[*] 当前活跃邮箱池: {len(ctx._active_email_queue)} 个")
    return prefetch_thread


def _start_stats_thread(stop_event: threading.Event) -> threading.Thread:
    ctx._stats_last_line = ""

    def _stats_display_thread():
        # 不再周期性刷屏；状态仅在关键节点主动打印。
        while not stop_event.is_set():
            time.sleep(1)

    stats_thread = threading.Thread(target=_stats_display_thread, daemon=True)
    stats_thread.start()
    return stats_thread


def _spawn_worker_threads(
    *,
    worker_count: int,
    rotator: ctx.ProxyRotator,
    single_proxy: Optional[str],
    sleep_min: int,
    sleep_max: int,
    count_target: Optional[int],
    remaining: Optional[list],
    stop_event: threading.Event,
) -> list[threading.Thread]:
    threads = []
    for tid in range(1, worker_count + 1):
        thread = threading.Thread(
            target=_worker,
            args=(tid, rotator, single_proxy, sleep_min, sleep_max, count_target, remaining, stop_event),
            daemon=True,
        )
        threads.append(thread)
        thread.start()
        time.sleep(1)
    return threads


def _run_batch_mode(
    *,
    batch_count: int,
    thread_count: int,
    rotator: ctx.ProxyRotator,
    effective_single_proxy: Optional[str],
    sleep_min: int,
    sleep_max: int,
    stop_event: threading.Event,
) -> None:
    remaining = [batch_count]
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
        threads = _spawn_worker_threads(
            worker_count=actual_threads,
            rotator=rotator,
            single_proxy=effective_single_proxy,
            sleep_min=sleep_min,
            sleep_max=sleep_max,
            count_target=batch_count,
            remaining=remaining,
            stop_event=stop_event,
        )
        try:
            for thread in threads:
                thread.join()
        except KeyboardInterrupt:
            print("\n[*] 收到中断信号，正在停止所有线程...")
            stop_event.set()
            for thread in threads:
                thread.join(timeout=5)

    print(f"\n[*] 批量注册完毕! 共成功: {ctx._success_counter} / 目标: {batch_count}")
    _print_final_stats()


def _run_loop_mode(
    *,
    thread_count: int,
    rotator: ctx.ProxyRotator,
    effective_single_proxy: Optional[str],
    sleep_min: int,
    sleep_max: int,
    stop_event: threading.Event,
) -> None:
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
        return

    print(f"[*] 启动 {thread_count} 个并发线程 (循环模式)...")
    threads = _spawn_worker_threads(
        worker_count=thread_count,
        rotator=rotator,
        single_proxy=effective_single_proxy,
        sleep_min=sleep_min,
        sleep_max=sleep_max,
        count_target=None,
        remaining=None,
        stop_event=stop_event,
    )
    try:
        while any(thread.is_alive() for thread in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] 收到中断信号，正在停止所有线程...")
        stop_event.set()
        for thread in threads:
            thread.join(timeout=5)

def _worker(
    worker_id: int,
    rotator: ctx.ProxyRotator,
    single_proxy: Optional[str],
    sleep_min: int,
    sleep_max: int,
    count_target: Optional[int],
    remaining: Optional[list],
    stop_event: threading.Event,
) -> int:
    """单个注册工作线程，返回本线程成功注册数"""
    local_success = 0
    local_round = 0

    while not stop_event.is_set():
        if ctx.EMAIL_MODE in {"file", "local_outlook"} and ctx._email_queue is not None and len(ctx._email_queue) == 0:
            _print_with_stats_clear(f"[T{worker_id}] 邮箱队列已用完，停止线程")
            break

        if remaining is not None:
            with ctx._success_counter_lock:
                if remaining[0] <= 0:
                    break
                remaining[0] -= 1

        local_round += 1
        proxy_str = rotator.next() if len(rotator) > 0 else single_proxy
        tag = f"[T{worker_id}#{local_round}]"

        _print_with_stats_clear(f"[{datetime.now().strftime('%H:%M:%S')}] 开始注册 (代理: {proxy_str or '直连'})", "")

        email_used = None
        fail_reason = None
        try:
            # 记录尝试
            if ctx._reg_stats:
                ctx._reg_stats.add_attempt()

            result = register.run(proxy_str)
            token_json = result[0] if result else None
            password = result[1] if result else None
            email_used = result[2] if len(result) > 2 else None
            fail_reason = result[3] if len(result) > 3 else "other_error"

            if token_json == "retry_403":
                _print_with_stats_clear("检测到 403，等待10秒后重试...", tag)
                if ctx._reg_stats:
                    ctx._reg_stats.add_failure("403_forbidden")
                    _print_status_snapshot(force=True)
                if remaining is not None:
                    with ctx._success_counter_lock:
                        remaining[0] += 1
                time.sleep(10)
                continue

            if token_json:
                _save_result(token_json, password, proxy_str)
                local_success += 1
                with ctx._success_counter_lock:
                    ctx._success_counter += 1
                if ctx._reg_stats:
                    ctx._reg_stats.add_success()
                    _print_status_snapshot(force=True)
                _print_with_stats_clear(f"注册成功! (本线程累计: {local_success})", tag)
            else:
                _print_with_stats_clear("本次注册失败", tag)
                if ctx._reg_stats:
                    ctx._reg_stats.add_failure(fail_reason or "other_error")
                    _print_status_snapshot(force=True)
                # 注册失败时禁用邮箱
                if ctx.EMAIL_MODE == "luckmail" and email_used:
                    _disable_email_on_failure(email_used, tag)
                if ctx.EMAIL_MODE in {"file", "local_outlook"} and ctx._email_queue is not None and len(ctx._email_queue) == 0:
                    _print_with_stats_clear("邮箱队列已用完，停止线程", tag)
                    break

        except Exception as e:
            _print_with_stats_clear(f"[Error] 未捕获异常: {e}", tag)
            if ctx._reg_stats:
                ctx._reg_stats.add_failure("other_error")
                _print_status_snapshot(force=True)
            # 异常时也尝试禁用邮箱
            if ctx.EMAIL_MODE == "luckmail" and email_used:
                _disable_email_on_failure(email_used, tag)

        if count_target == 1 and remaining is None:
            break

        if remaining is not None:
            with ctx._success_counter_lock:
                if remaining[0] <= 0:
                    break

        if not stop_event.is_set():
            wait_time = random.randint(sleep_min, sleep_max)
            _print_with_stats_clear(f"休息 {wait_time} 秒...", tag)
            for _ in range(wait_time):
                if stop_event.is_set():
                    break
                time.sleep(1)

    return local_success

def main() -> None:
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
        "--email-mode", default=None, choices=["cf", "hotmail007", "file", "luckmail", "local_outlook"],
        help="邮箱模式: file=从accounts.txt读取邮箱, local_outlook=从accounts.txt读取 Outlook 凭据, cf=Cloudflare自有域名, hotmail007=API拉取微软邮箱, luckmail=LuckMail (默认读.env ctx.EMAIL_MODE)"
    )
    parser.add_argument(
        "--accounts-file", default=None,
        help="账号文件路径；file 模式每行一个邮箱，local_outlook 模式每行: 邮箱----密码----client_id----refresh_token (默认 accounts.txt)"
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
    parser.add_argument(
        "--local-outlook-mail-mode", default=None, choices=["graph", "imap"],
        help="本地 Outlook 收信模式: graph=Microsoft Graph API, imap=IMAP协议 (默认读 .env LOCAL_OUTLOOK_MAIL_MODE)"
    )
    parser.add_argument("--luckmail-key", default=None, help="LuckMail API Key (覆盖.env)")
    parser.add_argument("--luckmail-auto-buy", action="store_true", help="LuckMail 自动购买邮箱")
    parser.add_argument("--luckmail-max-retry", type=int, default=None, help="LuckMail 购买邮箱时的最大重试次数 (默认3)")
    args = parser.parse_args()

    try:
        _apply_cli_overrides(args)
    except SystemExit:
        return

    proxy_file_path = args.proxy_file or ctx.PROXY_FILE
    rotator = ctx.ProxyRotator(ctx._load_proxies(proxy_file_path))
    effective_single_proxy = args.proxy or ctx.SINGLE_PROXY or None
    thread_count = _resolve_thread_count(args.threads)
    batch_count = _resolve_batch_count(args.count)
    try:
        batch_count = _apply_check_mode_batch_target(
            enabled=args.check,
            rotator=rotator,
            effective_single_proxy=effective_single_proxy,
            batch_count=batch_count,
        )
    except SystemExit:
        return
    sleep_min = max(1, args.sleep_min)
    sleep_max = max(sleep_min, args.sleep_max)

    _print_runtime_summary(rotator, effective_single_proxy, thread_count, batch_count)
    _start_luckmail_prefetch(rotator)
    if ctx.EMAIL_MODE == "luckmail" and (ctx._luckmail_purchased_only or ctx._luckmail_own_only):
        if ctx._active_email_queue is None or ctx._active_email_queue.is_empty():
            if ctx._luckmail_own_only:
                print("[*] 我的邮箱模式未读取到可用邮箱，停止启动注册线程")
            else:
                print("[*] 已购邮箱模式未筛出可用的 Hotmail 活跃邮箱，停止启动注册线程")
            return

    if ctx.EMAIL_MODE in {"file", "local_outlook"} and ctx._email_queue is not None and not batch_count:
        batch_count = len(ctx._email_queue)
        mode_name = "local_outlook" if ctx.EMAIL_MODE == "local_outlook" else "file"
        print(f"[*] {mode_name} 模式自动设置批量数量: {batch_count}")

    if args.once and not batch_count:
        batch_count = 1

    # 初始化注册统计
    ctx._reg_stats = ctx.RegistrationStats()
    ctx._success_counter = 0
    stop_event = threading.Event()
    stats_thread = _start_stats_thread(stop_event)
    _print_status_snapshot(force=True)

    try:
        if batch_count and batch_count > 0:
            _run_batch_mode(
                batch_count=batch_count,
                thread_count=thread_count,
                rotator=rotator,
                effective_single_proxy=effective_single_proxy,
                sleep_min=sleep_min,
                sleep_max=sleep_max,
                stop_event=stop_event,
            )
        else:
            _run_loop_mode(
                thread_count=thread_count,
                rotator=rotator,
                effective_single_proxy=effective_single_proxy,
                sleep_min=sleep_min,
                sleep_max=sleep_max,
                stop_event=stop_event,
            )
    finally:
        stop_event.set()
        stats_thread.join(timeout=2)
