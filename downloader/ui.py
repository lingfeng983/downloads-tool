import os
import subprocess
import sys

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QFont, QBrush, QIcon
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QFileDialog, QMessageBox, QAbstractItemView,
    QSystemTrayIcon, QStyle, QFrame, QPlainTextEdit, QMenu
)

from . import config
from .info import TaskStatus
from .manager import DownloadManager
from .util import format_size, format_speed, resource_path
from .worker import AddWorker

STATUS_COLOR = {
    TaskStatus.DOWNLOADING: QColor("#2f855a"),
    TaskStatus.COMPLETED: QColor("#2b6cb0"),
    TaskStatus.PAUSED: QColor("#b7791f"),
    TaskStatus.ERROR: QColor("#c53030"),
    TaskStatus.CANCELLED: QColor("#718096"),
    TaskStatus.WAITING: QColor("#4a5568"),
}

COLS = ["文件名", "大小", "进度", "速度", "线程", "状态", "操作"]

# 数字列用等宽字体，行间的位数对齐更整齐
NUM_FONT = QFont("Consolas", 10)

STYLE = """
* { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; }
QMainWindow, QWidget { background: #f3f5f9; color: #1f2937; }
QFrame#Card { background: #ffffff; border: 1px solid #e5e9f0; border-radius: 10px; }
QLabel { color: #475569; background: transparent; }
QLineEdit, QPlainTextEdit, QComboBox {
    background: #ffffff; border: 1px solid #d5dce6; border-radius: 6px;
    padding: 5px 8px; selection-background-color: #3b82f6;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid #3b82f6; }
QComboBox::drop-down { border: none; width: 22px; }
QPushButton {
    background: #ffffff; border: 1px solid #d5dce6; border-radius: 6px;
    padding: 6px 14px; color: #334155;
}
QPushButton:hover { background: #eef2f7; }
QPushButton:pressed { background: #e2e8f0; }
QPushButton#Primary { background: #3b82f6; border: none; color: white;
    font-weight: bold; padding: 7px 24px; }
QPushButton#Primary:hover { background: #2f6fe0; }
QPushButton#Primary:disabled { background: #a8c6f5; color: #f0f6ff; }
QPushButton#ToggleOn { background: #e6f0ff; border: 1px solid #3b82f6; color: #1d4ed8; }
QPushButton#Row, QPushButton#RowPrimary, QPushButton#RowDanger {
    padding: 3px 12px; font-size: 12px; border-radius: 5px; }
QPushButton#RowPrimary { background: #e6f0ff; border: 1px solid #b8d3fb; color: #1d4ed8; }
QPushButton#RowPrimary:hover { background: #d3e5fd; }
QPushButton#RowDanger { background: #fff5f5; border: 1px solid #f3c3c3; color: #dc2626; }
QPushButton#RowDanger:hover { background: #fee2e2; }
QTableWidget {
    background: #ffffff; border: 1px solid #e5e9f0; border-radius: 10px;
    gridline-color: #eef1f6; alternate-background-color: #f8fafc;
    outline: 0;
}
QTableWidget::item { padding: 2px 4px; border-right: 1px solid #f0f3f8; }
QTableWidget::item:hover { background: #f0f6ff; }
QTableWidget::item:selected { background: #e6f0ff; color: #1e3a8a; }
QHeaderView { background: transparent; border: none; }
QHeaderView::section {
    background: #f1f5f9; border: none; border-bottom: 2px solid #dbe3ec;
    border-right: 1px solid #e3e9f1;
    padding: 10px 6px; font-weight: bold; color: #334155;
}
QHeaderView::section:last { border-right: none; }
QProgressBar {
    border: none; background: #e8edf3; border-radius: 7px;
    color: #334155; font-size: 11px; text-align: center;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #60a5fa, stop:1 #3b82f6); border-radius: 7px;
}
QStatusBar { background: #eef2f7; color: #475569; }
QMenuBar { background: #f3f5f9; }
QMenu { background: #ffffff; border: 1px solid #d5dce6; }
QMenu::item { padding: 6px 24px; }
QMenu::item:selected { background: #e6f0ff; color: #1d4ed8; }
"""

# 状态列的浅色徽章背景
STATUS_BG = {
    TaskStatus.DOWNLOADING: "#e7f6ec",
    TaskStatus.COMPLETED: "#e8f1fd",
    TaskStatus.PAUSED: "#fdf3e0",
    TaskStatus.ERROR: "#fdeaea",
    TaskStatus.CANCELLED: "#eef0f3",
    TaskStatus.WAITING: "#eceff4",
}


