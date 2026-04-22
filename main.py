import json
import logging
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any
from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from PySide6.QtGui import QAction, QIcon
import ctypes
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("jira.fast.watcher")

import requests
from requests import Session
from requests.exceptions import RequestException

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot, QSize
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    from win11toast import toast as win_toast
except ImportError:
    from win11toast import notify as win_toast

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


class AppSignals(QObject):
    log_message = Signal(str)
    tray_tooltip = Signal(str)
    qt_message = Signal(str, str)
    monitoring_changed = Signal(bool)
    stats_updated = Signal(int, int, int, str, str)
    red_issues_updated = Signal(list)
    blue_issues_updated = Signal(list)
    work_issues_updated = Signal(list)
    completed_issues_loaded = Signal(list)
    analytics_updated = Signal(dict)


class MemoryLogHandler(logging.Handler):
    def __init__(self, signals: AppSignals, max_lines: int = 1500) -> None:
        super().__init__()
        self.signals = signals
        self.lines: list[str] = []
        self.max_lines = max_lines
        

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.lines.append(msg)
            if len(self.lines) > self.max_lines:
                self.lines = self.lines[-self.max_lines :]
            self.signals.log_message.emit(msg)
        except Exception:
            pass

    def clear(self) -> None:
        self.lines.clear()


class JiraClient:
    def __init__(self) -> None:
        self.session: Session = requests.Session()
        self.field_name_map: dict[str, str] = {}
        self.region_field_ids: list[str] = []
        self.region_portal_field_ids: list[str] = []
        self.request_type_field_ids: list[str] = []
        self.request_type_name_map: dict[str, str] = {}
        self.issue_request_type_cache: dict[str, str] = {}

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "JiraFastWatcher/5.0",
        }

    def fetch_fields(self, base_url: str, token: str) -> None:
        url = f"{base_url.rstrip('/')}/rest/api/2/field"
        response = self.session.get(url, headers=self._headers(token), timeout=20)

        if not response.ok:
            raise RequestException(f"HTTP {response.status_code}: {response.text}")

        data = response.json()
        if not isinstance(data, list):
            return

        self.field_name_map.clear()
        self.region_field_ids.clear()
        self.region_portal_field_ids.clear()
        self.request_type_field_ids.clear()

        for item in data:
            if not isinstance(item, dict):
                continue

            field_id = str(item.get("id", "")).strip()
            field_name = str(item.get("name", "")).strip()
            if not field_id or not field_name:
                continue

            self.field_name_map[field_id] = field_name
            lowered = field_name.lower()
            if lowered == "регион":
                self.region_field_ids.append(field_id)
            elif lowered == "регион портал":
                self.region_portal_field_ids.append(field_id)
            elif lowered in {"тип запроса", "тип обращения", "request type"}:
                self.request_type_field_ids.append(field_id)

    def _resolve_request_type_name(self, base_url: str, token: str, request_type_id: str) -> str:
        if not request_type_id:
            return "Не указан"
        if request_type_id in self.request_type_name_map:
            return self.request_type_name_map[request_type_id]

        detail_url = f"{base_url.rstrip('/')}/rest/servicedeskapi/requesttype/{request_type_id}"
        detail_response = self.session.get(detail_url, headers=self._headers(token), timeout=20)
        if detail_response.ok:
            detail_payload = detail_response.json()
            payload = detail_payload if isinstance(detail_payload, dict) else {}
            name = str(payload.get("name") or "").strip()
            if name:
                self.request_type_name_map[request_type_id] = name
                return name

        return request_type_id
    def fetch_request_types(self, base_url: str, token: str) -> None:
        url = f"{base_url.rstrip('/')}/rest/servicedeskapi/requesttype"
        response = self.session.get(url, headers=self._headers(token), timeout=20)
        if not response.ok:
            return
        data = response.json()
        values = data.get("values", []) if isinstance(data, dict) else []
        if not isinstance(values, list):
            return
        self.request_type_name_map.clear()
        for item in values:
            if not isinstance(item, dict):
                continue
            request_type_id = str(item.get("id") or "").strip()
            request_type_name = str(item.get("name") or "").strip()
            if request_type_id and request_type_name:
                self.request_type_name_map[request_type_id] = request_type_name

    def fetch_issues(self, base_url: str, token: str, jql: str) -> list[dict[str, Any]]:
        url = f"{base_url.rstrip('/')}/rest/api/2/search"
        params = {
            "jql": jql,
            "maxResults": 100,
            "fields": "*all",
        }

        response = self.session.get(url, params=params, headers=self._headers(token), timeout=20)

        if not response.ok:
            raise RequestException(f"HTTP {response.status_code}: {response.text}")

        data = response.json()
        issues = data.get("issues", [])
        return issues if isinstance(issues, list) else []

    def extract_region(self, fields: dict[str, Any]) -> str:
        for field_id in self.region_field_ids + self.region_portal_field_ids:
            parsed = self._parse_region_value(fields.get(field_id))
            if parsed:
                return parsed

        for key, value in fields.items():
            readable = self.field_name_map.get(key, key).strip().lower()
            if readable in {"регион", "регион портал"}:
                parsed = self._parse_region_value(value)
                if parsed:
                    return parsed

        return "Не указан"

    def extract_author(self, fields: dict[str, Any]) -> str:
        user = fields.get("creator") or fields.get("reporter")
        if isinstance(user, dict):
            return str(
                user.get("displayName")
                or user.get("name")
                or user.get("emailAddress")
                or "Неизвестно"
            )
        return "Неизвестно"

    def extract_request_type(self, base_url: str, token: str, issue: dict[str, Any]) -> str:
        issue_key = str(issue.get("key") or "").strip()
        if not issue_key:
            return "Не указан"

        cached = self.issue_request_type_cache.get(issue_key)
        if cached:
            return cached

        url = f"{base_url.rstrip('/')}/rest/servicedeskapi/request/{issue_key}"
        response = self.session.get(url, headers=self._headers(token), timeout=20)
        if response.ok:
            response_data = response.json()
            data = response_data if isinstance(response_data, dict) else {}
            request_type_id = str(data.get("requestTypeId") or "").strip()
            if request_type_id:
                name = self._resolve_request_type_name(base_url, token, request_type_id)
                self.issue_request_type_cache[issue_key] = name
                return name

        fields = issue.get("fields", {}) or {}
        for field_id in self.request_type_field_ids:
            parsed = self._parse_region_value(fields.get(field_id))
            if parsed:
                if parsed.isdigit():
                    parsed = self._resolve_request_type_name(base_url, token, parsed)
                self.issue_request_type_cache[issue_key] = parsed
                return parsed

        for key, value in fields.items():
            readable = self.field_name_map.get(key, key).strip().lower()
            if readable in {"тип запроса", "тип обращения", "request type"}:
                parsed = self._parse_region_value(value)
                if parsed:
                    if parsed.isdigit():
                        parsed = self._resolve_request_type_name(base_url, token, parsed)
                    self.issue_request_type_cache[issue_key] = parsed
                    return parsed

        return "Не указан"

    @staticmethod
    def _parse_region_value(value: Any) -> str:
        if value is None:
            return ""

        if isinstance(value, dict):
            for key in ("value", "name", "displayName"):
                candidate = value.get(key)
                if candidate:
                    return str(candidate).strip()

        if isinstance(value, list):
            if not value:
                return ""
            first = value[0]
            if isinstance(first, dict):
                for key in ("value", "name", "displayName"):
                    candidate = first.get(key)
                    if candidate:
                        return str(candidate).strip()
            return str(first).strip()

        return str(value).strip()


class NeonCard(QFrame):
    def __init__(self, title: str, accent: str) -> None:
        super().__init__()
        self.setObjectName("NeonCard")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(accent))
        self.setGraphicsEffect(shadow)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")

        self.body_layout = QVBoxLayout()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.title_label)
        layout.addLayout(self.body_layout)
        self.setLayout(layout)

        self.setStyleSheet(
            f"""
            QFrame#NeonCard {{
                background: rgba(13, 17, 29, 242);
                border: 1px solid {accent};
                border-radius: 18px;
            }}
            QLabel#CardTitle {{
                color: #F4F7FF;
                font-size: 17px;
                font-weight: 900;
                letter-spacing: 1px;
                padding-left: 2px;
                background: transparent;
                border: none;
            }}
            """
        )


class MetricCard(QFrame):
    def __init__(self, label_text: str, accent: str) -> None:
        super().__init__()

        self.label = QLabel(label_text)
        self.value = QLabel("—")

        self.label.setObjectName("MetricLabel")
        self.value.setObjectName("MetricValue")
        self.value.setWordWrap(True)
        self.value.setAlignment(Qt.AlignCenter)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(accent))
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)
        layout.addWidget(self.label, 0, Qt.AlignCenter)
        layout.addWidget(self.value, 1, Qt.AlignCenter)
        self.setLayout(layout)

        self.setStyleSheet(
            f"""
            QFrame {{
                background: rgba(15, 19, 34, 240);
                border: 1px solid {accent};
                border-radius: 16px;
            }}
            QLabel#MetricLabel {{
                color: #9FB0D8;
                font-size: 11px;
                font-weight: 700;
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
            }}
            QLabel#MetricValue {{
                color: #F4F7FF;
                font-size: 17px;
                font-weight: 900;
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
            }}
            """
        )


