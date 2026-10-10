import ctypes
import sys

_MUTEX_HANDLE = None   # 持有互斥体句柄，防止被垃圾回收后提前释放


def _ensure_single_instance() -> bool:
    """单实例守卫：返回 True 表示本进程可以继续启动。
    已有实例在运行时，把它的主窗口恢复并置前，返回 False 让本进程退出。"""
    global _MUTEX_HANDLE
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _MUTEX_HANDLE = k32.CreateMutexW(None, False, r"Local\DownloadsTool.SingleInstance")
    if ctypes.get_last_error() != 183:    # ERROR_ALREADY_EXISTS
        return True

    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "多线程下载工具")
    if hwnd:
        # 关闭按钮是最小化到托盘（hide），窗口被系统隐藏但仍存在，重新显示即可
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)    # SW_RESTORE：从最小化恢复
        else:
            user32.ShowWindow(hwnd, 8)    # SW_SHOWNA：从托盘隐藏中恢复，不抢焦点
        user32.SetForegroundWindow(hwnd)
    else:
        user32.MessageBoxW(None, "多线程下载工具已在运行，请查看任务栏或系统托盘。",
                           "提示", 0x40)
    return False


def main():
    if not _ensure_single_instance():
        return

    from . import config
    config.init(sys.argv[1:])

    try:
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
