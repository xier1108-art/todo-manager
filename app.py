# -*- coding: utf-8 -*-
"""할일 정리 — ★할일★ 폴더를 노션처럼 그룹별 목록으로 보여주는 창 프로그램."""
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import webview
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


def load_data():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"root": DEFAULT_ROOT, "groups": [], "meta": {}}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
        removed = []
        for g in data["groups"]:
            kept = []
            for tid in g.get("tasks", []):
                if tid in tasks:
                    kept.append(tid)
                else:
                    removed.append(tid.split("/")[-1])
            g["tasks"] = kept
        data["meta"] = {k: v for k, v in data.get("meta", {}).items() if k in tasks}

        # 새로 생긴 업무는 카테고리 이름과 같은 그룹에 자동 배치 (없으면 그룹 생성)
        assigned = {tid for g in data["groups"] for tid in g["tasks"]}
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

        save_data(data)
        return {"data": data, "tasks": tasks, "removed": removed,
                "rootExists": Path(data["root"]).is_dir()}

    def save_state(self, data):
        save_data(data)
        return True

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

    def toggle_on_top(self):
        window.on_top = not window.on_top
        return window.on_top

    def resize_window(self, width, height):
        width = max(MIN_W, int(width))
        height = max(MIN_H, int(height))
        window.resize(width, height)

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
    )
    window.events.closing += on_closing
    webview.start(setup_tray, icon=str(ICON_FILE) if ICON_FILE.exists() else None)
