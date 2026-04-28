import ctypes
import sys

from PySide6.QtWidgets import QApplication

from tray_app import TrayApp

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("jira.fast.watcher")
except Exception:
    pass


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    tray_app = TrayApp(app)
    app.tray_app = tray_app

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
