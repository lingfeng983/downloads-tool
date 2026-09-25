import sys


def main():
    from . import config
    config.init(sys.argv[1:])

    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QIcon
    from .ui import MainWindow
    from .util import resource_path

    app = QApplication(sys.argv[:1])
    app.setApplicationName("DownloadsTool")
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(resource_path("icon.png")))
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
