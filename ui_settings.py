from typing import Any
import json
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
    QSpinBox,
    QTextEdit,
)

from storage import APP_TITLE, BASE_DIR, DEFAULT_CONFIG, exportable_config, save_config


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


