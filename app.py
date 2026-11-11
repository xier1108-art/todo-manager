# -*- coding: utf-8 -*-
"""할일 정리 — ★할일★ 폴더를 노션처럼 그룹별 목록으로 보여주는 창 프로그램."""
import ctypes
import json
import os
from ctypes import wintypes
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import webview
from webview.window import FixPoint
import pystray
from PIL import Image

import gcal

# exe로 빌드된 경우 실행 파일이 있는 폴더에 데이터를 저장하고,
# 번들된 리소스(web/index.html)는 PyInstaller가 풀어놓은 임시 위치(_MEIPASS)에서 읽는다.
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    RES_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent
    RES_DIR = APP_DIR

DATA_FILE = APP_DIR / "data.json"
DEFAULT_ROOT = r"G:\바탕화면\★할일★"
ICON_FILE = RES_DIR / "icon.ico"
MIN_W, MIN_H = 760, 500
TOKEN_FILE = APP_DIR / "token.json"

user32 = ctypes.windll.user32
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
user32.MonitorFromWindow.restype = wintypes.HANDLE


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


# 64비트에서 핸들이 int로 잘리지 않도록 인자 타입을 지정한다.
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]


_restore_rect = None  # 최대화 전 창 위치·크기 (물리 픽셀)


def get_bg_color():
    """OS 다크/라이트 설정에 맞춰 초기 창 배경색을 고른다.
    (WebView2가 콘텐츠를 그리기 전/리사이즈 순간에 살짝 보일 수 있는
    기본 배경이 앱 테마와 다르면 모서리 등에 틈처럼 보이는 걸 방지)"""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "#faf8f4" if light else "#0c0d0f"
    except OSError:
        return "#0c0d0f"


# 앱이 마지막으로 쓴 data.json의 수정 시각. 이것과 다르면 외부(자동화 등)에서 고친 것.
_seen_mtime = None


def _mtime():
    try:
        return DATA_FILE.stat().st_mtime_ns
    except OSError:
        return None


def load_data():
    if not DATA_FILE.exists():
        return {"root": DEFAULT_ROOT, "groups": [], "meta": {}}
    # 외부 프로그램이 쓰는 도중이면 잠깐 깨진 내용이 읽힐 수 있어 몇 번 다시 시도한다.
    for _ in range(10):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            time.sleep(0.2)
    # 빈 데이터로 대신하면 이어지는 저장이 진짜 데이터를 덮어써 버리므로 여기서 멈춘다.
    raise RuntimeError("data.json을 읽을 수 없습니다")


def save_data(data):
    global _seen_mtime
    # 임시 파일에 다 쓴 뒤 바꿔치기해서, 외부 프로그램이 반쯤 쓰인 파일을 읽지 않게 한다.
    tmp = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)
    _seen_mtime = _mtime()


def scan_tasks(root):
    """카테고리(1단계)/업무(2단계) 폴더를 스캔해 업무 목록을 만든다."""
    tasks = {}
    rootp = Path(root)
    if not rootp.is_dir():
        return tasks
    for cat in sorted(rootp.iterdir()):
        if not cat.is_dir():
            continue
        for t in sorted(cat.iterdir()):
            if not t.is_dir():
                continue
            rel = f"{cat.name}/{t.name}"
            file_count = 0
            latest = t.stat().st_mtime
            for dirpath, dirnames, filenames in os.walk(t):
                for fn in filenames:
                    if fn.startswith("~$"):
                        continue
                    file_count += 1
                    try:
                        m = os.stat(os.path.join(dirpath, fn)).st_mtime
                        if m > latest:
                            latest = m
                    except OSError:
                        pass
            tasks[rel] = {
                "id": rel,
                "name": t.name,
                "category": cat.name,
                "mtime": datetime.fromtimestamp(latest).strftime("%Y-%m-%d"),
                "fileCount": file_count,
            }
    return tasks