class StatusIconButton(QToolButton):
    def __init__(self, symbol: str, tooltip: str) -> None:
        super().__init__()
        self._active = False
        self.setText(symbol)
        self.setToolTip(tooltip)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setFixedSize(42, 42)

        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        self.setFont(font)

        self.update_style()

    def set_active(self, is_active: bool) -> None:
        self._active = is_active
        self.update_style()

    def update_style(self) -> None:
        if self._active:
            border = "#D7FF4A"
            bg = "rgba(23, 31, 45, 245)"
            color = "#EDFFD8"
            glow_color = QColor(215, 255, 74, 170)
        else:
            border = "#4B5568"
            bg = "rgba(18, 24, 38, 235)"
            color = "#D3DBEA"
            glow_color = QColor(88, 96, 115, 90)

        self.setStyleSheet(
            f"""
            QToolButton {{
                background: {bg};
                color: {color};
                border: 1px solid {border};
                border-radius: 21px;
            }}
            QToolButton:hover {{
                border: 1px solid #7EB7FF;
                background: rgba(24, 31, 49, 250);
            }}
            """
        )

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16 if self._active else 8)
        shadow.setOffset(0, 0)
        shadow.setColor(glow_color)
        self.setGraphicsEffect(shadow)


class BubbleGridWidget(QListWidget):
    def __init__(self, accent: str, grid_size: QSize | None = None) -> None:
        super().__init__()
        self.default_accent = accent
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setWrapping(True)
        self.setWordWrap(True)
        self.setUniformItemSizes(False)
        self.setSpacing(8)
        self.setGridSize(grid_size or QSize(255, 100))
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: none;
                outline: none;
                color: #F3F6FF;
                font-size: 12px;
                padding: 0px;
            }}

            QListWidget::item {{
                background: transparent;
                border: none;
                padding: 0px;
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 4px 0 4px 0;
            }}

            QScrollBar::handle:vertical {{
                background: rgba(255,255,255,0.18);
                border-radius: 4px;
                min-height: 20px;
            }}

            QScrollBar::handle:vertical:hover {{
                background: rgba(255,255,255,0.30);
            }}

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)


class LogWindow(QWidget):
    def __init__(self, tray_app: "TrayApp") -> None:
        super().__init__()
        self.tray_app = tray_app
        self.setWindowTitle(f"{APP_TITLE} — Логи")
        self.resize(920, 640)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(
            """
            QTextEdit {
                background: #0A0D17;
                color: #DCE7FF;
                border: 1px solid #29314A;
                border-radius: 14px;
                padding: 10px;
                font-family: Consolas, monospace;
                font-size: 12px;
            }
            """
        )

        self.clear_button = QPushButton("Очистить")
        self.clear_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.clear_button.setMinimumHeight(38)
        self.clear_button.setStyleSheet(
            """
            QPushButton {
                background: #171C2B;
                color: #F3F6FF;
                border: 1px solid #33405F;
                border-radius: 12px;
                padding: 8px 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #20283C;
                border: 1px solid #5C76B8;
            }
            """
        )
        self.clear_button.clicked.connect(self.clear_logs)

        buttons = QHBoxLayout()
        buttons.addWidget(self.clear_button)
        buttons.addStretch()

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(buttons)
        layout.addWidget(self.log_view)
        self.setLayout(layout)
        self.setStyleSheet("background: #090C16;")

    def hydrate_from_memory(self) -> None:
        self.log_view.setPlainText("\n".join(self.tray_app.memory_log_handler.lines))
        self.log_view.moveCursor(QTextCursor.End)

    def clear_logs(self) -> None:
        self.tray_app.memory_log_handler.clear()
        self.log_view.clear()

    @Slot(str)
    def append_log(self, msg: str) -> None:
        self.log_view.append(msg)


