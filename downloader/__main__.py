import sys


def main():
    from . import config
    config.init(sys.argv[1:])

    try:
        import ctypes
        # 任务栏分组用独立 AppUserModelID：源码运行时避免任务栏沿用 python.exe
        # 的默认图标；打包 exe 则确保与应用图标一致
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DownloadsTool")
        # 高 DPI 感知必须在 QApplication 实例化前声明：否则 Windows 在 >100% 缩放的
        # 显示器上对窗口做位图拉伸，布局右缘（保存目录一行右侧的按钮）会被裁掉
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor aware
    except OSError:
        pass                                              # 进程已声明过（如 python.exe 带 manifest）
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    from PyQt5.QtGui import QIcon
    from .ui import MainWindow
    from .util import resource_path

    app = QApplication(sys.argv[:1])
    app.setApplicationName("DownloadsTool")
    app.setStyle("Fusion")
    # 多尺寸 ico 让任务栏/标题栏/Alt-Tab 在各尺寸下都取到清晰的应用图标
    icon = QIcon(resource_path("icon.ico"))
    if icon.isNull():
        icon = QIcon(resource_path("icon.png"))
    app.setWindowIcon(icon)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
