import logging
import sys
import threading
import webbrowser
from datetime import datetime
from typing import Any

from requests.exceptions import RequestException
from PySide6.QtCore import QObject, Signal, Slot, Qt, QTimer
from PySide6.QtGui import QTextCursor, QCursor, QIcon, QAction
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QTextEdit,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QSystemTrayIcon,
    QMenu,
)

try:
    from win11toast import toast as win_toast
except ImportError:
    from win11toast import notify as win_toast

from jira_client import JiraClient
from analytics import overlap_score, tokenize_summary
from storage import (
    APP_ICON_ICO,
    APP_TITLE,
    BASE_DIR,
    BLUE_ICON_PATH,
    RED_ICON_PATH,
    load_config,
    load_state,
    save_config,
    save_state,
    trim_text,
)
from ui_completed import CompletedWindow
from ui_dashboard import DashboardWindow
from ui_settings import SettingsWindow

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

    def _enrich_completed_issues(self, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for issue in issues:
            fields = issue.get("fields", {}) or {}
            request_type = self.get_request_type(issue)
            region = self.client.extract_region(fields)
            author = self.client.extract_author(fields)
            status = self.dashboard.get_status_name(fields)
            category = self.classify_issue_category(fields)

            issue["_request_type"] = request_type
            issue["_region"] = region
            issue["_author"] = author
            issue["_status"] = status
            issue["_category"] = category
            enriched.append(issue)
        return enriched

    def _find_similar_issue_key(self, issue: dict[str, Any]) -> str:
        fields = issue.get("fields", {}) or {}
        summary = str(fields.get("summary") or "")
        source_tokens = tokenize_summary(summary)
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
            candidate_tokens = tokenize_summary(c_summary)
            if not candidate_tokens:
                continue
            intersection = source_tokens & candidate_tokens
            if len(intersection) < 2:
                continue
            score = overlap_score(source_tokens, candidate_tokens)
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

            category = str(issue.get("_category") or self.classify_issue_category(fields))
            status = str(issue.get("_status") or self.dashboard.get_status_name(fields))
            region = str(issue.get("_region") or self.client.extract_region(fields))
            author = str(issue.get("_author") or self.client.extract_author(fields))
            request_type = str(issue.get("_request_type") or self.get_request_type(issue))
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

        file_handler = logging.FileHandler(str(BASE_DIR / "jira_fast_watcher.log"), encoding="utf-8")
        file_handler.setFormatter(formatter)

        self.memory_log_handler = MemoryLogHandler(self.signals)
        self.memory_log_handler.setFormatter(formatter)

        self.logger.addHandler(console_handler)
        self.logger.addHandler(file_handler)
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
                issues = self._enrich_completed_issues(issues)
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
        lowered = value.lower()
        if lowered in {"http:взять в работу", "взять в работу"}:
            return
        if "take:" in lowered:
            issue_key = value.split("take:", 1)[1].strip()
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
            payload = {
                "app_id": "JiraFastWatcher4",
                "duration": "long",
                "buttons": [
                    {
                        "activationType": "protocol",
                        "content": "Открыть",
                        "arguments": issue_url,
                    },
                    {
                        "activationType": "protocol",
                        "content": "Взять в работу",
                        "arguments": f"take:{issue_key}",
                    }
                ],
                "on_click": self.handle_toast_action,
                "on_action": self.handle_toast_action,
            }
            try:
                win_toast(title, summary, **payload)
            except TypeError:
                # fallback для старых версий win11toast, где используется actions
                payload.pop("buttons", None)
                payload["actions"] = [
                    {"content": "Открыть", "arguments": issue_url},
                    {"content": "Взять в работу", "arguments": f"take:{issue_key}"},
                ]
                win_toast(title, summary, **payload)

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