class MainWindow(QMainWindow):
    done_sig = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.mgr = DownloadManager.instance()
        self.mgr.load_state()
        self.mgr.start_monitor()
        self.done_sig.connect(self._handle_done)
        self.mgr.set_on_task_done(self.done_sig.emit)
        self.row_by_url: dict[str, int] = {}
        self.setWindowTitle("多线程下载工具")
        self.setWindowIcon(QIcon(resource_path("icon.png")))
        self.resize(1000, 620)
        self.setStyleSheet(STYLE)
        self._build_ui()
        self._refresh = QTimer(self, interval=500, timeout=self._update_table)
        self._refresh.start()
        self._worker = AddWorker(self.mgr)
        self._worker.done.connect(self._on_added)
        self._worker.finished.connect(self._on_batch_finished)
        self.tray = self._make_tray()
        self._quitting = False
        self._tray_hint_shown = False
        app = QApplication.instance()
        app.aboutToQuit.connect(self._persist_options)
        app.aboutToQuit.connect(self.mgr.shutdown)
        self._build_menu()

    def _build_menu(self):
        menu = self.menuBar().addMenu("设置(&S)")
        act = menu.addAction("配置文件保存位置…")
        act.triggered.connect(self._choose_data_dir)
        menu.addAction("打开配置文件夹", lambda: self._reveal(config.DATA_DIR))

    def _choose_data_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择配置文件保存位置（任务列表与设置将存放到此目录）",
            config.DATA_DIR)
        if not d or os.path.abspath(d) == config.DATA_DIR:
            return
        config.apply_data_dir(d)          # 写指针文件，下次启动自动沿用
        self.mgr.load_state()             # 若新目录已有历史任务则合并显示
        self.mgr.update_settings()
        QMessageBox.information(
            self, "完成",
            f"配置文件将保存到:\n{config.DATA_DIR}\n\n"
            "该位置已记住；启动时加 --data-dir 参数可临时覆盖。")

    # ---------- UI ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 10, 14, 0)
        root.setSpacing(10)

        # ---- 顶部：添加任务卡片 ----
        card = QFrame()
        card.setObjectName("Card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(16, 12, 16, 12)
        card_lay.setSpacing(8)

        s = self.mgr.settings
        self.url_edit = QPlainTextEdit()
        self.url_edit.setPlaceholderText("粘贴 http/https 下载链接，每行一个，可同时下载多个文件")
        self.url_edit.setFixedHeight(58)
        card_lay.addWidget(self.url_edit)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        dir_row.addWidget(QLabel("保存目录"))
        self.dir_edit = QLineEdit(s.get("save_dir") or config.DEFAULT_SETTINGS["save_dir"])
        dir_row.addWidget(self.dir_edit, 1)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)
        dir_row.addWidget(browse)
        card_lay.addLayout(dir_row)

        opt_row = QHBoxLayout()
        opt_row.setSpacing(8)
        opt_row.addWidget(QLabel("线程数"))
        self.thread_combo = QComboBox()
        self.thread_combo.addItems(["1", "2", "4", "8", "16"])
        self.thread_combo.setCurrentText(str(s.get("thread_count", 8)))
        self.thread_combo.setMinimumWidth(64)
        opt_row.addWidget(self.thread_combo)
        opt_row.addWidget(QLabel("限速"))
        self.limit_combo = QComboBox()
        self.limit_combo.addItem("不限速", 0)
        for kb in (64, 128, 256, 512):
            self.limit_combo.addItem(f"{kb} KB/s", kb * 1024)
        for mb in (1, 2, 5, 10, 20, 50):
            self.limit_combo.addItem(f"{mb} MB/s", mb * 1024 * 1024)
        idx = self.limit_combo.findData(s.get("speed_limit", 0))
        self.limit_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.limit_combo.setMinimumWidth(108)
        opt_row.addWidget(self.limit_combo)
        self.auto_open = QPushButton("完成后打开目录")
        self.auto_open.setCheckable(True)
        self.auto_open.setChecked(bool(s.get("auto_open", False)))
        self.auto_open.toggled.connect(self._sync_auto_open_text)
        self._sync_auto_open_text(self.auto_open.isChecked())
        opt_row.addWidget(self.auto_open)
        opt_row.addStretch()
        self.start_btn = QPushButton("开始下载")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self._start_download)
        opt_row.addWidget(self.start_btn)
        card_lay.addLayout(opt_row)
        root.addWidget(card)

        # ---- 任务列表 ----
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignCenter)
        h.setHighlightSections(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i, w in enumerate([0, 172, 200, 105, 80, 88, 165]):
            if i:
                self.table.setColumnWidth(i, w)
        root.addWidget(self.table, 1)

        self.status_bar = self.statusBar()
        self.status_bar.showMessage("就绪")

    def _sync_auto_open_text(self, on: bool):
        self.auto_open.setText("✓ 完成后打开目录" if on else "完成后打开目录")
        self.auto_open.setObjectName("ToggleOn" if on else "")
        self.auto_open.style().unpolish(self.auto_open)
        self.auto_open.style().polish(self.auto_open)

    def _make_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        icon = QIcon(resource_path("icon.png"))
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.SP_ArrowDown)
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip("多线程下载工具")
        menu = QMenu()
        menu.addAction("显示主界面", self._show_from_tray)
        menu.addSeparator()
        menu.addAction("退出", self._quit)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_from_tray()

    def _quit(self):
        self._quitting = True
        if self.tray:
            self.tray.hide()
        self.close()

    def closeEvent(self, event):
        # 关闭时最小化到托盘；仅托盘不可用或用户主动退出时才真正关闭
        if self.tray and not self._quitting:
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.showMessage(
                    "已最小化到托盘", "程序仍在后台下载，双击托盘图标可恢复窗口",
                    QSystemTrayIcon.Information, 3000)
            return
        super().closeEvent(event)

    # ---------- actions ----------

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def _persist_options(self):
        self.mgr.update_settings(
            save_dir=self.dir_edit.text().strip(),
            thread_count=int(self.thread_combo.currentText()),
            speed_limit=self.limit_combo.currentData() or 0,
            auto_open=self.auto_open.isChecked())

    def _start_download(self):
        lines = [ln.strip() for ln in
                 self.url_edit.toPlainText().splitlines() if ln.strip()]
        urls = list(dict.fromkeys(lines))          # 去重且保持顺序
        if not urls:
            QMessageBox.warning(self, "提示", "请输入下载链接")
            return
        bad = [u for u in urls if not u.startswith(("http://", "https://"))]
        if bad:
            QMessageBox.warning(self, "提示",
                                "以下链接不是有效的 HTTP/HTTPS 地址：\n" + "\n".join(bad))
            return
        save_dir = self.dir_edit.text().strip() or config.DEFAULT_SETTINGS["save_dir"]
        self.dir_edit.setText(save_dir)
        self._persist_options()
        self.mgr.set_speed_limit(self.limit_combo.currentData() or 0)
        self.start_btn.setEnabled(False)
        self.status_bar.showMessage(f"正在解析 {len(urls)} 个链接…")
        threads = int(self.thread_combo.currentText())
        self._worker.add_batch(urls, save_dir, threads)

    @pyqtSlot(str, str)
    def _on_added(self, url, name):
        self.status_bar.showMessage(f"已开始下载: {name}")

    @pyqtSlot(int, str)
    def _on_batch_finished(self, ok_count, errors):
        self.start_btn.setEnabled(True)
        if ok_count:
            self.url_edit.clear()
        if errors:
            QMessageBox.critical(self, "部分下载添加失败",
                                 f"成功 {ok_count} 个，失败:\n{errors}")
        else:
            self.status_bar.showMessage(f"已添加 {ok_count} 个下载任务")

    # ---------- table refresh ----------

    def _update_table(self):
        snaps = self.mgr.snapshots()
        current = {s["url"] for s in snaps}
        for url in list(self.row_by_url):
            if url not in current:
                self.row_by_url.pop(url)
        if len(self.row_by_url) < len(snaps):
            self._rebuild_index(snaps)
        self.table.clearContents()
        self.table.setRowCount(len(snaps))
        for r, s in enumerate(snaps):
            self._fill_row(r, s)
        total = self.mgr.total_speed()
        active = sum(1 for s in snaps if s["status"] == TaskStatus.DOWNLOADING)
        self.status_bar.showMessage(
            f"进行中: {active}   总速度: {format_speed(total)}   "
            f"限速: {self.limit_combo.currentText()}")

    def _rebuild_index(self, snaps):
        self.row_by_url = {s["url"]: i for i, s in enumerate(snaps)}

    def _fill_row(self, r, s):
        name = QTableWidgetItem(s["file_name"])
        name.setToolTip(s["url"])
        name.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        name.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        self.table.setItem(r, 0, name)

        size_item = QTableWidgetItem(
            f"{format_size(s['downloaded'])} / {format_size(s['total_size'])}")
        size_item.setTextAlignment(Qt.AlignCenter)
        size_item.setFont(NUM_FONT)
        self.table.setItem(r, 1, size_item)

        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setValue(int(s["progress"] * 1000))
        bar.setTextVisible(True)
        bar.setFormat(f"{s['progress'] * 100:.1f}%")
        bar.setFixedHeight(14)
        bar_wrap = QWidget()
        bl = QHBoxLayout(bar_wrap)
        bl.setContentsMargins(10, 0, 10, 0)
        bl.addStretch()
        bl.addWidget(bar)
        bl.addStretch()
        self.table.setCellWidget(r, 2, bar_wrap)

        speed_item = QTableWidgetItem(format_speed(s["speed"]))
        speed_item.setTextAlignment(Qt.AlignCenter)
        speed_item.setFont(NUM_FONT)
        self.table.setItem(r, 3, speed_item)

        threads = s["thread_count"] if s["support_range"] else 1
        th_item = QTableWidgetItem("单线程" if threads <= 1 else f"{threads} 线程")
        th_item.setTextAlignment(Qt.AlignCenter)
        th_item.setForeground(QColor("#64748b"))
        self.table.setItem(r, 4, th_item)

        status = s["status"]
        st_item = QTableWidgetItem(status.value)
        st_item.setTextAlignment(Qt.AlignCenter)
        st_item.setForeground(STATUS_COLOR.get(status, QColor("#000")))
        bg = STATUS_BG.get(status)
        if bg:
            st_item.setBackground(QBrush(QColor(bg)))
        f = QFont()
        f.setBold(True)
        st_item.setFont(f)
        if s["error"]:
            st_item.setToolTip(s["error"])
        self.table.setItem(r, 5, st_item)

        self.table.setCellWidget(r, 6, self._action_widget(s))

    def _action_widget(self, s) -> QWidget:
        url = s["url"]
        status = s["status"]
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignCenter)

        def btn(text, slot, obj="Row"):
            b = QPushButton(text)
            b.setObjectName(obj)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            return b

        if status == TaskStatus.DOWNLOADING:
            lay.addWidget(btn("暂停", lambda: self.mgr.pause(url), "RowPrimary"))
        elif status == TaskStatus.PAUSED:
            lay.addWidget(btn("继续", lambda: self.mgr.resume(url), "RowPrimary"))
        elif status in (TaskStatus.ERROR, TaskStatus.CANCELLED, TaskStatus.WAITING):
            lay.addWidget(btn("重试", lambda: self.mgr.retry(url), "RowPrimary"))
        elif status == TaskStatus.COMPLETED:
            lay.addWidget(btn("打开目录", lambda: self._open_dir(url)))
        if status != TaskStatus.COMPLETED:
            lay.addWidget(btn("取消", lambda: self._cancel(url), "RowDanger"))
        else:
            lay.addWidget(btn("移除", lambda: self._cancel(url), "RowDanger"))
        return w

    def _cancel(self, url):
        if QMessageBox.question(
                self, "确认",
                "确定移除该任务吗？将删除其所有临时分片目录及未完成的残留文件。") \
                == QMessageBox.Yes:
            self.mgr.cancel(url)
        self._update_table()

    def _open_dir(self, url):
        task = self.mgr.get(url)
        if task:
            self._reveal(task.info.save_path)

    # ---------- completion ----------

    @pyqtSlot(str)
    def _handle_done(self, path):
        name = os.path.basename(path)
        if self.tray:
            self.tray.showMessage("下载完成", name, QSystemTrayIcon.Information, 5000)
        if self.auto_open.isChecked():
            self._open_folder(os.path.dirname(path))

    @staticmethod
    def _open_folder(folder):
        folder = os.path.normpath(folder)
        if sys.platform == "win32":
            os.startfile(folder)            # 直接打开对应目录
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, folder])

    @staticmethod
    def _reveal(path):
        folder = os.path.dirname(path)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", folder])