class Api:
    def get_state(self):
        data = load_data()
        tasks = scan_tasks(data["root"])

        # 디스크에서 사라진 업무(완료 후 개인 하드로 이동)는 목록에서 정리
        # (그룹 바로 아래 업무뿐 아니라 중분류 안의 업무도 함께 정리)
        removed = []
        for g in data["groups"]:
            kept = []
            for tid in g.get("tasks", []):
                if tid in tasks:
                    kept.append(tid)
                else:
                    removed.append(tid.split("/")[-1])
            g["tasks"] = kept
            for sg in g.get("subgroups", []):
                kept_sg = []
                for tid in sg.get("tasks", []):
                    if tid in tasks:
                        kept_sg.append(tid)
                    else:
                        removed.append(tid.split("/")[-1])
                sg["tasks"] = kept_sg
        data["meta"] = {k: v for k, v in data.get("meta", {}).items() if k in tasks}

        # 새로 생긴 업무는 카테고리 이름과 같은 그룹에 자동 배치 (없으면 그룹 생성)
        assigned = set()
        for g in data["groups"]:
            assigned.update(g["tasks"])
            for sg in g.get("subgroups", []):
                assigned.update(sg.get("tasks", []))
        for tid, t in tasks.items():
            if tid in assigned:
                continue
            gname = t["category"].strip("★ ")
            grp = next((g for g in data["groups"] if g["name"] == gname), None)
            if grp is None:
                grp = {"id": uuid.uuid4().hex[:8], "name": gname,
                       "collapsed": False, "tasks": []}
                data["groups"].append(grp)
            grp["tasks"].append(tid)

        # shortcut: 위에서 읽은 뒤 여기서 쓰기까지(수 ms) 사이의 외부 수정은 덮어써진다, 문제 되면 쓰기 직전 mtime 재확인
        save_data(data)
        return {"data": data, "tasks": tasks, "removed": removed,
                "rootExists": Path(data["root"]).is_dir()}

    def save_state(self, data):
        # 화면이 아직 모르는 외부 수정이 있으면 덮어쓰지 않는다 (화면 쪽이 새로 불러옴).
        if _mtime() != _seen_mtime:
            return {"conflict": True}
        save_data(data)
        return True

    def data_changed(self):
        return _mtime() != _seen_mtime

    def open_path(self, rel):
        p = Path(load_data()["root"]) / rel
        if p.exists():
            os.startfile(str(p))
            return True
        return False

    def list_children(self, rel):
        p = Path(load_data()["root"]) / rel
        out = []
        if not p.is_dir():
            return out
        try:
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except OSError:
            return out
        for c in entries:
            if c.name.startswith("~$"):
                continue
            out.append({"name": c.name, "dir": c.is_dir(), "rel": f"{rel}/{c.name}"})
        return out

    def set_root(self, path):
        data = load_data()
        if not Path(path).is_dir():
            return False
        data["root"] = path
        save_data(data)
        return True

    def pick_root_folder(self):
        """네이티브 폴더 선택 다이얼로그를 띄운다. 고르면 자동으로 저장까지 한다."""
        data = load_data()
        start_dir = data["root"] if Path(data["root"]).is_dir() else ""
        result = window.create_file_dialog(webview.FOLDER_DIALOG, directory=start_dir)
        if not result:
            return None
        path = result[0]
        data["root"] = path
        save_data(data)
        return path

    # ── 창 제어 (프레임 없는 위젯형 창이라 직접 구현) ──────
    def minimize_window(self):
        window.minimize()

    def hide_window(self):
        window.hide()

    # 테두리 없는 창은 WinForms 기본 최대화가 작업표시줄까지 덮으므로,
    # 창이 있는 모니터의 작업 영역(작업표시줄 제외)에 직접 맞춘다. 좌표는 모두 물리 픽셀.
    def maximize_window(self):
        global _restore_rect
        hwnd = window.native.Handle.ToInt32()
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        _restore_rect = (r.left, r.top, r.right - r.left, r.bottom - r.top)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi))  # 2 = 가장 가까운 모니터
        w = mi.rcWork
        user32.SetWindowPos(hwnd, None, w.left, w.top, w.right - w.left, w.bottom - w.top, 0x0014)  # NOZORDER|NOACTIVATE

    def restore_window(self):
        if _restore_rect:
            user32.SetWindowPos(window.native.Handle.ToInt32(), None, *_restore_rect, 0x0014)

    def toggle_on_top(self):
        window.on_top = not window.on_top
        return window.on_top

    def resize_window(self, width, height, fix_point=3):
        """fix_point: FixPoint 비트마스크 (NORTH=1, WEST=2, EAST=4, SOUTH=8).
        드래그하는 모서리/변의 반대쪽을 고정점으로 넘긴다. 기본값(3=NORTH|WEST)은
        기존 우하단 리사이즈와 동일하게 좌상단을 고정한다."""
        width = max(MIN_W, int(width))
        height = max(MIN_H, int(height))
        window.resize(width, height, FixPoint(int(fix_point)))

    # ── 구글 캘린더 연동 (중계 서버를 통해 클라이언트 시크릿 없이 로그인) ──
    def gcal_status(self):
        return {"connected": gcal.is_connected(TOKEN_FILE)}

    def gcal_connect(self):
        try:
            gcal.connect(TOKEN_FILE)  # 브라우저 로그인 진행
            return {"ok": True}
        except gcal.GCalError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"연결에 실패했습니다: {e}"}

    def gcal_disconnect(self):
        gcal.disconnect(TOKEN_FILE)
        return {"ok": True}

    def gcal_list_calendars(self):
        try:
            return {"calendars": gcal.list_calendars(TOKEN_FILE)}
        except gcal.GCalError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"캘린더 목록을 가져오지 못했습니다: {e}"}

    def gcal_sync_deadline(self, task_id, task_name, deadline, calendar_id):
        """마감일이 바뀔 때마다 호출되어 종일 일정을 등록/수정/삭제한다."""
        if not calendar_id:
            return {"skipped": True}

        data = load_data()
        meta = data["meta"].setdefault(
            task_id, {"memo": "", "deadline": "", "status": "대기"}
        )
        old_event_id = meta.get("gcalEventId")
        old_calendar_id = meta.get("gcalCalendarId")

        try:
            if not deadline:
                if old_event_id and old_calendar_id:
                    gcal.delete_event(TOKEN_FILE, old_calendar_id, old_event_id)
                meta.pop("gcalEventId", None)
                meta.pop("gcalCalendarId", None)
                save_data(data)
                return {"ok": True}

            if old_event_id and old_calendar_id and old_calendar_id != calendar_id:
                gcal.delete_event(TOKEN_FILE, old_calendar_id, old_event_id)
                old_event_id = None

            event_id = gcal.upsert_event(
                TOKEN_FILE, calendar_id, old_event_id, f"(업무) {task_name}", deadline,
            )
            meta["gcalEventId"] = event_id
            meta["gcalCalendarId"] = calendar_id
            save_data(data)
            return {"ok": True}
        except gcal.GCalError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"동기화에 실패했습니다: {e}"}


