import webbrowser
from typing import Any

from PySide6.QtCore import Qt, QSize, Slot
from PySide6.QtGui import QAction, QColor, QCursor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from storage import APP_TITLE, save_config, trim_text


class NeonCard(QFrame):
    def __init__(self, title: str, accent: str) -> None:
        super().__init__()
        self.setObjectName("NeonCard")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.setGraphicsEffect(shadow)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")
        self.title_label.setProperty("accent", accent)

        self.count_label = QLabel("0")
        self.count_label.setObjectName("CardCount")
        self.count_label.setAlignment(Qt.AlignCenter)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_row.addWidget(self.title_label)
        title_row.addStretch()
        title_row.addWidget(self.count_label)

        self.body_layout = QVBoxLayout()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(10)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        layout.addLayout(title_row)
        layout.addLayout(self.body_layout)
        self.setLayout(layout)

        self.setStyleSheet(
            f"""
            QFrame#NeonCard {{
                background: #111827;
                border: 1px solid #263244;
                border-top: 2px solid {accent};
                border-radius: 8px;
            }}
            QLabel#CardTitle {{
                color: #F8FAFC;
                font-size: 14px;
                font-weight: 800;
                letter-spacing: 0px;
                padding-left: 2px;
                background: transparent;
                border: none;
            }}
            QLabel#CardCount {{
                min-width: 28px;
                max-width: 42px;
                min-height: 22px;
                max-height: 22px;
                color: #E2E8F0;
                background: #172033;
                border: 1px solid #334155;
                border-radius: 8px;
                font-size: 12px;
                font-weight: 900;
            }}
            """
        )

    def set_count(self, value: int) -> None:
        self.count_label.setText(str(value))


