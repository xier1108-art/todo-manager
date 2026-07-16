# -*- coding: utf-8 -*-
"""구글 캘린더 연동 — 업무 마감일을 종일 일정으로 등록/수정/삭제한다.

클라이언트 시크릿은 이 앱에 들어있지 않다. 토큰 교환/갱신은 별도의
중계 서버(relay, Vercel)가 대신 처리하고, 이 앱은 공개 정보인
client_id와 PKCE만으로 로그인 흐름을 진행한다.
"""
import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
import truststore

# 사내망처럼 HTTPS를 검사(TLS 인터셉트)하는 네트워크에서도 요청이 실패하지 않도록,
# Windows가 이미 신뢰하는 인증서 저장소를 Python이 그대로 쓰게 만든다.
truststore.inject_into_ssl()

CLIENT_ID = "643329263654-mmjq4v1vcd5gtaj8s5r7ib9qhrmo9317.apps.googleusercontent.com"
RELAY_URL = "https://todo-manager-gcal-relay.vercel.app"
SCOPE = "https://www.googleapis.com/auth/calendar"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"


class GCalError(Exception):
    pass


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server.auth_code = qs.get("code", [None])[0]
        self.server.auth_error = qs.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "로그인이 완료됐어요. 이 창은 닫으셔도 됩니다." if self.server.auth_code else "로그인에 실패했어요."
        self.wfile.write(
            f"<html><body style='font-family:sans-serif;padding:40px'><h2>{msg}</h2></body></html>".encode("utf-8")
        )

    def log_message(self, *args):
        pass  # 콘솔 로그 억제


def _run_login_flow():
    verifier, challenge = _pkce_pair()
    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    server.auth_code = None
    server.auth_error = None
    port = server.server_address[1]
    redirect_uri = f"http://localhost:{port}"

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    webbrowser.open(AUTH_URI + "?" + urllib.parse.urlencode(params))

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=180)
    server.server_close()

    if server.auth_error:
        raise GCalError(f"로그인이 취소되거나 실패했습니다: {server.auth_error}")
    if not server.auth_code:
        raise GCalError("로그인 시간이 초과됐습니다. 다시 시도해주세요.")

    try:
        r = requests.post(
            f"{RELAY_URL}/api/exchange",
            json={"code": server.auth_code, "code_verifier": verifier, "redirect_uri": redirect_uri},
            timeout=15,
        )
    except requests.RequestException as e:
        raise GCalError(f"중계 서버에 연결하지 못했습니다: {e}")
    data = r.json()
    if r.status_code != 200 or "access_token" not in data:
        raise GCalError(f"토큰 교환에 실패했습니다: {data.get('error_description') or data.get('error') or r.text}")
    return data


def _load_token(token_file):
    if not token_file.exists():
        return None
    try:
        return json.loads(token_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_token(token_file, data):
    token_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _refresh_access_token(refresh_token):
    try:
        r = requests.post(f"{RELAY_URL}/api/refresh", json={"refresh_token": refresh_token}, timeout=15)
    except requests.RequestException as e:
        raise GCalError(f"중계 서버에 연결하지 못했습니다: {e}")
    data = r.json()
    if r.status_code != 200 or "access_token" not in data:
        raise GCalError(f"토큰 갱신에 실패했습니다: {data.get('error_description') or data.get('error') or r.text}")
    return data


def _get_access_token(token_file):
    tok = _load_token(token_file)
    if not tok:
        raise GCalError("구글 계정에 연결되어 있지 않습니다.")
    if time.time() < tok.get("expires_at", 0) - 60:
        return tok["access_token"]
    fresh = _refresh_access_token(tok["refresh_token"])
    tok["access_token"] = fresh["access_token"]
    tok["expires_at"] = time.time() + fresh.get("expires_in", 3600)
    _save_token(token_file, tok)
    return tok["access_token"]


def is_connected(token_file):
    return token_file.exists()


def disconnect(token_file):
    if token_file.exists():
        token_file.unlink()


def connect(token_file):
    """브라우저로 로그인 플로우를 진행하고, 성공하면 token.json에 저장한다."""
    data = _run_login_flow()
    tok = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_at": time.time() + data.get("expires_in", 3600),
    }
    if not tok["refresh_token"]:
        # 재로그인이라 리프레시 토큰이 다시 내려오지 않은 경우 기존 걸 유지
        old = _load_token(token_file)
        if old and old.get("refresh_token"):
            tok["refresh_token"] = old["refresh_token"]
        else:
            raise GCalError("리프레시 토큰을 받지 못했습니다. 다시 시도해주세요.")
    _save_token(token_file, tok)


def _api_get(token_file, path, params=None):
    token = _get_access_token(token_file)
    try:
        r = requests.get(
            f"{CALENDAR_API}{path}", headers={"Authorization": f"Bearer {token}"}, params=params, timeout=15
        )
    except requests.RequestException as e:
        raise GCalError(f"구글 캘린더에 연결하지 못했습니다: {e}")
    if r.status_code >= 400:
        raise GCalError(f"구글 캘린더 API 오류: {r.text}")
    return r.json()


def _api_write(token_file, method, path, body=None):
    token = _get_access_token(token_file)
    try:
        r = requests.request(
            method, f"{CALENDAR_API}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body, timeout=15,
        )
    except requests.RequestException as e:
        raise GCalError(f"구글 캘린더에 연결하지 못했습니다: {e}")
    if r.status_code >= 400 and r.status_code not in (404, 410):
        raise GCalError(f"구글 캘린더 API 오류: {r.text}")
    return r.json() if r.text else {}


def list_calendars(token_file):
    data = _api_get(token_file, "/users/me/calendarList")
    cals = []
    for c in data.get("items", []):
        if c.get("accessRole") not in ("owner", "writer"):
            continue
        cals.append({
            "id": c["id"],
            "name": c.get("summaryOverride") or c.get("summary", c["id"]),
            "primary": bool(c.get("primary")),
        })
    cals.sort(key=lambda c: (not c["primary"], c["name"]))
    return cals


def upsert_event(token_file, calendar_id, event_id, title, date_str):
    """종일 이벤트를 생성하거나(기존 event_id 없음) 갱신한다. 생성/갱신된 이벤트 id를 반환."""
    end_date = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    body = {"summary": title, "start": {"date": date_str}, "end": {"date": end_date}}
    cal = urllib.parse.quote(calendar_id, safe="")
    if event_id:
        ev = _api_write(token_file, "PUT", f"/calendars/{cal}/events/{event_id}", body)
        if ev.get("id"):
            return ev["id"]
        # 사용자가 캘린더에서 직접 지운 경우(404) 등 -> 새로 생성
    ev = _api_write(token_file, "POST", f"/calendars/{cal}/events", body)
    return ev["id"]


def delete_event(token_file, calendar_id, event_id):
    if not event_id:
        return
    cal = urllib.parse.quote(calendar_id, safe="")
    _api_write(token_file, "DELETE", f"/calendars/{cal}/events/{event_id}")