def make_tray_icon():
    if ICON_FILE.exists():
        return Image.open(ICON_FILE)
    im = Image.new("RGBA", (64, 64), (35, 131, 226, 255))
    return im


def show_window():
    window.show()
    window.restore()


_quitting = False


def quit_app(tray_icon):
    global _quitting
    _quitting = True
    tray_icon.stop()
    window.destroy()


def setup_tray():
    menu = pystray.Menu(
        pystray.MenuItem("열기", lambda: show_window(), default=True),
        pystray.MenuItem("완전 종료", lambda icon: quit_app(icon)),
    )
    icon = pystray.Icon("할일정리", make_tray_icon(), "할일 정리", menu)
    icon.run()


def on_closing():
    # 트레이 메뉴의 "완전 종료"가 아닌 이상(Alt+F4 등) 실제로 닫지 않고 트레이로 숨긴다.
    if _quitting:
        return
    window.hide()
    return False


if __name__ == "__main__":
    api = Api()
    window = webview.create_window(
        "할일 정리",
        str(RES_DIR / "web" / "index.html"),
        js_api=api,
        width=1150,
        height=800,
        min_size=(MIN_W, MIN_H),
        frameless=True,
        easy_drag=False,
        background_color=get_bg_color(),
    )
    window.events.closing += on_closing
    # 글꼴/테마/정렬 설정은 브라우저 localStorage가 아니라 data.json에 저장한다
    # (private_mode=False로 localStorage를 유지하려던 적이 있었는데, 그러면
    # pywebview가 창마다 같은 포트를 재사용하게 되어 WebView2가 이전 실행의
    # 페이지를 캐싱해 새로 고친 코드가 반영 안 되는 문제가 있었음).
    webview.start(setup_tray, icon=str(ICON_FILE) if ICON_FILE.exists() else None)