class MetricCard(QFrame):
    def __init__(self, label_text: str, accent: str) -> None:
        super().__init__()

        self.label = QLabel(label_text)
        self.value = QLabel("—")

        self.label.setObjectName("MetricLabel")
        self.value.setObjectName("MetricValue")
        self.value.setWordWrap(True)
        self.value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setMinimumHeight(64)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)
        layout.addWidget(self.label)
        layout.addWidget(self.value)
        self.setLayout(layout)

        self.setStyleSheet(
            f"""
            QFrame {{
                background: #0F172A;
                border: 1px solid #263244;
                border-left: 3px solid {accent};
                border-radius: 8px;
            }}
            QLabel#MetricLabel {{
                color: #94A3B8;
                font-size: 11px;
                font-weight: 700;
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
            }}
            QLabel#MetricValue {{
                color: #F8FAFC;
                font-size: 18px;
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
            border = "#2DD4BF"
            bg = "#12343A"
            color = "#ECFEFF"
            glow_color = QColor(0, 0, 0, 75)
        else:
            border = "#334155"
            bg = "#111827"
            color = "#CBD5E1"
            glow_color = QColor(0, 0, 0, 55)

        self.setStyleSheet(
            f"""
            QToolButton {{
                background: {bg};
                color: {color};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QToolButton:hover {{
                border: 1px solid #38BDF8;
                background: #172033;
            }}
            """
        )

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(14 if self._active else 10)
        shadow.setOffset(0, 5)
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
                background: #0B1120;
                border: none;
                outline: none;
                color: #F8FAFC;
                font-size: 12px;
                padding: 6px;
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
                background: #334155;
                border-radius: 4px;
                min-height: 20px;
            }}

            QScrollBar::handle:vertical:hover {{
                background: #475569;
            }}

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)



class DashboardWindow(QWidget):
    def __init__(self, tray_app: "TrayApp") -> None:
        super().__init__()
        self.tray_app = tray_app
        self.setFont(QFont("Segoe UI", 10))

        self.setWindowTitle(APP_TITLE)
        self.resize(1180, 760)
        self.setMinimumSize(1080, 700)

        self.setStyleSheet(
            """
            QWidget {
                background: #0B1020;
                color: #F8FAFC;
                font-family: "Segoe UI", Tahoma, Arial, sans-serif;
            }
            QLabel {
                background: transparent;
                border: none;
            }
            QLabel#MainTitle {
                color: #F8FAFC;
                font-size: 23px;
                font-weight: 900;
                letter-spacing: 0px;
                background: transparent;
                border: none;
            }
            QLabel#SubTitle {
                color: #94A3B8;
                font-size: 12px;
                font-weight: 600;
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
                background: #111827;
                color: #F8FAFC;
                border: 1px solid #263244;
                border-radius: 8px;
                padding: 6px;
            }
            QMenu::item {
                padding: 8px 20px 8px 12px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background: #1E293B;
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
        header_layout.setContentsMargins(12, 10, 12, 10)
        header_layout.setSpacing(10)
        header_layout.addLayout(title_layout)
        header_layout.addStretch()
        header_layout.addLayout(controls_layout)

        header_panel = QFrame()
        header_panel.setObjectName("HeaderPanel")
        header_panel.setLayout(header_layout)
        header_panel.setStyleSheet(
            """
            QFrame#HeaderPanel {
                background: #111827;
                border: 1px solid #263244;
                border-radius: 8px;
            }
            """
        )

        self.metric_red = MetricCard("КОВРОВ", "#F43F5E")
        self.metric_blue = MetricCard("РЕГИОНЫ", "#38BDF8")
        self.metric_work = MetricCard("В РАБОТЕ", "#22C55E")
        self.metric_last = MetricCard("ПОСЛЕДНЯЯ ПРОВЕРКА", "#F59E0B")
        self.metric_error = MetricCard("ПОСЛЕДНЯЯ ОШИБКА", "#FB7185")

        metrics_layout = QHBoxLayout()
        metrics_layout.setSpacing(8)
        metrics_layout.addWidget(self.metric_red)
        metrics_layout.addWidget(self.metric_blue)
        metrics_layout.addWidget(self.metric_work)
        metrics_layout.addWidget(self.metric_last)
        metrics_layout.addWidget(self.metric_error)

        self.red_card = NeonCard("КОВРОВ", "#F43F5E")
        self.blue_card = NeonCard("РЕГИОНЫ", "#38BDF8")
        self.work_card = NeonCard("В РАБОТЕ", "#22C55E")

        self.red_list = BubbleGridWidget("#F43F5E", QSize(255, 104))
        self.blue_list = BubbleGridWidget("#38BDF8", QSize(255, 104))
        self.work_list = BubbleGridWidget("#22C55E", QSize(285, 132))
        self.red_list.setMinimumHeight(250)
        self.blue_list.setMinimumHeight(250)
        self.work_list.setMinimumHeight(250)

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
        root_layout.setContentsMargins(16, 14, 16, 16)
        root_layout.setSpacing(12)
        root_layout.addWidget(header_panel)
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
        self.red_card.set_count(len(issues))
        self.fill_bubbles(self.red_list, issues, self.format_red_issue)

    @Slot(list)
    def update_blue_issues(self, issues: list[dict[str, Any]]) -> None:
        self.blue_card.set_count(len(issues))
        self.fill_bubbles(self.blue_list, issues, self.format_blue_issue)

    @Slot(list)
    def update_work_issues(self, issues: list[dict[str, Any]]) -> None:
        self.work_card.set_count(len(issues))
        self.fill_work_bubbles(self.work_list, issues)

    @Slot(bool)
    def update_monitoring_button(self, is_enabled: bool) -> None:
        self.monitor_button.set_active(is_enabled)

    def fill_bubbles(self, widget: BubbleGridWidget, issues: list[dict[str, Any]], formatter) -> None:
        widget.clear()

        if not issues:
            item = QListWidgetItem("Сейчас пусто")
            item.setSizeHint(QSize(260, 92))
            widget.addItem(item)
            empty_card = self.build_issue_card_widget(
                [item.text()],
                "#64748B",
                show_button=False,
            )
            widget.setItemWidget(item, empty_card)
            return

        for issue in issues:
            issue_key = issue.get("key", "UNKNOWN")
            text, tooltip = formatter(issue)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, self.tray_app.build_issue_url(issue_key))
            item.setSizeHint(QSize(260, 104))
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
                "#64748B",
            )
            widget.setItemWidget(item, empty_card)
            return

        for issue in issues:
            fields = issue.get("fields", {}) or {}
            issue_key = issue.get("key", "UNKNOWN")
            summary = trim_text(fields.get("summary") or "Без темы", 44)
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
            item.setSizeHint(QSize(285, 126))
            widget.addItem(item)

            card = self.build_issue_card_widget(lines, accent, issue_key, show_button=False)
            widget.setItemWidget(item, card)

    def build_issue_card_widget(self, text_lines, accent, issue_key=None, show_button=True):
        accent = accent or "#6EA8FF"
        accent_key = accent.lower()
        soft_bg = "#111827"
        text_muted = "#94A3B8"
        if accent_key in {"#22c55e", "#00ffa6", "#68ffc0"}:
            soft_bg = "#0F241B"
            text_muted = "#86EFAC"
        elif accent_key in {"#64748b", "#6b7280"}:
            soft_bg = "#111827"
            text_muted = "#94A3B8"

        frame = QFrame()
        frame.setObjectName("IssueCard")
        shadow = QGraphicsDropShadowEffect(frame)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 70))
        frame.setGraphicsEffect(shadow)
        frame.setStyleSheet(f"""
            QFrame#IssueCard {{
                background: {soft_bg};
                border: 1px solid #263244;
                border-left: 3px solid {accent};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 9, 10, 10)
        layout.setSpacing(4)

        for index, line in enumerate(text_lines):
            label = QLabel(line)
            label.setWordWrap(True)

            if index == 0:
                label.setStyleSheet("color:#F8FAFC;font-size:14px;font-weight:900;")
            elif line.startswith("Автор:"):
                label.setStyleSheet(f"color:{text_muted};font-size:12px;")
            else:
                label.setStyleSheet("color:#CBD5E1;font-size:12px;")

            layout.addWidget(label)

        # 👉 КНОПКА
        if issue_key and show_button:
            btn = QPushButton("Взять")
            btn.setFixedHeight(26)
            btn.setCursor(QCursor(Qt.PointingHandCursor))

            btn.setStyleSheet("""
                QPushButton {
                    background: #2563EB;
                    color: #FFFFFF;
                    border: 1px solid #3B82F6;
                    border-radius: 7px;
                    font-size: 11px;
                    font-weight: 800;
                    padding: 2px 10px;
                }
                QPushButton:hover {
                    background: #1D4ED8;
                    border: 1px solid #60A5FA;
                }
            """)

            btn.clicked.connect(lambda: self.tray_app.take_issue(issue_key))

            bottom = QHBoxLayout()
            bottom.setContentsMargins(0, 4, 0, 2)
            bottom.addStretch()
            bottom.addWidget(btn)

            layout.addLayout(bottom)

        frame.setLayout(layout)
        return frame

    def work_issue_accent(self, fields):
        status = self.get_status_name(fields).lower()

        if "отлож" in status:
            return "#64748B"   # серый

        if "работ" in status:
            return "#22C55E"   # яркий (в работе)

        return "#64748B"       # дефолт

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