class CompletedWindow(QWidget):
    def __init__(self, tray_app: "TrayApp") -> None:
        super().__init__()
        self.tray_app = tray_app
        self.setWindowTitle(f"{APP_TITLE} — Выполненные")
        self.resize(980, 680)

        self.title_label = QLabel("МОИ ВЫПОЛНЕННЫЕ ЗАДАЧИ")
        self.title_label.setStyleSheet(
            """
            QLabel {
                color: #F4F7FF;
                font-size: 20px;
                font-weight: 900;
                background: transparent;
                border: none;
            }
            """
        )

        self.sub_label = QLabel("Сортировка: сначала самые свежие по дате закрытия")
        self.sub_label.setStyleSheet(
            """
            QLabel {
                color: #92A4D3;
                font-size: 12px;
                font-weight: 600;
                background: transparent;
                border: none;
            }
            """
        )

        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.refresh_button.setMinimumHeight(38)
        self.refresh_button.clicked.connect(self.tray_app.load_completed_issues)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск: ключ, тема, автор…")
        self.search_edit.textChanged.connect(self.apply_filters)

        self.category_filter = QComboBox()
        self.category_filter.currentIndexChanged.connect(self.apply_filters)
        self.status_filter = QComboBox()
        self.status_filter.currentIndexChanged.connect(self.apply_filters)
        self.region_filter = QComboBox()
        self.region_filter.currentIndexChanged.connect(self.apply_filters)
        self.request_type_filter = QComboBox()
        self.request_type_filter.currentIndexChanged.connect(self.apply_filters)

        self.stats_label = QLabel("Статистика: —")
        self.stats_label.setStyleSheet(
            """
            QLabel {
                color: #B7C6EC;
                font-size: 12px;
                font-weight: 600;
                background: transparent;
                border: none;
            }
            """
        )

        self.list_widget = BubbleGridWidget("#B58CFF", QSize(300, 118))
        self.list_widget.itemClicked.connect(self.open_issue_from_item)
        self.all_issues: list[dict[str, Any]] = []
        self.analytics_data: dict[str, Any] = {}

        top = QHBoxLayout()
        top.addWidget(self.title_label)
        top.addStretch()
        top.addWidget(self.refresh_button)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        filters.addWidget(self.search_edit, 2)
        filters.addWidget(self.category_filter, 1)
        filters.addWidget(self.status_filter, 1)
        filters.addWidget(self.region_filter, 1)
        filters.addWidget(self.request_type_filter, 1)

        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addLayout(top)
        root.addWidget(self.sub_label)
        root.addLayout(filters)
        root.addWidget(self.stats_label)
        root.addWidget(self.list_widget)

        self.setLayout(root)
        self.setStyleSheet(
            """
            QWidget {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #060D1D, stop:0.6 #09152B, stop:1 #0A1931);
                color: #F7FAFF;
                font-family: Segoe UI, Arial, sans-serif;
            }
            QLineEdit, QComboBox {
                background: rgba(16, 29, 52, 0.82);
                color: #F3F7FF;
                border: 1px solid rgba(120, 162, 230, 0.45);
                border-radius: 10px;
                padding: 7px 10px;
                min-height: 34px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid rgba(137, 207, 255, 0.95);
            }
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 rgba(57, 118, 215, 0.55),
                    stop:1 rgba(86, 213, 197, 0.50));
                color: #F7FBFF;
                border: 1px solid rgba(126, 168, 244, 0.88);
                border-radius: 12px;
                padding: 8px 12px;
                font-weight: 800;
            }
            QPushButton:hover {
                background: rgba(90, 160, 245, 0.65);
                border: 1px solid #B0D6FF;
            }
            """
        )
        self._set_default_filters()

    def open_issue_from_item(self, item: QListWidgetItem) -> None:
        issue_url = item.data(Qt.UserRole)
        if issue_url:
            webbrowser.open(issue_url)

    @Slot(list)
    def update_issues(self, issues: list[dict[str, Any]]) -> None:
        self.all_issues = issues
        self._rebuild_filter_values()
        self.apply_filters()

    @Slot(dict)
    def update_analytics(self, analytics: dict[str, Any]) -> None:
        self.analytics_data = analytics or {}
        self.apply_filters()

    def _set_default_filters(self) -> None:
        self.category_filter.clear()
        self.category_filter.addItems(["Все категории", "Ковров", "Регионы", "Прочее"])
        self.status_filter.clear()
        self.status_filter.addItem("Все статусы")
        self.region_filter.clear()
        self.region_filter.addItem("Все регионы")
        self.request_type_filter.clear()
        self.request_type_filter.addItem("Все типы запроса")

    def _rebuild_filter_values(self) -> None:
        statuses = sorted(
            {
                self._status_name(issue.get("fields", {}) or {})
                for issue in self.all_issues
            }
        )
        regions = sorted(
            {
                self.tray_app.client.extract_region(issue.get("fields", {}) or {})
                for issue in self.all_issues
            }
        )
        request_types = sorted(
            {
                self.tray_app.get_request_type(issue)
                for issue in self.all_issues
            }
        )

        current_status = self.status_filter.currentText()
        current_region = self.region_filter.currentText()
        current_request_type = self.request_type_filter.currentText()

        self.status_filter.blockSignals(True)
        self.region_filter.blockSignals(True)
        self.request_type_filter.blockSignals(True)

        self.status_filter.clear()
        self.status_filter.addItem("Все статусы")
        self.status_filter.addItems([s for s in statuses if s])

        self.region_filter.clear()
        self.region_filter.addItem("Все регионы")
        self.region_filter.addItems([r for r in regions if r])

        self.request_type_filter.clear()
        self.request_type_filter.addItem("Все типы запроса")
        self.request_type_filter.addItems([r for r in request_types if r and r != "Не указан"])

        idx_status = self.status_filter.findText(current_status)
        if idx_status >= 0:
            self.status_filter.setCurrentIndex(idx_status)
        idx_region = self.region_filter.findText(current_region)
        if idx_region >= 0:
            self.region_filter.setCurrentIndex(idx_region)
        idx_request_type = self.request_type_filter.findText(current_request_type)
        if idx_request_type >= 0:
            self.request_type_filter.setCurrentIndex(idx_request_type)

        self.status_filter.blockSignals(False)
        self.region_filter.blockSignals(False)
        self.request_type_filter.blockSignals(False)

    def apply_filters(self) -> None:
        query = self.search_edit.text().strip().lower()
        selected_category = self.category_filter.currentText()
        selected_status = self.status_filter.currentText()
        selected_region = self.region_filter.currentText()
        selected_request_type = self.request_type_filter.currentText()

        filtered: list[dict[str, Any]] = []
        for issue in self.all_issues:
            fields = issue.get("fields", {}) or {}
            issue_key = str(issue.get("key", ""))
            summary = str(fields.get("summary") or "")
            author = self.tray_app.client.extract_author(fields)
            category = self.tray_app.classify_issue_category(fields)
            status_name = self._status_name(fields)
            region_name = self.tray_app.client.extract_region(fields)
            request_type = self.tray_app.get_request_type(issue)

            if selected_category != "Все категории" and category != selected_category:
                continue
            if selected_status != "Все статусы" and status_name != selected_status:
                continue
            if selected_region != "Все регионы" and region_name != selected_region:
                continue
            if selected_request_type != "Все типы запроса" and request_type != selected_request_type:
                continue

            if query:
                haystack = f"{issue_key} {summary} {author} {status_name} {region_name} {request_type}".lower()
                if query not in haystack:
                    continue

            filtered.append(issue)

        self._render_issues(filtered)
        self._render_stats(filtered)

    def _render_issues(self, issues: list[dict[str, Any]]) -> None:
        self.list_widget.clear()

        if not issues:
            item = QListWidgetItem("Нет выполненных задач")
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            item.setSizeHint(QSize(260, 72))
            self.list_widget.addItem(item)
            return

        for issue in issues:
            issue_key = issue.get("key", "UNKNOWN")
            fields = issue.get("fields", {}) or {}

            summary = trim_text(fields.get("summary") or "Без темы", 62)
            status_name = self._status_name(fields)
            resolved_text = self._resolved_text(fields)
            author_name = self.tray_app.client.extract_author(fields)

            text = (
                f"{issue_key}\n"
                f"{summary}\n"
                f"{status_name} • {resolved_text}\n"
                f"Автор: {author_name}"
            )

            tooltip = (
                f"{issue_key} | {summary} | "
                f"Статус: {status_name} | "
                f"Закрыто: {resolved_text} | "
                f"Автор: {author_name}"
            )

            item = QListWidgetItem(text)
            item.setToolTip(tooltip)
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            item.setData(Qt.UserRole, self.tray_app.build_issue_url(issue_key))
            self.list_widget.addItem(item)

    def _render_stats(self, issues: list[dict[str, Any]]) -> None:
        total = len(issues)
        by_category = {"Ковров": 0, "Регионы": 0, "Прочее": 0}
        for issue in issues:
            category = self.tray_app.classify_issue_category(issue.get("fields", {}) or {})
            by_category[category] = by_category.get(category, 0) + 1

        analytics_total = len((self.analytics_data or {}).get("completed_records", []))
        taken_count = int((self.analytics_data or {}).get("taken_count", 0))
        today_key = datetime.now().strftime("%Y-%m-%d")
        daily_created_seen = int((self.analytics_data or {}).get("daily_created_seen", {}).get(today_key, 0))
        daily_closed = int((self.analytics_data or {}).get("daily_closed", {}).get(today_key, 0))
        reaction_values = [
            int(item.get("reaction_minutes"))
            for item in (self.analytics_data or {}).get("completed_records", [])
            if isinstance(item, dict) and item.get("reaction_minutes") is not None
        ]
        avg_reaction = int(sum(reaction_values) / len(reaction_values)) if reaction_values else 0
        request_type_counts: dict[str, int] = {}
        for issue in issues:
            request_type = self.tray_app.get_request_type(issue)
            if request_type and request_type != "Не указан":
                request_type_counts[request_type] = request_type_counts.get(request_type, 0) + 1

        top_request_types = sorted(request_type_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        request_type_stats = ", ".join([f"{name}: {count}" for name, count in top_request_types]) or "нет данных"

        self.stats_label.setText(
            "Статистика: "
            f"в фильтре {total} | "
            f"Ковров {by_category.get('Ковров', 0)} | "
            f"Регионы {by_category.get('Регионы', 0)} | "
            f"Прочее {by_category.get('Прочее', 0)} | "
            f"накоплено без дублей {analytics_total} | "
            f"взято в работу {taken_count} | "
            f"сегодня создано {daily_created_seen} | "
            f"сегодня закрыто {daily_closed} | "
            f"ср. реакция {avg_reaction} мин | "
            f"типы запроса: {request_type_stats}"
        )

    @staticmethod
    def _status_name(fields: dict[str, Any]) -> str:
        status_obj = fields.get("status") or {}
        return str(status_obj.get("name") or "Без статуса")

    @staticmethod
    def _resolved_text(fields: dict[str, Any]) -> str:
        resolved = str(fields.get("resolved") or "").strip()
        if not resolved:
            return "Дата не указана"

        try:
            dt = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return resolved


class SettingsWindow(QWidget):
    def __init__(self, tray_app: "TrayApp") -> None:
        super().__init__()
        self.tray_app = tray_app
        self.setWindowTitle(f"{APP_TITLE} — Настройки")
        self.resize(980, 820)

        self.setStyleSheet(
            """
            QWidget {
                background: #0B0F1A;
                color: #F0F4FF;
                font-size: 13px;
            }
            QLabel {
                color: #DDE6FF;
                background: transparent;
                border: none;
            }
            QLineEdit, QTextEdit, QSpinBox {
                background: #12182A;
                color: #F3F6FF;
                border: 1px solid #33405F;
                border-radius: 12px;
                padding: 8px;
            }
            QLineEdit:focus, QTextEdit:focus, QSpinBox:focus {
                border: 1px solid #72A8FF;
            }
            QPushButton {
                background: #151C30;
                color: #F4F7FF;
                border: 1px solid #30405E;
                border-radius: 12px;
                padding: 9px 12px;
                font-weight: 700;
                min-height: 40px;
            }
            QPushButton:hover {
                background: #1D2640;
                border: 1px solid #69A7FF;
            }
            """
        )

        self.base_url_edit = QLineEdit()
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(5, 3600)

        self.red_jql_edit = QTextEdit()
        self.blue_jql_edit = QTextEdit()
        self.work_jql_edit = QTextEdit()
        self.completed_jql_edit = QTextEdit()

        self.status_label = QLabel("Готово")
        self.status_label.setStyleSheet("font-weight: 700; color: #99FFC1; background: transparent; border: none;")

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFormAlignment(Qt.AlignTop)
        form.setSpacing(12)
        form.addRow("Jira Base URL:", self.base_url_edit)
        form.addRow("PAT Token:", self.token_edit)
        form.addRow("Интервал, сек:", self.interval_spin)

        self.save_button = QPushButton("Сохранить")
        self.import_button = QPushButton("Импорт конфига")
        self.export_button = QPushButton("Экспорт конфига")
        self.close_button = QPushButton("Закрыть")

        self.save_button.clicked.connect(self.save_settings)
        self.import_button.clicked.connect(self.import_config)
        self.export_button.clicked.connect(self.export_config)
        self.close_button.clicked.connect(self.hide)

        buttons = QHBoxLayout()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.import_button)
        buttons.addWidget(self.export_button)
        buttons.addStretch()
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("ТЕХНИЧЕСКИЕ НАСТРОЙКИ")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: 900; color: #F3F6FF; background: transparent; border: none;")

        layout.addWidget(title)
        layout.addLayout(form)
        layout.addWidget(QLabel("JQL для Коврова:"))
        layout.addWidget(self.red_jql_edit)
        layout.addWidget(QLabel("JQL для Регионов:"))
        layout.addWidget(self.blue_jql_edit)
        layout.addWidget(QLabel("JQL для блока В РАБОТЕ:"))
        layout.addWidget(self.work_jql_edit)
        layout.addWidget(QLabel("JQL для окна ВЫПОЛНЕННЫЕ:"))
        layout.addWidget(self.completed_jql_edit)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)

        self.setLayout(layout)
        self.load_into_form()

    def load_into_form(self) -> None:
        cfg = self.tray_app.config
        self.base_url_edit.setText(cfg.get("base_url", ""))
        self.token_edit.setText(cfg.get("token", ""))
        self.interval_spin.setValue(int(cfg.get("interval_seconds", 10)))
        self.red_jql_edit.setPlainText(cfg.get("red_jql", ""))
        self.blue_jql_edit.setPlainText(cfg.get("blue_jql", ""))
        self.work_jql_edit.setPlainText(cfg.get("work_jql", DEFAULT_CONFIG["work_jql"]))
        self.completed_jql_edit.setPlainText(cfg.get("completed_jql", DEFAULT_CONFIG["completed_jql"]))

    def collect_form_data(self) -> dict[str, Any]:
        data = self.tray_app.config.copy()
        data.update(
            {
                "base_url": self.base_url_edit.text().strip(),
                "token": self.token_edit.text().strip(),
                "interval_seconds": int(self.interval_spin.value()),
                "red_jql": self.red_jql_edit.toPlainText().strip(),
                "blue_jql": self.blue_jql_edit.toPlainText().strip(),
                "work_jql": self.work_jql_edit.toPlainText().strip() or DEFAULT_CONFIG["work_jql"],
                "completed_jql": self.completed_jql_edit.toPlainText().strip() or DEFAULT_CONFIG["completed_jql"],
            }
        )
        return data

    def save_settings(self) -> None:
        try:
            self.tray_app.config = self.collect_form_data()
            save_config(self.tray_app.config)
            self.tray_app._field_map_loaded = False
            self.tray_app.apply_config()
            self.status_label.setText("Настройки сохранены")
            self.tray_app.logger.info("Настройки сохранены")
            self.tray_app.show_qt_message(APP_TITLE, "Настройки сохранены")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить настройки:\n{e}")

    def export_config(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт конфига",
            str(BASE_DIR / "jira_fast_watcher_config.json"),
            "JSON Files (*.json)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(exportable_config(self.collect_form_data()), f, ensure_ascii=False, indent=2)
            self.status_label.setText("Конфиг экспортирован")
            self.tray_app.logger.info(f"Конфиг экспортирован: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось экспортировать конфиг:\n{e}")

    def import_config(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Импорт конфига",
            str(BASE_DIR),
            "JSON Files (*.json)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                imported = json.load(f)

            if not isinstance(imported, dict):
                raise ValueError("Файл должен содержать JSON-объект")

            self.base_url_edit.setText(str(imported.get("base_url", self.base_url_edit.text())))
            self.interval_spin.setValue(int(imported.get("interval_seconds", self.interval_spin.value())))
            self.red_jql_edit.setPlainText(str(imported.get("red_jql", self.red_jql_edit.toPlainText())))
            self.blue_jql_edit.setPlainText(str(imported.get("blue_jql", self.blue_jql_edit.toPlainText())))
            self.work_jql_edit.setPlainText(str(imported.get("work_jql", self.work_jql_edit.toPlainText())))
            self.completed_jql_edit.setPlainText(str(imported.get("completed_jql", self.completed_jql_edit.toPlainText())))

            enabled_value = imported.get("enabled")
            if isinstance(enabled_value, bool):
                self.tray_app.config["enabled"] = enabled_value

            self.status_label.setText("Конфиг импортирован")
            self.tray_app.logger.info(f"Конфиг импортирован: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать конфиг:\n{e}")


class DashboardWindow(QWidget):
    def __init__(self, tray_app: "TrayApp") -> None:
        super().__init__()
        self.tray_app = tray_app

        self.setWindowTitle(APP_TITLE)
        self.resize(1180, 760)
        self.setMinimumSize(1080, 700)
        self.setMaximumHeight(820)

        self.setStyleSheet(
            """
            QWidget {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #060B18, stop:0.55 #0B1326, stop:1 #091426);
                color: #F4F7FF;
                font-family: Segoe UI, Arial, sans-serif;
            }
            QLabel {
                background: transparent;
                border: none;
            }
            QLabel#MainTitle {
                color: #F8FCFF;
                font-size: 25px;
                font-weight: 900;
                letter-spacing: 1.4px;
                background: transparent;
                border: none;
            }
            QLabel#SubTitle {
                color: #A6B8E6;
                font-size: 12px;
                font-weight: 700;
                background: transparent;
                border: none;
            }
            """
        )

        self.main_title = QLabel("JIRA FAST WATCHER")
        self.main_title.setObjectName("MainTitle")

        self.sub_title = QLabel("Разработано NikolasFonPetroff")
        self.sub_title.setObjectName("SubTitle")

        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        title_layout.addWidget(self.main_title)
        title_layout.addWidget(self.sub_title)

        self.monitor_button = StatusIconButton("⏻", "Включить / выключить мониторинг")
        self.monitor_button.clicked.connect(self.toggle_monitoring)

        self.completed_button = StatusIconButton("✓", "Выполненные задачи")
        self.completed_button.set_active(True)
        self.completed_button.clicked.connect(self.tray_app.show_completed_window)

        self.menu_button = StatusIconButton("⚙", "Действия")
        self.menu_button.set_active(True)

        self.actions_menu = QMenu(self)
        self.actions_menu.setStyleSheet(
            """
            QMenu {
                background: #101625;
                color: #F3F6FF;
                border: 1px solid #33405F;
                border-radius: 12px;
                padding: 6px;
            }
            QMenu::item {
                padding: 8px 20px 8px 12px;
                border-radius: 8px;
            }
            QMenu::item:selected {
                background: #1E2A45;
            }
            """
        )

        action_settings = QAction("Настройки", self)
        action_settings.triggered.connect(self.tray_app.show_settings)
        self.actions_menu.addAction(action_settings)

        action_check = QAction("Проверить сейчас", self)
        action_check.triggered.connect(lambda: self.tray_app.run_check(force_notify=True))
        self.actions_menu.addAction(action_check)

        action_logs = QAction("Логи", self)
        action_logs.triggered.connect(self.tray_app.show_log_window)
        self.actions_menu.addAction(action_logs)

        action_reset = QAction("Сбросить состояние", self)
        action_reset.triggered.connect(self.reset_state)
        self.actions_menu.addAction(action_reset)

        self.menu_button.setMenu(self.actions_menu)
        self.menu_button.setPopupMode(QToolButton.InstantPopup)

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(8)
        controls_layout.addWidget(self.monitor_button)
        controls_layout.addWidget(self.completed_button)
        controls_layout.addWidget(self.menu_button)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)
        header_layout.addLayout(title_layout)
        header_layout.addStretch()
        header_layout.addLayout(controls_layout)

        self.metric_red = MetricCard("КОВРОВ", "#FF4976")
        self.metric_blue = MetricCard("РЕГИОНЫ", "#56A6FF")
        self.metric_work = MetricCard("В РАБОТЕ", "#7CFFBE")
        self.metric_last = MetricCard("ПОСЛЕДНЯЯ ПРОВЕРКА", "#D7FF51")
        self.metric_error = MetricCard("ПОСЛЕДНЯЯ ОШИБКА", "#FFC66D")

        metrics_layout = QHBoxLayout()
        metrics_layout.setSpacing(8)
        metrics_layout.addWidget(self.metric_red)
        metrics_layout.addWidget(self.metric_blue)
        metrics_layout.addWidget(self.metric_work)
        metrics_layout.addWidget(self.metric_last)
        metrics_layout.addWidget(self.metric_error)

        self.red_card = NeonCard("КОВРОВ", "#FF4C7A")
        self.blue_card = NeonCard("РЕГИОНЫ", "#4EA5FF")
        self.work_card = NeonCard("В РАБОТЕ", "#68FFC0")

        self.red_list = BubbleGridWidget("#FF4C7A", QSize(255, 92))
        self.blue_list = BubbleGridWidget("#4EA5FF", QSize(255, 92))
        self.work_list = BubbleGridWidget("#68FFC0", QSize(270, 132))

        self.red_list.itemClicked.connect(self.open_issue_from_item)
        self.blue_list.itemClicked.connect(self.open_issue_from_item)
        self.work_list.itemClicked.connect(self.open_issue_from_item)

        self.red_card.body_layout.addWidget(self.red_list)
        self.blue_card.body_layout.addWidget(self.blue_list)
        self.work_card.body_layout.addWidget(self.work_list)

        top_split_layout = QHBoxLayout()
        top_split_layout.setSpacing(10)
        top_split_layout.addWidget(self.red_card, 1)
        top_split_layout.addWidget(self.blue_card, 1)

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)
        root_layout.addLayout(header_layout)
        root_layout.addLayout(metrics_layout)
        root_layout.addLayout(top_split_layout, 1)
        root_layout.addWidget(self.work_card, 1)
        self.setLayout(root_layout)

    def open_issue_from_item(self, item: QListWidgetItem) -> None:
        issue_url = item.data(Qt.UserRole)
        if issue_url:
            webbrowser.open(issue_url)

    def toggle_monitoring(self) -> None:
        self.tray_app.config["enabled"] = not bool(self.tray_app.config.get("enabled", True))
        save_config(self.tray_app.config)
        self.tray_app.apply_config()

        if self.tray_app.config["enabled"]:
            self.tray_app.logger.info("Мониторинг включён из главного окна")
            self.tray_app.show_qt_message(APP_TITLE, "Мониторинг включён")
        else:
            self.tray_app.logger.info("Мониторинг выключен из главного окна")
            self.tray_app.show_qt_message(APP_TITLE, "Мониторинг выключен")

    def reset_state(self) -> None:
        reply = QMessageBox.question(
            self,
            "Подтверждение",
            "Сбросить сохранённое состояние тикетов?\nТекущие заявки снова будут считаться новыми.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.tray_app.known_red.clear()
        self.tray_app.known_blue.clear()
        self.tray_app.known_work.clear()
        self.tray_app.current_red_keys.clear()
        self.tray_app.current_blue_keys.clear()
        self.tray_app.current_work_keys.clear()
        self.tray_app.current_red_issues = []
        self.tray_app.current_blue_issues = []
        self.tray_app.current_work_issues = []
        self.tray_app.last_error = ""
        self.tray_app.analytics = {
            "taken_count": 0,
            "new_red_count": 0,
            "new_blue_count": 0,
            "new_work_count": 0,
            "completed_records": [],
            "first_seen_by_key": {},
            "daily_created_seen": {},
            "daily_closed": {},
        }
        self.tray_app.duplicate_hints_shown.clear()
        self.tray_app.alerted_unassigned_keys.clear()
        self.tray_app.persist_state()
        self.tray_app.update_tray_tooltip()
        self.tray_app.emit_stats()
        self.tray_app.signals.red_issues_updated.emit([])
        self.tray_app.signals.blue_issues_updated.emit([])
        self.tray_app.signals.work_issues_updated.emit([])
        self.tray_app.signals.analytics_updated.emit(self.tray_app.analytics)
        self.tray_app.logger.info("Состояние сброшено вручную")
        self.tray_app.show_qt_message(APP_TITLE, "Состояние сброшено")

    @Slot(int, int, int, str, str)
    def update_stats(self, red_count: int, blue_count: int, work_count: int, last_check: str, last_error: str) -> None:
        self.metric_red.value.setText(str(red_count))
        self.metric_blue.value.setText(str(blue_count))
        self.metric_work.value.setText(str(work_count))
        self.metric_last.value.setText(last_check or "—")
        self.metric_error.value.setText(last_error or "—")

    @Slot(list)
    def update_red_issues(self, issues: list[dict[str, Any]]) -> None:
        self.fill_bubbles(self.red_list, issues, self.format_red_issue)

    @Slot(list)
    def update_blue_issues(self, issues: list[dict[str, Any]]) -> None:
        self.fill_bubbles(self.blue_list, issues, self.format_blue_issue)

    @Slot(list)
    def update_work_issues(self, issues: list[dict[str, Any]]) -> None:
        self.fill_work_bubbles(self.work_list, issues)

    @Slot(bool)
    def update_monitoring_button(self, is_enabled: bool) -> None:
        self.monitor_button.set_active(is_enabled)

    def fill_bubbles(self, widget: BubbleGridWidget, issues: list[dict[str, Any]], formatter) -> None:
        widget.clear()

        if not issues:
            item = QListWidgetItem("Сейчас пусто")
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            item.setSizeHint(QSize(260, 124))
            widget.addItem(item)
            return

        for issue in issues:
            issue_key = issue.get("key", "UNKNOWN")
            text, tooltip = formatter(issue)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, self.tray_app.build_issue_url(issue_key))
            item.setSizeHint(QSize(260, 110))
            widget.addItem(item)

            card = self.build_issue_card_widget(
                text.split("\n"),
                widget.default_accent,
                issue_key,
                show_button=True   # 🔥 ВКЛЮЧАЕМ КНОПКУ
            )

            widget.setItemWidget(item, card)

    def fill_work_bubbles(self, widget: BubbleGridWidget, issues: list[dict[str, Any]]) -> None:
        widget.clear()

        if not issues:
            item = QListWidgetItem()
            item.setSizeHint(QSize(260, 90))
            widget.addItem(item)
            empty_card = self.build_issue_card_widget(
                ["Сейчас пусто"],
                "#68FFC0",
            )
            widget.setItemWidget(item, empty_card)
            return

        for issue in issues:
            fields = issue.get("fields", {}) or {}
            issue_key = issue.get("key", "UNKNOWN")
            summary = trim_text(fields.get("summary") or "Без темы", 54)
            status_name = self.get_status_name(fields)
            region_name = self.tray_app.client.extract_region(fields)
            author_name = self.tray_app.client.extract_author(fields)

            accent = self.work_issue_accent(fields)

            lines = [
                issue_key,
                summary,
                f"{region_name} • {status_name}",
                f"Автор: {author_name}",
            ]

            tooltip = (
                f"{issue_key} | {summary} | "
                f"Регион: {region_name} | Статус: {status_name} | Автор: {author_name}"
            )

            item = QListWidgetItem()
            item.setData(Qt.UserRole, self.tray_app.build_issue_url(issue_key))
            item.setToolTip(tooltip)
            item.setSizeHint(QSize(245, 104))
            widget.addItem(item)

            card = self.build_issue_card_widget(lines, accent, issue_key, show_button=False)
            widget.setItemWidget(item, card)

    def build_issue_card_widget(self, text_lines, accent, issue_key=None, show_button=True):
        accent = accent or "#6EA8FF"
        soft_bg = "rgba(12, 17, 30, 230)"
        if accent.lower() in {"#00ffa6", "#68ffc0"}:
            soft_bg = "rgba(14, 28, 24, 236)"
        elif accent.lower() == "#6b7280":
            soft_bg = "rgba(24, 26, 32, 230)"

        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background: {soft_bg};
                border: 1px solid {accent};
                border-radius: 16px;
            }}
        """)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 10, 10)
        layout.setSpacing(5)

        for index, line in enumerate(text_lines):
            label = QLabel(line)
            label.setWordWrap(True)

            if index == 0:
                label.setStyleSheet("color:#FFFFFF;font-size:14px;font-weight:800;")
            elif line.startswith("Автор:"):
                label.setStyleSheet("color:#9FB0D8;font-size:12px;")
            else:
                label.setStyleSheet("color:#DCE6FF;font-size:12px;")

            layout.addWidget(label)

        # 👉 КНОПКА
        if issue_key and show_button:
            btn = QPushButton("Взять")
            btn.setFixedHeight(26)
            btn.setCursor(QCursor(Qt.PointingHandCursor))

            btn.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 rgba(71, 149, 255, 0.30),
                        stop:1 rgba(104, 255, 214, 0.28));
                    color: #E7F4FF;
                    border: 1px solid rgba(126,183,255,0.85);
                    border-radius: 8px;
                    font-size: 11px;
                    font-weight: 800;
                    padding: 2px 8px;
                }
                QPushButton:hover {
                    background: rgba(89, 172, 255, 0.45);
                }
            """)

            btn.clicked.connect(lambda: self.tray_app.take_issue(issue_key))

            bottom = QHBoxLayout()
            bottom.addStretch()
            bottom.addWidget(btn)

            layout.addLayout(bottom)

        frame.setLayout(layout)
        return frame

    def work_issue_accent(self, fields):
        status = self.get_status_name(fields).lower()

        if "отлож" in status:
            return "#6B7280"   # серый

        if "работ" in status:
            return "#00FFA6"   # яркий (в работе)

        return "#68FFC0"       # дефолт

    def format_red_issue(self, issue: dict[str, Any]) -> tuple[str, str]:
        fields = issue.get("fields", {}) or {}
        issue_key = issue.get("key", "UNKNOWN")
        summary = trim_text(fields.get("summary") or "Без темы", 62)
        status_name = self.get_status_name(fields)
        assignee_name = self.get_assignee_name(fields)
        text = f"{issue_key}\n{summary}\n{status_name} • {assignee_name}"
        tooltip = f"{issue_key} | {summary} | Статус: {status_name} | Исполнитель: {assignee_name}"
        return text, tooltip

    def format_blue_issue(self, issue: dict[str, Any]) -> tuple[str, str]:
        fields = issue.get("fields", {}) or {}
        issue_key = issue.get("key", "UNKNOWN")
        summary = trim_text(fields.get("summary") or "Без темы", 62)
        status_name = self.get_status_name(fields)
        region_name = self.tray_app.client.extract_region(fields)
        text = f"{issue_key}\n{summary}\n{region_name} • {status_name}"
        tooltip = f"{issue_key} | {summary} | Регион: {region_name} | Статус: {status_name}"
        return text, tooltip

    @staticmethod
    def get_status_name(fields: dict[str, Any]) -> str:
        status_obj = fields.get("status") or {}
        return str(status_obj.get("name") or "Без статуса")

    @staticmethod
    def get_assignee_name(fields: dict[str, Any]) -> str:
        assignee = fields.get("assignee")
        if not assignee:
            return "Не назначен"
        if isinstance(assignee, dict):
            return str(
                assignee.get("displayName")
                or assignee.get("name")
                or assignee.get("emailAddress")
                or "Назначен"
            )
        return "Назначен"


