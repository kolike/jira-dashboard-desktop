from datetime import datetime
from typing import Any
import webbrowser

from PySide6.QtCore import Qt, QSize, Slot
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QComboBox,
    QLineEdit,
    QWidget,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QListWidgetItem,
)

from storage import APP_TITLE, trim_text
from ui_dashboard import BubbleGridWidget


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
                str(issue.get("_region") or self.tray_app.client.extract_region(issue.get("fields", {}) or {}))
                for issue in self.all_issues
            }
        )
        request_types = sorted(
            {
                str(issue.get("_request_type") or self.tray_app.get_request_type(issue))
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
            author = str(issue.get("_author") or self.tray_app.client.extract_author(fields))
            category = str(issue.get("_category") or self.tray_app.classify_issue_category(fields))
            status_name = str(issue.get("_status") or self._status_name(fields))
            region_name = str(issue.get("_region") or self.tray_app.client.extract_region(fields))
            request_type = str(issue.get("_request_type") or self.tray_app.get_request_type(issue))

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
            author_name = str(issue.get("_author") or self.tray_app.client.extract_author(fields))

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
            category = str(issue.get("_category") or self.tray_app.classify_issue_category(issue.get("fields", {}) or {}))
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
            request_type = str(issue.get("_request_type") or self.tray_app.get_request_type(issue))
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
