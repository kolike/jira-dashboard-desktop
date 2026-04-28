import json
import sys
from pathlib import Path
from typing import Any


def resource_path(filename: str) -> Path:
    if getattr(sys, "frozen", False):
        base_path = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base_path = Path(__file__).resolve().parent
    return base_path / filename


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"

APP_ICON_ICO = resource_path("app_icon.ico")
RED_ICON_PATH = resource_path("app_icon.png")
BLUE_ICON_PATH = resource_path("icon128.png")

APP_TITLE = "Jira Fast Watcher"

DEFAULT_CONFIG = {
    "base_url": "https://jira.vseinstrumenti.ru",
    "token": "",
    "interval_seconds": 10,
    "enabled": True,
    "red_jql": 'project = "Рабочее место" AND (Регион = Ковров OR "Регион портал" = "Ковров(офис)") AND resolution = Unresolved AND assignee in (EMPTY)',
    "blue_jql": 'project = "Рабочее место" AND (Регион = Владимир OR Регион = "Не заполнено" OR Регион = Нижний-Новгород OR Регион = Москва OR "Регион портал" = "Владимир(офис)" OR "Регион портал" = "Москва(офис)") AND resolution = Unresolved AND assignee in (EMPTY)',
    "work_jql": 'project = "Рабочее место" AND resolution = Unresolved AND assignee = currentUser()',
    "completed_jql": 'project = "Рабочее место" AND assignee = currentUser() AND resolution != Unresolved ORDER BY resolved DESC',
    "unassigned_alert_minutes": 30,
}

DEFAULT_STATE = {
    "known_red": [],
    "known_blue": [],
    "known_work": [],
    "current_red_keys": [],
    "current_blue_keys": [],
    "current_work_keys": [],
    "last_check_time": "",
    "last_error": "",
    "analytics": {
        "taken_count": 0,
        "new_red_count": 0,
        "new_blue_count": 0,
        "new_work_count": 0,
        "completed_records": [],
        "first_seen_by_key": {},
        "daily_created_seen": {},
        "daily_closed": {},
    },
    "duplicate_hints_shown": [],
    "alerted_unassigned_keys": [],
}


def load_json(path: Path, default_data: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        save_json(path, default_data)
        return default_data.copy()

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

    merged = default_data.copy()
    if isinstance(data, dict):
        merged.update(data)
    return merged


def save_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config() -> dict[str, Any]:
    config = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    for key, value in DEFAULT_CONFIG.items():
        config.setdefault(key, value)
    return config


def save_config(data: dict[str, Any]) -> None:
    save_json(CONFIG_PATH, data)


def load_state() -> dict[str, Any]:
    state = load_json(STATE_PATH, DEFAULT_STATE)
    for key, value in DEFAULT_STATE.items():
        state.setdefault(key, value)
    return state


def save_state(data: dict[str, Any]) -> None:
    save_json(STATE_PATH, data)


def exportable_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_url": config.get("base_url", ""),
        "interval_seconds": int(config.get("interval_seconds", 10)),
        "enabled": bool(config.get("enabled", True)),
        "red_jql": config.get("red_jql", ""),
        "blue_jql": config.get("blue_jql", ""),
        "work_jql": config.get("work_jql", DEFAULT_CONFIG["work_jql"]),
        "completed_jql": config.get("completed_jql", DEFAULT_CONFIG["completed_jql"]),
    }


def trim_text(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