class TrayApp:
    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    def classify_issue_category(self, fields: dict[str, Any]) -> str:
        region = self.client.extract_region(fields).lower()
        if "ковров" in region:
            return "Ковров"
        if region and region != "не указан":
            return "Регионы"
        return "Прочее"

    def get_request_type(self, issue: dict[str, Any]) -> str:
        base_url = self.config.get("base_url", "").strip()
        token = self.config.get("token", "").strip()
        if not base_url or not token:
            return "Не указан"
        return self.client.extract_request_type(base_url, token, issue)

    @staticmethod
    def _tokenize_summary(text: str) -> set[str]:
        stopwords = {
            "и", "или", "для", "что", "как", "это", "при", "надо", "нужно",
            "the", "and", "for", "with", "from", "user", "jira",
        }
        tokens = set()
        for raw in text.lower().replace("/", " ").replace("-", " ").split():
            token = "".join(ch for ch in raw if ch.isalnum())
            if len(token) >= 4 and token not in stopwords:
                tokens.add(token)
        return tokens

    def _find_similar_issue_key(self, issue: dict[str, Any]) -> str:
        fields = issue.get("fields", {}) or {}
        summary = str(fields.get("summary") or "")
        source_tokens = self._tokenize_summary(summary)
        if len(source_tokens) < 2:
            return ""

        candidates = self.current_red_issues + self.current_blue_issues + self.current_work_issues
        issue_key = str(issue.get("key") or "")
        best_key = ""
        best_score = 0.0
        for candidate in candidates:
            candidate_key = str(candidate.get("key") or "")
            if not candidate_key or candidate_key == issue_key:
                continue
            c_fields = candidate.get("fields", {}) or {}
            c_summary = str(c_fields.get("summary") or "")
            candidate_tokens = self._tokenize_summary(c_summary)
            if not candidate_tokens:
                continue
            intersection = source_tokens & candidate_tokens
            if len(intersection) < 2:
                continue
            score = len(intersection) / max(1, min(len(source_tokens), len(candidate_tokens)))
            if score > best_score:
                best_score = score
                best_key = candidate_key
        return best_key if best_score >= 0.6 else ""

    def _track_daily_created_seen(self, issues: list[dict[str, Any]]) -> None:
        first_seen_map = self.analytics.get("first_seen_by_key", {})
        daily_created_seen = self.analytics.get("daily_created_seen", {})
        now = datetime.now()
        today_key = now.strftime("%Y-%m-%d")

        for issue in issues:
            issue_key = str(issue.get("key") or "").strip()
            if not issue_key or issue_key in first_seen_map:
                continue
            first_seen_map[issue_key] = now.isoformat()
            daily_created_seen[today_key] = self._safe_int(daily_created_seen.get(today_key, 0)) + 1

        self.analytics["first_seen_by_key"] = first_seen_map
        self.analytics["daily_created_seen"] = daily_created_seen

    def _track_unassigned_alerts(self, issues: list[dict[str, Any]]) -> None:
        threshold_minutes = self._safe_int(self.config.get("unassigned_alert_minutes", 30))
        alerted = self.alerted_unassigned_keys
        now = datetime.now()

        for issue in issues:
            issue_key = str(issue.get("key") or "").strip()
            if not issue_key or issue_key in alerted:
                continue
            fields = issue.get("fields", {}) or {}
            if fields.get("assignee"):
                continue
            region = self.client.extract_region(fields)
            if "ковров" not in region.lower():
                continue

            created_raw = str(fields.get("created") or "").strip()
            if not created_raw:
                continue
            try:
                created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                continue
            age_minutes = int((now - created_dt).total_seconds() // 60)
            if age_minutes < threshold_minutes:
                continue

            self.show_qt_message(APP_TITLE, f"🔥 КОВРОВ {issue_key} без исполнителя {age_minutes} мин")
            self.logger.warning(f"{issue_key} без исполнителя {age_minutes} мин (регион: {region})")
            alerted.add(issue_key)

    def _record_completed_analytics(self, issues: list[dict[str, Any]]) -> None:
        existing_records = self.analytics.get("completed_records", [])
        existing_ids = {
            f"{item.get('key', '')}|{item.get('resolved', '')}"
            for item in existing_records
            if isinstance(item, dict)
        }

        added = 0
        daily_closed = self.analytics.get("daily_closed", {})
        first_seen_map = self.analytics.get("first_seen_by_key", {})
        for issue in issues:
            issue_key = str(issue.get("key") or "").strip()
            fields = issue.get("fields", {}) or {}
            resolved = str(fields.get("resolved") or "").strip()
            if not issue_key or not resolved:
                continue

            record_id = f"{issue_key}|{resolved}"
            if record_id in existing_ids:
                continue

            category = self.classify_issue_category(fields)
            status = self.dashboard.get_status_name(fields)
            region = self.client.extract_region(fields)
            author = self.client.extract_author(fields)
            request_type = self.get_request_type(issue)
            created = str(fields.get("created") or "").strip()
            reaction_minutes = None
            first_seen_raw = str(first_seen_map.get(issue_key) or "").strip()
            if created and first_seen_raw:
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    first_seen_dt = datetime.fromisoformat(first_seen_raw)
                    reaction_minutes = int((first_seen_dt - created_dt).total_seconds() // 60)
                except Exception:
                    reaction_minutes = None

            try:
                resolved_date = datetime.fromisoformat(resolved.replace("Z", "+00:00")).strftime("%Y-%m-%d")
                daily_closed[resolved_date] = self._safe_int(daily_closed.get(resolved_date, 0)) + 1
            except Exception:
                pass

            existing_records.append(
                {
                    "key": issue_key,
                    "resolved": resolved,
                    "category": category,
                    "status": status,
                    "region": region,
                    "author": author,
                    "request_type": request_type,
                    "created": created,
                    "first_seen": first_seen_raw,
                    "reaction_minutes": reaction_minutes,
                }
            )
            existing_ids.add(record_id)
            added += 1

        if added:
            self.analytics["completed_records"] = existing_records
            self.analytics["daily_closed"] = daily_closed
            self.logger.info(f"Аналитика completed обновлена, добавлено без дублей: {added}")
            self.signals.analytics_updated.emit(self.analytics)

    def _jira_assignee_payload(self) -> dict[str, str]:
        base_url = self.config.get("base_url", "").rstrip("/")
        token = self.config.get("token", "").strip()
        if not base_url or not token:
            raise ValueError("Не заполнены base_url/token")

        url = f"{base_url}/rest/api/2/myself"
        response = self.client.session.get(url, headers=self.client._headers(token), timeout=20)
        if not response.ok:
            raise RequestException(f"Не удалось определить текущего пользователя: HTTP {response.status_code}")

        me_json = response.json()
        me = me_json if isinstance(me_json, dict) else {}
        account_id = str(me.get("accountId") or "").strip()
        user_name = str(me.get("name") or "").strip()
        user_key = str(me.get("key") or "").strip()

        if account_id:
            return {"accountId": account_id}
        if user_name:
            return {"name": user_name}
        if user_key:
            return {"name": user_key}
        return {"name": "-1"}

    @staticmethod
    def _find_in_progress_transition_id(transitions: list[dict[str, Any]]) -> str:
        prioritized_names = [
            "в работу",
            "выполняется",
            "в процессе",
            "in progress",
            "start progress",
            "начать работу",
        ]

        normalized: list[tuple[str, str]] = []
        for item in transitions:
            if not isinstance(item, dict):
                continue
            transition_id = str(item.get("id") or "").strip()
            transition_name = str(item.get("name") or "").strip()
            if not transition_id or not transition_name:
                continue
            normalized.append((transition_id, transition_name.lower()))

        for preferred_name in prioritized_names:
            for transition_id, transition_name in normalized:
                if preferred_name in transition_name:
                    return transition_id
        return ""

    def _move_issue_to_in_progress(self, issue_key: str) -> bool:
        base_url = self.config.get("base_url", "").rstrip("/")
        token = self.config.get("token", "").strip()
        transitions_url = f"{base_url}/rest/api/2/issue/{issue_key}/transitions"

        response = self.client.session.get(
            transitions_url,
            headers=self.client._headers(token),
            timeout=20,
        )
        if not response.ok:
            raise RequestException(f"Не удалось получить переходы: HTTP {response.status_code}")

        response_data = response.json()
        data = response_data if isinstance(response_data, dict) else {}
        transitions = data.get("transitions", [])
        if not isinstance(transitions, list):
            transitions = []

        transition_id = self._find_in_progress_transition_id(transitions)
        if not transition_id:
            self.logger.warning(f"Для {issue_key} не найден переход в 'В работе'")
            return False

        payload = {"transition": {"id": transition_id}}
        transition_response = self.client.session.post(
            transitions_url,
            json=payload,
            headers=self.client._headers(token),
            timeout=20,
        )
        if transition_response.status_code not in (200, 204):
            raise RequestException(
                f"Не удалось перевести задачу в работу: HTTP {transition_response.status_code}: {transition_response.text}"
            )
        return True

    def take_issue(self, issue_key: str):
        try:
            base_url = self.config.get("base_url", "").rstrip("/")
            token = self.config.get("token", "").strip()
            if not base_url or not token:
                raise ValueError("Заполни URL и токен в настройках")

            url = f"{base_url}/rest/api/2/issue/{issue_key}/assignee"
            payload = self._jira_assignee_payload()

            response = self.client.session.put(
                url,
                json=payload,
                headers=self.client._headers(token),
                timeout=20,
            )

            if response.status_code in (200, 204):
                moved_to_work = self._move_issue_to_in_progress(issue_key)
                if moved_to_work:
                    self.logger.info(f"{issue_key} назначена и переведена в работу")
                    self.show_qt_message("Jira", f"{issue_key} назначена и переведена в работу")
                else:
                    self.logger.info(f"{issue_key} назначена, но переход в работу не найден")
                    self.show_qt_message("Jira", f"{issue_key} назначена (переход в работу не найден)")
                self.analytics["taken_count"] = self._safe_int(self.analytics.get("taken_count", 0)) + 1
                self.signals.analytics_updated.emit(self.analytics)

                # обновляем список
                self.run_check(force_notify=True)
            else:
                raise Exception(f"HTTP {response.status_code}: {response.text}")

        except Exception as e:
            self.logger.error(f"Ошибка взятия {issue_key}: {e}")
            self.show_qt_message("Ошибка", str(e))

    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.config = load_config()
        self.state = load_state()
        self.client = JiraClient()
        self.signals = AppSignals()

        self.known_red: set[str] = set(self.state.get("known_red", []))
        self.known_blue: set[str] = set(self.state.get("known_blue", []))
        self.known_work: set[str] = set(self.state.get("known_work", []))

        self.current_red_keys: set[str] = set(self.state.get("current_red_keys", []))
        self.current_blue_keys: set[str] = set(self.state.get("current_blue_keys", []))
        self.current_work_keys: set[str] = set(self.state.get("current_work_keys", []))

        self.current_red_issues: list[dict[str, Any]] = []
        self.current_blue_issues: list[dict[str, Any]] = []
        self.current_work_issues: list[dict[str, Any]] = []

        self.last_check_time: str = self.state.get("last_check_time", "")
        self.last_error: str = self.state.get("last_error", "")
        analytics_state = self.state.get("analytics", {}) if isinstance(self.state.get("analytics"), dict) else {}
        self.analytics: dict[str, Any] = {
            "taken_count": int(analytics_state.get("taken_count", 0)),
            "new_red_count": int(analytics_state.get("new_red_count", 0)),
            "new_blue_count": int(analytics_state.get("new_blue_count", 0)),
            "new_work_count": int(analytics_state.get("new_work_count", 0)),
            "completed_records": list(analytics_state.get("completed_records", [])),
            "first_seen_by_key": dict(analytics_state.get("first_seen_by_key", {})),
            "daily_created_seen": dict(analytics_state.get("daily_created_seen", {})),
            "daily_closed": dict(analytics_state.get("daily_closed", {})),
        }
        self.duplicate_hints_shown: set[str] = set(self.state.get("duplicate_hints_shown", []))
        self.alerted_unassigned_keys: set[str] = set(self.state.get("alerted_unassigned_keys", []))

        self._check_in_progress = False
        self._field_map_loaded = False

        if APP_ICON_ICO.exists():
            self.icon = QIcon(str(APP_ICON_ICO))
        elif RED_ICON_PATH.exists():
            self.icon = QIcon(str(RED_ICON_PATH))
        elif BLUE_ICON_PATH.exists():
            self.icon = QIcon(str(BLUE_ICON_PATH))
        else:
            self.icon = QIcon()

        print("APP_ICON_ICO =", APP_ICON_ICO)
        print("RED_ICON_PATH =", RED_ICON_PATH)
        print("BLUE_ICON_PATH =", BLUE_ICON_PATH)
        print("tray icon isNull =", self.icon.isNull())

        self.tray_icon = QSystemTrayIcon(self.icon, self.app)
        self.tray_icon.setIcon(self.icon)
        self.tray_icon.setToolTip(APP_TITLE)

        self.dashboard = DashboardWindow(self)
        self.settings_window = SettingsWindow(self)
        self.log_window = LogWindow(self)
        self.completed_window = CompletedWindow(self)

        self.setup_logging()
        self.setup_signals()
        self.setup_tray()

        self.timer = QTimer()
        self.timer.timeout.connect(self.on_timer_tick)

        self.apply_config()
        self.emit_stats()
        self.signals.red_issues_updated.emit(self.current_red_issues)
        self.signals.blue_issues_updated.emit(self.current_blue_issues)
        self.signals.work_issues_updated.emit(self.current_work_issues)
        self.signals.monitoring_changed.emit(bool(self.config.get("enabled", True)))
        self.signals.analytics_updated.emit(self.analytics)

        self.show_dashboard()
        self.show_qt_message(APP_TITLE, "Приложение запущено")
        self.logger.info("Приложение запущено")

    def setup_logging(self) -> None:
        self.logger = logging.getLogger("jira-fast-watcher")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)

        self.memory_log_handler = MemoryLogHandler(self.signals)
        self.memory_log_handler.setFormatter(formatter)

        self.logger.addHandler(console_handler)
        self.logger.addHandler(self.memory_log_handler)

    def setup_signals(self) -> None:
        self.signals.log_message.connect(self.log_window.append_log)
        self.signals.tray_tooltip.connect(self.tray_icon.setToolTip)
        self.signals.qt_message.connect(self._show_qt_message_slot)
        self.signals.monitoring_changed.connect(self.dashboard.update_monitoring_button)
        self.signals.stats_updated.connect(self.dashboard.update_stats)
        self.signals.red_issues_updated.connect(self.dashboard.update_red_issues)
        self.signals.blue_issues_updated.connect(self.dashboard.update_blue_issues)
        self.signals.work_issues_updated.connect(self.dashboard.update_work_issues)
        self.signals.completed_issues_loaded.connect(self.completed_window.update_issues)
        self.signals.analytics_updated.connect(self.completed_window.update_analytics)

    def show_main_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def quit_app(self):
        self.tray.hide()
        QApplication.quit()

    def on_tray_click(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.show_main_window()

    def setup_tray(self) -> None:
        self.menu = QMenu()

        action_open = QAction("Открыть дашборд", self.menu)
        action_open.triggered.connect(self.show_dashboard)
        self.menu.addAction(action_open)

        action_settings = QAction("Настройки", self.menu)
        action_settings.triggered.connect(self.show_settings)
        self.menu.addAction(action_settings)

        action_check = QAction("Проверить сейчас", self.menu)
        action_check.triggered.connect(lambda: self.run_check(force_notify=True))
        self.menu.addAction(action_check)

        action_completed = QAction("Выполненные", self.menu)
        action_completed.triggered.connect(self.show_completed_window)
        self.menu.addAction(action_completed)

        action_logs = QAction("Логи", self.menu)
        action_logs.triggered.connect(self.show_log_window)
        self.menu.addAction(action_logs)

        self.menu.addSeparator()

        action_exit = QAction("Выход", self.menu)
        action_exit.triggered.connect(self.app.quit)
        self.menu.addAction(action_exit)

        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()


    def persist_state(self) -> None:
        state = {
            "known_red": sorted(self.known_red),
            "known_blue": sorted(self.known_blue),
            "known_work": sorted(self.known_work),
            "current_red_keys": sorted(self.current_red_keys),
            "current_blue_keys": sorted(self.current_blue_keys),
            "current_work_keys": sorted(self.current_work_keys),
            "last_check_time": self.last_check_time,
            "last_error": self.last_error,
            "analytics": self.analytics,
            "duplicate_hints_shown": sorted(self.duplicate_hints_shown),
            "alerted_unassigned_keys": sorted(self.alerted_unassigned_keys),
        }
        save_state(state)

    def apply_config(self) -> None:
        interval_ms = max(5, int(self.config.get("interval_seconds", 10))) * 1000

        if self.config.get("enabled", True):
            self.timer.start(interval_ms)
            self.logger.info(f"Таймер запущен, интервал: {interval_ms // 1000} сек")
        else:
            self.timer.stop()
            self.logger.info("Мониторинг выключен")

        self.update_tray_tooltip()
        self.signals.monitoring_changed.emit(bool(self.config.get("enabled", True)))

    def ensure_field_map_loaded(self) -> None:
        if self._field_map_loaded:
            return

        token = self.config.get("token", "").strip()
        base_url = self.config.get("base_url", "").strip()
        if not token or not base_url:
            return

        self.client.fetch_fields(base_url, token)
        self.client.fetch_request_types(base_url, token)
        self._field_map_loaded = True
        self.logger.info("Карта полей Jira загружена")

    def build_issue_url(self, issue_key: str) -> str:
        base_url = self.config.get("base_url", "").rstrip("/")
        return f"{base_url}/browse/{issue_key}"

    def update_tray_tooltip(self) -> None:
        state = "ON" if self.config.get("enabled", True) else "OFF"
        interval = int(self.config.get("interval_seconds", 10))
        tooltip = (
            f"{APP_TITLE} [{state}] — "
            f"Ковров: {len(self.current_red_keys)} — "
            f"Регионы: {len(self.current_blue_keys)} — "
            f"В работе: {len(self.current_work_keys)} — "
            f"{interval}с"
        )
        self.signals.tray_tooltip.emit(tooltip)

    def emit_stats(self) -> None:
        self.signals.stats_updated.emit(
            len(self.current_red_keys),
            len(self.current_blue_keys),
            len(self.current_work_keys),
            self.last_check_time,
            self.last_error,
        )

    def show_dashboard(self) -> None:
        self.dashboard.show()
        self.dashboard.raise_()
        self.dashboard.activateWindow()

    def show_settings(self) -> None:
        self.settings_window.load_into_form()
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def show_log_window(self) -> None:
        self.log_window.hydrate_from_memory()
        self.log_window.show()
        self.log_window.raise_()
        self.log_window.activateWindow()

    def show_completed_window(self) -> None:
        self.completed_window.show()
        self.completed_window.raise_()
        self.completed_window.activateWindow()
        self.load_completed_issues()

    def load_completed_issues(self) -> None:
        token = self.config.get("token", "").strip()
        base_url = self.config.get("base_url", "").strip()
        completed_jql = self.config.get("completed_jql", "").strip()

        if not token or not base_url or not completed_jql:
            self.show_qt_message(APP_TITLE, "Заполни completed_jql в настройках")
            return

        self.logger.info("Загрузка выполненных задач")

        def worker() -> None:
            try:
                self.client.fetch_request_types(base_url, token)
                issues = self.client.fetch_issues(base_url, token, completed_jql)
                issues = self.sort_completed_issues(issues)
                self._record_completed_analytics(issues)
                self.signals.completed_issues_loaded.emit(issues)
                self.logger.info(f"Загружено выполненных задач: {len(issues)}")
            except RequestException as e:
                self.logger.error(f"Ошибка загрузки выполненных задач: {e}")
                self.show_qt_message(APP_TITLE, f"Ошибка загрузки выполненных: {e}")
            except Exception as e:
                self.logger.exception(f"Неожиданная ошибка при загрузке выполненных: {e}")
                self.show_qt_message(APP_TITLE, f"Ошибка выполненных: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.show_dashboard()

    def show_qt_message(self, title: str, text: str) -> None:
        self.signals.qt_message.emit(title, text)

    def handle_toast_action(self, argument: str) -> None:
        value = str(argument or "").strip()
        if not value:
            return
        if value.startswith("take:"):
            issue_key = value.split(":", 1)[1].strip()
            if issue_key:
                self.take_issue(issue_key)
            return
        webbrowser.open(value)

    @Slot(str, str)
    def _show_qt_message_slot(self, title: str, text: str) -> None:
        self.tray_icon.showMessage(title, text, QSystemTrayIcon.Information, 4000)

    def notify_issue(self, issue: dict[str, Any], is_red: bool) -> None:
        issue_key = issue.get("key", "UNKNOWN")
        fields = issue.get("fields", {}) or {}
        summary = trim_text(fields.get("summary") or "Новая заявка", 140)
        issue_url = self.build_issue_url(issue_key)
        region = self.client.extract_region(fields)
        is_kovrov = "ковров" in region.lower()

        if is_kovrov:
            title = f"🔥 {issue_key}"
        else:
            title = f"{'🔴⚡' if is_red else '🔵'} {issue_key}"

        try:
            win_toast(
                title,
                summary,
                app_id="JiraFastWatcher4",
                duration="long",
                actions=[
                    {
                        "content": "Открыть",
                        "arguments": issue_url
                    },
                    {
                        "content": "Взять в работу",
                        "arguments": f"take:{issue_key}"
                    }
                ],
                on_click=self.handle_toast_action,
            )

        except Exception as e:
            self.logger.error(f"Toast error: {e}")

    def on_timer_tick(self) -> None:
        self.run_check(force_notify=False)

    @staticmethod
    def sort_issues_newest_first(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def created_key(issue: dict[str, Any]) -> str:
            fields = issue.get("fields", {}) or {}
            return str(fields.get("created") or "")
        return sorted(issues, key=created_key, reverse=True)

    @staticmethod
    def sort_completed_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def resolved_key(issue: dict[str, Any]) -> str:
            fields = issue.get("fields", {}) or {}
            return str(fields.get("resolved") or "")
        return sorted(issues, key=resolved_key, reverse=True)

    def run_check(self, force_notify: bool = False) -> None:
        if self._check_in_progress:
            self.logger.info("Проверка пропущена: предыдущая ещё идёт")
            return

        token = self.config.get("token", "").strip()
        base_url = self.config.get("base_url", "").strip()
        red_jql = self.config.get("red_jql", "").strip()
        blue_jql = self.config.get("blue_jql", "").strip()
        work_jql = self.config.get("work_jql", "").strip()

        if not token or not base_url or not red_jql or not blue_jql or not work_jql:
            self.logger.warning("Не заполнены URL, токен или один из JQL")
            self.show_qt_message(APP_TITLE, "Заполни URL, токен и все JQL в настройках")
            return

        if not self.config.get("enabled", True) and not force_notify:
            self.logger.info("Проверка пропущена: мониторинг выключен")
            return

        self._check_in_progress = True
        self.logger.info("Запуск проверки Jira")

        def worker() -> None:
            try:
                self.ensure_field_map_loaded()

                red_issues = self.client.fetch_issues(base_url, token, red_jql)
                blue_issues_raw = self.client.fetch_issues(base_url, token, blue_jql)
                work_issues = self.client.fetch_issues(base_url, token, work_jql)

                red_keys_raw = {issue.get("key") for issue in red_issues if issue.get("key")}
                blue_issues = [
                    issue
                    for issue in blue_issues_raw
                    if issue.get("key") and issue.get("key") not in red_keys_raw
                ]

                red_issues = self.sort_issues_newest_first(red_issues)
                blue_issues = self.sort_issues_newest_first(blue_issues)
                work_issues = self.sort_issues_newest_first(work_issues)

                red_keys_now = {issue.get("key") for issue in red_issues if issue.get("key")}
                blue_keys_now = {issue.get("key") for issue in blue_issues if issue.get("key")}
                work_keys_now = {issue.get("key") for issue in work_issues if issue.get("key")}

                new_red = [issue for issue in red_issues if issue.get("key") not in self.known_red]
                new_blue = [issue for issue in blue_issues if issue.get("key") not in self.known_blue]
                new_work = [issue for issue in work_issues if issue.get("key") not in self.known_work]
                self._track_daily_created_seen(new_red + new_blue + new_work)
                self._track_unassigned_alerts(red_issues)

                self.analytics["new_red_count"] = self._safe_int(self.analytics.get("new_red_count", 0)) + len(new_red)
                self.analytics["new_blue_count"] = self._safe_int(self.analytics.get("new_blue_count", 0)) + len(new_blue)
                self.analytics["new_work_count"] = self._safe_int(self.analytics.get("new_work_count", 0)) + len(new_work)

                self.current_red_keys = red_keys_now
                self.current_blue_keys = blue_keys_now
                self.current_work_keys = work_keys_now

                self.current_red_issues = red_issues
                self.current_blue_issues = blue_issues
                self.current_work_issues = work_issues

                self.last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.last_error = ""

                if force_notify:
                    self.logger.info("Режим force_notify включён")
                    for issue in red_issues:
                        self.notify_issue(issue, is_red=True)
                    for issue in blue_issues:
                        self.notify_issue(issue, is_red=False)
                else:
                    for issue in new_red:
                        self.notify_issue(issue, is_red=True)

                    if self.known_blue:
                        for issue in new_blue:
                            self.notify_issue(issue, is_red=False)
                    else:
                        self.logger.info("Первичная инициализация BLUE без уведомлений")

                self.known_red |= red_keys_now
                self.known_blue |= blue_keys_now
                self.known_work |= work_keys_now

                for issue in new_red + new_blue:
                    issue_key = str(issue.get("key") or "").strip()
                    if not issue_key or issue_key in self.duplicate_hints_shown:
                        continue
                    similar_key = self._find_similar_issue_key(issue)
                    if similar_key:
                        self.logger.info(f"{issue_key}: уже есть похожая проблема {similar_key}")
                        self.show_qt_message(APP_TITLE, f"{issue_key}: уже есть похожая проблема {similar_key}")
                        self.duplicate_hints_shown.add(issue_key)

                self.logger.info(
                    f"Найдено RED: {len(red_issues)}, BLUE: {len(blue_issues)}, WORK: {len(work_issues)}"
                )

            except RequestException as e:
                self.last_error = str(e)
                self.logger.error(f"Ошибка Jira API: {e}")
                self.show_qt_message(APP_TITLE, f"Ошибка Jira API: {e}")
            except Exception as e:
                self.last_error = str(e)
                self.logger.exception(f"Неожиданная ошибка: {e}")
                self.show_qt_message(APP_TITLE, f"Ошибка: {e}")
            finally:
                self._check_in_progress = False
                self.update_tray_tooltip()
                self.persist_state()
                self.emit_stats()
                self.signals.red_issues_updated.emit(self.current_red_issues)
                self.signals.blue_issues_updated.emit(self.current_blue_issues)
                self.signals.work_issues_updated.emit(self.current_work_issues)
                self.signals.analytics_updated.emit(self.analytics)

        threading.Thread(target=worker, daemon=True).start()


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    tray_app = TrayApp(app)
    app.tray_app = tray_app

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
