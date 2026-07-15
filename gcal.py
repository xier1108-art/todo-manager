# -*- coding: utf-8 -*-
"""구글 캘린더 연동 — 업무 마감일을 종일 일정으로 등록/수정/삭제한다."""
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GCalError(Exception):
    pass


def _load_credentials(credentials_file, token_file):
    creds = None
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except (ValueError, OSError):
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_file.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception:
            pass  # 리프레시 실패 → 재로그인으로 폴백

    if not credentials_file.exists():
        raise GCalError(
            "credentials.json이 없습니다. Google Cloud Console에서 OAuth 클라이언트를 "
            "만들어 credentials.json으로 저장한 뒤 프로그램 폴더에 넣어주세요."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
    creds = flow.run_local_server(port=0)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def get_service(credentials_file, token_file):
    creds = _load_credentials(credentials_file, token_file)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def is_connected(token_file):
    return token_file.exists()


def disconnect(token_file):
    if token_file.exists():
        token_file.unlink()


def list_calendars(credentials_file, token_file):
    service = get_service(credentials_file, token_file)
    try:
        result = service.calendarList().list().execute()
    except HttpError as e:
        raise GCalError(f"캘린더 목록을 가져오지 못했습니다: {e}")
    cals = []
    for c in result.get("items", []):
        if c.get("accessRole") not in ("owner", "writer"):
            continue
        cals.append({
            "id": c["id"],
            "name": c.get("summaryOverride") or c.get("summary", c["id"]),
            "primary": bool(c.get("primary")),
        })
    cals.sort(key=lambda c: (not c["primary"], c["name"]))
    return cals


def upsert_event(credentials_file, token_file, calendar_id, event_id, title, date_str):
    """종일 이벤트를 생성하거나(기존 event_id 없음) 갱신한다. 생성/갱신된 이벤트 id를 반환."""
    service = get_service(credentials_file, token_file)
    end_date = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    body = {
        "summary": title,
        "start": {"date": date_str},
        "end": {"date": end_date},
    }
    try:
        if event_id:
            ev = service.events().update(
                calendarId=calendar_id, eventId=event_id, body=body
            ).execute()
        else:
            ev = service.events().insert(calendarId=calendar_id, body=body).execute()
        return ev["id"]
    except HttpError as e:
        if event_id and e.resp.status in (404, 410):
            # 캘린더에서 사용자가 직접 지운 경우 — 새로 생성
            ev = service.events().insert(calendarId=calendar_id, body=body).execute()
            return ev["id"]
        raise GCalError(f"캘린더 등록에 실패했습니다: {e}")


def delete_event(credentials_file, token_file, calendar_id, event_id):
    if not event_id:
        return
    service = get_service(credentials_file, token_file)
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except HttpError as e:
        if e.resp.status not in (404, 410, 400):
            raise GCalError(f"캘린더 일정 삭제에 실패했습니다: {e}")
