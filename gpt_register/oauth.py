import base64
import hashlib
import json
import os
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

from curl_cffi import requests

from . import context as ctx


AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_REDIRECT_URI = "http://localhost:1455/auth/callback"
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
        if not ctx._ssl_verify():
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
                    verify=ctx._ssl_verify(),
                    timeout=timeout,
                )
            return session.post(
                url,
                headers=headers,
                data=data,
                proxies=proxies,
                verify=ctx._ssl_verify(),
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
    expired_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0)))
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



def _refresh_token(refresh_tok: str, proxies: Any = None) -> Dict[str, Any]:
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
            verify=ctx._ssl_verify(),
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
            verify=ctx._ssl_verify(),
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
    if not os.path.isdir(ctx.CLI_PROXY_AUTHS_DIR):
        print(f"[Error] 目录不存在: {ctx.CLI_PROXY_AUTHS_DIR}")
        return {"total": 0, "valid": 0, "refreshed": 0, "deleted": 0}

    files = sorted(
        f for f in os.listdir(ctx.CLI_PROXY_AUTHS_DIR)
        if f.startswith("codex-") and f.endswith(".json")
    )
    if not files:
        print("[*] 没有找到 codex token 文件")
        return {"total": 0, "valid": 0, "refreshed": 0, "deleted": 0}

    print(f"[*] 共发现 {len(files)} 个 codex token，开始检测...\n")
    valid_count = 0
    refreshed_count = 0
    deleted_count = 0

    for i, fname in enumerate(files, 1):
        fpath = os.path.join(ctx.CLI_PROXY_AUTHS_DIR, fname)
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
                    print(" 刷新失败(账号无效)，已删除")
                    deleted_count += 1
                    continue
                print(f" 刷新失败: {err}")
                continue

        test = _test_token(access_token, account_id=account_id, proxies=proxies)
        if test["valid"]:
            print(f"  [{i}/{len(files)}] {email} - 状态正常 ✓")
            valid_count += 1
            continue

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
                print(" 刷新失败，已删除")
                deleted_count += 1
        else:
            print(f"  [{i}/{len(files)}] {email} - {reason}")

    print(f"\n[*] 检测完毕: 有效 {valid_count} / 刷新 {refreshed_count} / 删除 {deleted_count} / 共 {len(files)}")
    return {"total": len(files), "valid": valid_count, "refreshed": refreshed_count, "deleted": deleted_count}
