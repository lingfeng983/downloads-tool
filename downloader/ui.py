import os
import subprocess
import sys
import threading
import time

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QRectF
from PyQt5.QtGui import QColor, QFont, QBrush, QIcon, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QFileDialog, QMessageBox, QAbstractItemView,
    QSystemTrayIcon, QStyle, QFrame, QPlainTextEdit, QMenu, QDialog,
    QDialogButtonBox, QToolButton
)

from . import config
from .info import TaskStatus
from .manager import DownloadManager
from .update import CHECK_INTERVAL, UpdateWorker, is_frozen_exe
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

COLS = ["文件名", "大小", "进度", "速度", "线程速度", "状态", "操作"]

# 数字列用等宽字体，行间的位数对齐更整齐
NUM_FONT = QFont("Consolas", 10)
BAR_FONT = QFont("Consolas", 8)

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
QPushButton#Mini, QToolButton#Mini {
    padding: 5px 10px; font-size: 12px; border-radius: 6px;
    background: #ffffff; border: 1px solid #d5dce6; color: #334155; }
QToolButton#Mini:hover { background: #eef2f7; }
/* 原生菜单指示箭头会溢出按钮边框（与文字中的"▾"重复），按 id 单选器隐藏 */
QToolButton#Mini::menu-indicator { image: none; width: 0; height: 0; }
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
QDialog { background: #f3f5f9; }
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

SPEED_IDLE = QColor("#dde3ea")
SPEED_DONE = QColor("#86efac")


def speed_color(ratio: float) -> QColor:
    """按相对速度插值：慢=浅蓝，快=深蓝"""
    ratio = max(0.0, min(1.0, ratio))
    r = int(0xba + (0x1d - 0xba) * ratio)
    g = int(0xd7 + (0x4e - 0xd7) * ratio)
    b = int(0xfd + (0xd8 - 0xfd) * ratio)
    return QColor(r, g, b)


class ThreadSpeedWidget(QWidget):
    """把每个分片下载线程画成一根柱子：柱高 = 该线程速度 / 最快线程速度。
    悬停显示单个线程详情，点击展开完整线程列表。"""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chunks: list[dict] = []
        self._declared = 1        # 用户设定的线程数（未开始下载时用）
        self._hover = -1
        self.setMinimumHeight(34)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("")

    def set_state(self, chunks: list[dict], declared: int):
        self._chunks = chunks or []
        self._declared = max(1, declared)
        self._sync_tooltip()
        self.update()

    # ---------- 绘制 ----------

    def paintEvent(self, _event):
        n = max(len(self._chunks), self._declared if not self._chunks else 0)
        n = max(n, 1)
        w = self.width()
        h = self.height()
        label_h = 13
        chart_h = max(h - label_h - 2, 6)

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setFont(BAR_FONT)

        slots = self._slots(n, w)
        if not self._chunks:
            # 尚未开始：用等高灰柱表示计划的线程数
            p.setPen(Qt.NoPen)
            p.setBrush(SPEED_IDLE)
            for i, x in enumerate(slots[:-1]):
                bw = (slots[i + 1] - x - 2) if i + 1 < len(slots) else (w - x - 1)
                p.drawRoundedRect(QRectF(x, 4, max(bw, 2.0), chart_h - 4), 2, 2)
        else:
            top = max((c["speed"] for c in self._chunks), default=0.0)
            p.setPen(Qt.NoPen)
            for i, c in enumerate(self._chunks):
                x = slots[i]
                bw = (slots[i + 1] - x - 2) if i + 1 < len(slots) else (w - x - 1)
                if c["finished"]:
                    p.setBrush(SPEED_DONE)
                    bh = max(3.0, chart_h * 0.16)
                elif c["speed"] <= 0:
                    p.setBrush(SPEED_IDLE)
                    bh = 3.0
                else:
                    p.setBrush(speed_color(c["speed"] / top if top > 0 else 0))
                    bh = max(4.0, chart_h * (c["speed"] / top))
                y = 4 + (chart_h - 4) - bh
                if i == self._hover:
                    p.setPen(QPen(QColor("#1d4ed8"), 1))
                    p.setBrush(speed_color(0.95))
                p.drawRoundedRect(QRectF(x, y, max(bw, 2.0), bh), 2, 2)
                p.setPen(Qt.NoPen)

        # 底部文字：线程数 + 完成数 + 最快线程速度
        if self._chunks:
            done = sum(1 for c in self._chunks if c["finished"])
            top = max((c["speed"] for c in self._chunks), default=0.0)
            text = f"{len(self._chunks)}线程" + (f" · {done}完成" if done else "")
            p.setPen(QColor("#64748b"))
            p.drawText(QRectF(0, h - label_h, w, label_h),
                       Qt.AlignLeft | Qt.AlignVCenter, text)
            if top > 0:
                p.setPen(QColor("#1d4ed8"))
                p.drawText(QRectF(0, h - label_h, w, label_h),
                           Qt.AlignRight | Qt.AlignVCenter, format_speed(top))
        else:
            p.setPen(QColor("#94a3b8"))
            p.drawText(QRectF(0, h - label_h, w, label_h),
                       Qt.AlignLeft | Qt.AlignVCenter, f"{self._declared}线程")
        p.end()

    @staticmethod
    def _slots(n: int, width: int) -> list[float]:
        """每根柱子的 x 起点，最后一个元素为右边界"""
        step = width / n
        return [i * step for i in range(n)] + [float(width)]

    # ---------- 交互 ----------

    def _slot_at(self, x: int) -> int:
        n = max(len(self._chunks), 1)
        if x < 0 or x > self.width():
            return -1
        return min(int(x / max(self.width() / n, 1)), n - 1)

    def _chunk_text(self, c: dict) -> str:
        state = "已完成" if c["finished"] else ("传输中" if c["speed"] > 0 else "等待/停滞")
        seg = "未知大小" if c["total"] <= 0 else \
            f"{format_size(c['done'])} / {format_size(c['total'])}"
        return (f"线程 {c['cid'] + 1} · {state}\n"
                f"速度: {format_speed(c['speed'])}\n"
                f"分片: {seg} ({c['percent']:.1f}%)")

    def _sync_tooltip(self):
        if not self._chunks:
            self.setToolTip(f"计划 {self._declared} 个下载线程\n点击查看各线程速度")
        else:
            self.setToolTip("\n".join(self._chunk_text(c) for c in self._chunks)
                            + "\n\n点击查看各线程速度")

    def mouseMoveEvent(self, e):
        self._hover = self._slot_at(int(e.x()))
        if 0 <= self._hover < len(self._chunks):
            self.setToolTip(self._chunk_text(self._chunks[self._hover]))
        else:
            self._sync_tooltip()
        self.update()

    def leaveEvent(self, _e):
        self._hover = -1
        self._sync_tooltip()
        self.update()

    def mouseReleaseEvent(self, _e):
        self.clicked.emit()


class ThreadDetailDialog(QDialog):
    """单个任务的全部下载线程明细：速度、进度、状态"""

    COLS = ["线程", "状态", "速度", "分片进度", "已下载 / 分片大小"]

    def __init__(self, win: "MainWindow", url: str, parent=None):
        super().__init__(parent)
        self.win = win
        self.url = url
        self.setWindowTitle("线程速度明细")
        self.resize(660, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        self.head = QLabel()
        self.head.setStyleSheet("font-weight: bold; color: #334155;")
        lay.addWidget(self.head)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setShowGrid(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        for i, w in enumerate([60, 78, 100, 150]):
            self.table.setColumnWidth(i, w)
        lay.addWidget(self.table, 1)
        self._bars: dict[int, QProgressBar] = {}   # 复用进度条，避免每次 tick 重建

        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

        self._timer = QTimer(self, interval=700, timeout=self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self):
        task = self.win.mgr.get(self.url)
        if task is None:
            self._timer.stop()
            self.reject()
            return
        snap = task.snapshot()
        chunks = snap["chunks"]
        if not chunks:
            self.head.setText(f"{snap['file_name']} — 尚未启动下载")
            self.table.setRowCount(0)
            return
        live = sum(1 for c in chunks if not c["finished"])
        self.head.setText(
            f"{snap['file_name']} — 共 {len(chunks)} 个线程，"
            f"{live} 个传输中，总速度 {format_speed(snap['speed'])}")
        if len(chunks) != self.table.rowCount():
            self.table.setRowCount(len(chunks))
            # setRowCount 缩行时 Qt 会连同 cellWidget 一起删除旧进度条，
            # 缓存里留下失效的 C++ 包装对象，下次 .get(r) 取到就 setValue 崩溃
            self._bars.clear()
        for r, c in enumerate(chunks):
            state = "已完成" if c["finished"] else ("传输中" if c["speed"] > 0 else "等待/停滞")
            state_item = QTableWidgetItem(state)
            state_item.setTextAlignment(Qt.AlignCenter)
            state_item.setForeground(QColor(
                "#16a34a" if c["finished"] else
                ("#2f855a" if c["speed"] > 0 else "#94a3b8")))
            self.table.setItem(r, 0, QTableWidgetItem(f"#{c['cid'] + 1}"))
            self.table.item(r, 0).setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 1, state_item)

            sp = QTableWidgetItem(format_speed(c["speed"]))
            sp.setTextAlignment(Qt.AlignCenter)
            sp.setFont(NUM_FONT)
            self.table.setItem(r, 2, sp)

            # 进度条按行缓存复用：原先每 700ms new QProgressBar + new QWidget，
            # 对话框长时间开着会持续累积 Qt 对象
            bar = self._bars.get(r)
            if bar is None:
                bar = QProgressBar()
                bar.setRange(0, 1000)
                bar.setTextVisible(False)
                bar.setFixedHeight(12)
                wrap = QWidget()
                bl = QHBoxLayout(wrap)
                bl.setContentsMargins(8, 0, 8, 0)
                bl.addWidget(bar)
                self._bars[r] = bar
                self.table.setCellWidget(r, 3, wrap)
            bar.setValue(int(c["percent"] * 10))

            seg = "未知" if c["total"] <= 0 else \
                f"{format_size(c['done'])} / {format_size(c['total'])}"
            sz = QTableWidgetItem(seg)
            sz.setFont(NUM_FONT)
            self.table.setItem(r, 4, sz)


class RowActions(QWidget):
    """操作列：两个常驻按钮，状态变化时只改文案，不重建控件（避免每 500ms 卡顿）"""

    clicked = pyqtSignal(str)      # "primary" / "danger"

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignCenter)
        self.primary = self._btn("RowPrimary", "primary")
        self.danger = self._btn("RowDanger", "danger")
        lay.addWidget(self.primary)
        lay.addWidget(self.danger)
        self._signature = None

    def _btn(self, obj: str, tag: str) -> QPushButton:
        b = QPushButton()
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda _=False, t=tag: self.clicked.emit(t))
        self._apply(b, "", obj)
        return b

    @staticmethod
    def _apply(btn: QPushButton, text: str, obj: str):
        btn.setText(text)
        if btn.objectName() != obj:
            btn.setObjectName(obj)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_status(self, status: TaskStatus):
        if status == TaskStatus.DOWNLOADING:
            sig = ("暂停", "取消", "RowPrimary", "RowDanger")
        elif status == TaskStatus.PAUSED:
            sig = ("继续", "取消", "RowPrimary", "RowDanger")
        elif status in (TaskStatus.ERROR, TaskStatus.CANCELLED, TaskStatus.WAITING):
            sig = ("重试", "移除", "RowPrimary", "RowDanger")
        else:
            sig = ("打开目录", "移除", "Row", "RowDanger")
        if sig == self._signature:
            return
        self._signature = sig
        self._apply(self.primary, sig[0], sig[2])
        self._apply(self.danger, sig[1], sig[3])


class UpdateDialog(QDialog):
    """在线更新对话框：展示新版说明，一键下载并替换当前 exe"""

    def __init__(self, info: dict, parent: "MainWindow" = None):
        super().__init__(parent)
        self.info = info
        self.win = parent
        self.worker = None
        self.downloaded_path = ""
        self.setWindowTitle("发现新版本")
        self.resize(520, 430)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)

        head = QLabel(f"当前版本 v{info.get('local_version', '')} → "
                      f"新版本 v{info.get('version', '')}（{info.get('tag', '')}）")
        head.setStyleSheet("font-weight: bold; color: #1d4ed8; font-size: 14px;")
        lay.addWidget(head)

        notes = QPlainTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(info.get("body") or "（发行版无说明）")
        lay.addWidget(notes, 1)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(True)
        self.bar.setFormat("准备下载…")
        self.bar.setVisible(False)
        lay.addWidget(self.bar)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.dl_btn = QPushButton("立即下载更新")
        self.dl_btn.setObjectName("Primary")
        self.dl_btn.clicked.connect(self._start_download)
        btns.addWidget(self.dl_btn)
        btns.addWidget(QPushButton("查看发布页", clicked=self._open_page))
        btns.addWidget(QPushButton("忽略此版本", clicked=self._skip_version))
        btns.addWidget(QPushButton("不再提示", clicked=self._ignore_updates))
        lay.addLayout(btns)

    def _start_download(self):
        if self.worker is not None:          # 下载中 → 该按钮作为取消
            self._cancelling = True
            self.worker.cancel_download()
            self.dl_btn.setEnabled(False)
            self.bar.setFormat("正在取消…")
            return
        url = self.info.get("download_url")
        if not url:
            QMessageBox.warning(self, "提示", "该发行版没有可用的 exe 附件，请打开发布页手动下载。")
            return
        self.worker = UpdateWorker(self.info.get("local_version") or "")
        self.worker.download_progress.connect(self._on_progress)
        self.worker.download_done.connect(self._on_done)
        self.worker.download_failed.connect(self._on_failed)
        self.dl_btn.setText("取消下载")
        self.dl_btn.setObjectName("")
        self.dl_btn.style().unpolish(self.dl_btn)
        self.dl_btn.style().polish(self.dl_btn)
        self._cancelling = False
        self.bar.setVisible(True)
        self.bar.setFormat("准备下载…")
        self.worker.start_download(url, self.info.get("file_name") or "update.exe",
                                   self.info.get("sha256") or "")

    def reject(self):
        # 关闭对话框 = 取消后台下载，否则线程继续下载、完成后凭空弹模态框
        if self.worker is not None:
            self.worker.cancel_download()
        super().reject()

    def _on_progress(self, done: int, total: int):
        if total > 0:
            self.bar.setValue(int(done * 1000 / total))
            self.bar.setFormat(f"{format_size(done)} / {format_size(total)}")
        else:
            self.bar.setRange(0, 0)
            self.bar.setFormat(f"已下载 {format_size(done)}")

    def _on_done(self, path: str):
        self.worker = None
        self.downloaded_path = path
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000)
        self.win._apply_update(path, self.info)
        self.accept()

    def _on_failed(self, msg: str):
        self.worker = None                   # 复位：否则按钮永远走"取消"分支无法重试
        self.dl_btn.setEnabled(True)
        self.bar.setVisible(False)
        self.dl_btn.setText("立即下载更新")
        self.dl_btn.setObjectName("Primary")
        self.dl_btn.style().unpolish(self.dl_btn)
        self.dl_btn.style().polish(self.dl_btn)
        if not self._cancelling:
            QMessageBox.warning(self, "下载失败", msg)

    def _open_page(self):
        url = self.info.get("page_url")
        if url:
            self.win._shell_open(url)

    def _skip_version(self):
        self.win.mgr.update_settings(
            skipped_update_version=self.info.get("version", ""))
        self.reject()

    def _ignore_updates(self):
        box = QMessageBox.question(
            self, "不再提示",
            "今后启动时不再检查新版本？（仍可通过 设置→检查更新 手动检查）",
            QMessageBox.Yes | QMessageBox.No)
        if box == QMessageBox.Yes:
            self.win.mgr.update_settings(update_ignored=True)
            self.reject()


class MainWindow(QMainWindow):
    done_sig = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.mgr = DownloadManager.instance()
        self.mgr.load_state()
        self.mgr.start_monitor()
        self.done_sig.connect(self._handle_done)
        self.mgr.set_on_task_done(self.done_sig.emit)
        self._row_urls: list[str] = []      # 当前表格里的 url 顺序
        self._cells: list[dict] = []        # 每行的可复用控件，避免整表重建
        self._detail: ThreadDetailDialog | None = None
        self._open_queue: dict[str, float] = {}   # 合并"完成后打开目录"的重复请求
        self._open_timer = QTimer(self, interval=700, timeout=self._flush_open)
        self._open_timer.setSingleShot(True)
        self.setWindowTitle("多线程下载工具")
        # 窗口图标跟随 QApplication 的多尺寸 ico（在 __main__ 里设置），保证任务栏一致
        self.resize(1180, 640)
        self.setStyleSheet(STYLE)
        self._build_ui()
        self._refresh = QTimer(self, interval=500, timeout=self._update_table)
        self._refresh.start()
        self._worker = AddWorker(self.mgr)
        self._worker.done.connect(self._on_added)
        self._worker.finished.connect(self._on_batch_finished)
        self._update_worker = None
        self._update_dialog = None
        self._update_timer = QTimer(self, interval=3000, singleShot=True,
                                    timeout=self._auto_check_update)
        self._update_timer.start()
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
        menu.addAction("打开配置文件夹", lambda: self._shell_open(config.DATA_DIR))
        menu.addSeparator()
        menu.addAction("检查更新…", lambda: self._check_update(manual=True))

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

    # ---------- 在线更新（Gitee 发行版） ----------

    def _auto_check_update(self):
        s = self.mgr.settings
        if s.get("update_ignored"):
            return
        if time.time() - float(s.get("last_update_check") or 0) < CHECK_INTERVAL:
            return
        self._check_update(manual=False)

    def _check_update(self, manual: bool):
        if self._update_worker is not None:
            if manual:
                self.status_bar.showMessage("正在检查更新…")
            return
        if manual:
            self.status_bar.showMessage("正在检查更新…")
        from . import __version__
        w = UpdateWorker(__version__)
        w.checked.connect(lambda info: self._on_update_checked(info, manual))
        # _spawn 兜底只 emit download_failed：检查线程若抛异常，checked 永不发出，
        # 不复位则 _update_worker 永远非 None，之后所有检查直接 return
        w.download_failed.connect(lambda msg: setattr(self, "_update_worker", None))
        self._update_worker = w
        w.check()

    def _on_update_checked(self, info: dict, manual: bool):
        self._update_worker = None
        self.mgr.update_settings(last_update_check=time.time())
        if not info.get("ok"):
            if manual:
                self.status_bar.showMessage("检查更新失败")
                QMessageBox.warning(self, "检查更新", info.get("error") or "检查更新失败")
            return
        if not info.get("available"):
            if manual:
                self.status_bar.showMessage("已是最新版本")
                QMessageBox.information(self, "检查更新",
                                        f"当前已是最新版本（v{info.get('local_version', '')}）。")
            return
        if (info.get("version")
                == self.mgr.settings.get("skipped_update_version")
                and not manual):
            return
        if manual:
            self.status_bar.showMessage(f"发现新版本 {info.get('tag', '')}")
            self._show_update_dialog(info)
        elif self.tray:
            self.tray.showMessage(
                "发现新版本 " + str(info.get("tag", "")),
                "可在 设置 → 检查更新 中下载，或直接运行新版程序",
                QSystemTrayIcon.Information, 8000)
            self._show_update_dialog(info)
        else:
            self._show_update_dialog(info)

    def _show_update_dialog(self, info):
        if self._update_dialog is not None:
            try:
                self._update_dialog.raise_()
            except RuntimeError:
                pass
            return
        dlg = UpdateDialog(info, self)
        self._update_dialog = dlg
        dlg.finished.connect(lambda _: setattr(self, "_update_dialog", None))
        dlg.exec_() if not self.isVisible() else dlg.show()

    def _apply_update(self, path: str, info: dict):
        """下载完成：打包版写替换脚本并退出重启；其余情况打开所在目录"""
        if is_frozen_exe():
            box = QMessageBox.question(
                self, "更新已下载",
                "新版本已下载完成。是否立即退出并自动更新、重启新版？\n"
                "选择稍后则新版程序会保存在下载目录，当前版本继续运行。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if box == QMessageBox.Yes:
                from . import update as update_mod
                update_mod.apply_update(path, info, self)
                self._quit()
                return
        self._shell_open(path, must_dir=True)   # 只打开所在目录，绝不直接执行下载来的 exe
        self.status_bar.showMessage(f"新版本已保存: {path}")

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
        # 直接打开当前目录：os.startfile 走 ShellExecute，比进文件对话框快得多
        open_dir_btn = QPushButton("打开")
        open_dir_btn.setObjectName("Mini")
        open_dir_btn.setToolTip("直接打开当前保存目录")
        open_dir_btn.clicked.connect(
            lambda: self._shell_open(self.dir_edit.text().strip()))
        dir_row.addWidget(open_dir_btn)
        # 最近目录：常用目录一键切换，省掉文件对话框
        self.recent_btn = QToolButton()
        self.recent_btn.setObjectName("Mini")
        self.recent_btn.setText("最近 ▾")
        self.recent_btn.setToolTip("最近使用过的保存目录")
        self.recent_btn.setPopupMode(QToolButton.InstantPopup)
        self.recent_btn.setMenu(self._build_recent_menu())
        dir_row.addWidget(self.recent_btn)
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
        self.limit_combo.currentIndexChanged.connect(
            lambda: self.mgr.set_speed_limit(self.limit_combo.currentData() or 0))
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
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._row_menu)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        h = self.table.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignCenter)
        h.setHighlightSections(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i, w in enumerate([0, 168, 190, 100, 132, 84, 160]):
            if i:
                self.table.setColumnWidth(i, w)
        root.addWidget(self.table, 1)

        self.status_bar = self.statusBar()
        self.status_bar.showMessage("就绪")

    def _build_recent_menu(self) -> QMenu:
        menu = QMenu(self)
        dirs = list(self.mgr.settings.get("recent_dirs") or [])
        act = menu.addAction("保存目录")
        act.setEnabled(False)
        if dirs:
            menu.addSeparator()
            for d in dirs[:8]:
                menu.addAction(d, lambda _=False, p=d: self.dir_edit.setText(p))
        else:
            none = menu.addAction("（暂无记录）")
            none.setEnabled(False)
        return menu

    def _refresh_recent_menu(self):
        self.recent_btn.setMenu(self._build_recent_menu())

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
        menu = QMenu(self)                    # 挂 parent：setContextMenu 不接管所有权，
        menu.addAction("显示主界面", self._show_from_tray)   # 局部变量会被 GC 成悬挂指针
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
        start = self.dir_edit.text().strip() or config.DEFAULT_SETTINGS["save_dir"]
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", start)
        if d:
            self.dir_edit.setText(os.path.normpath(d))

    def _remember_dir(self, path: str):
        if not path:
            return
        dirs = [d for d in (self.mgr.settings.get("recent_dirs") or [])
                if d != path]
        dirs.insert(0, path)
        self.mgr.settings["recent_dirs"] = dirs[:8]
        self._refresh_recent_menu()

    def _persist_options(self):
        save_dir = self.dir_edit.text().strip()
        if save_dir:
            self._remember_dir(save_dir)      # 先更新内存中的 recent_dirs，再一并落盘
        self.mgr.update_settings(
            save_dir=save_dir,
            recent_dirs=self.mgr.settings.get("recent_dirs") or [],
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

    # ---------- table refresh（增量更新） ----------

    def _update_table(self):
        snaps = self.mgr.snapshots()
        urls = [s["url"] for s in snaps]
        if urls != self._row_urls:
            self._rebuild_rows(snaps)
        else:
            for r, s in enumerate(snaps):
                self._refresh_row(r, s)
        total = self.mgr.total_speed()
        active = sum(1 for s in snaps if s["status"] == TaskStatus.DOWNLOADING)
        self.status_bar.showMessage(
            f"进行中: {active} / {len(snaps)}   总速度: {format_speed(total)}   "
            f"限速: {self.limit_combo.currentText()}")

    def _rebuild_rows(self, snaps):
        """只在任务集合或顺序变化时重建控件"""
        self._row_urls = [s["url"] for s in snaps]
        self.table.setRowCount(len(snaps))
        self._cells = []
        for r, s in enumerate(snaps):
            self._make_row(r, s)   # 内部按行号写入 self._cells

    def _make_row(self, r, s) -> dict:
        name = QTableWidgetItem(s["file_name"])
        name.setToolTip(s["url"])
        name.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        name.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))

        size_item = QTableWidgetItem("")
        size_item.setTextAlignment(Qt.AlignCenter)
        size_item.setFont(NUM_FONT)

        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setTextVisible(True)
        bar.setFixedHeight(14)
        bar_wrap = QWidget()
        bl = QHBoxLayout(bar_wrap)
        bl.setContentsMargins(10, 0, 10, 0)
        bl.addStretch()
        bl.addWidget(bar)
        bl.addStretch()

        speed_item = QTableWidgetItem("")
        speed_item.setTextAlignment(Qt.AlignCenter)
        speed_item.setFont(NUM_FONT)

        threads = ThreadSpeedWidget()
        threads.clicked.connect(lambda u=s["url"]: self._open_thread_detail(u))

        status_item = QTableWidgetItem("")
        status_item.setTextAlignment(Qt.AlignCenter)

        actions = RowActions()
        actions.clicked.connect(lambda tag, u=s["url"]: self._on_row_action(tag, u))

        self.table.setItem(r, 0, name)
        self.table.setItem(r, 1, size_item)
        self.table.setCellWidget(r, 2, bar_wrap)
        self.table.setItem(r, 3, speed_item)
        self.table.setCellWidget(r, 4, threads)
        self.table.setItem(r, 5, status_item)
        self.table.setCellWidget(r, 6, actions)

        cells = {"name": name, "size": size_item, "bar": bar, "speed": speed_item,
                 "threads": threads, "status": status_item, "actions": actions}
        while len(self._cells) <= r:      # 先占位，_refresh_row 需要按行索引取控件
            self._cells.append(None)
        self._cells[r] = cells
        self._refresh_row(r, s)
        return cells

    def _refresh_row(self, r, s):
        c = self._cells[r]
        status = s["status"]

        c["size"].setText(f"{format_size(s['downloaded'])} / {format_size(s['total_size'])}")
        c["bar"].setValue(int(s["progress"] * 1000))
        c["bar"].setFormat(f"{s['progress'] * 100:.1f}%")

        c["speed"].setText(format_speed(s["speed"]))
        c["speed"].setToolTip(f"任务总速度 {format_speed(s['speed'])}\n点击左侧柱状图查看各线程")

        declared = s["thread_count"] if s["support_range"] else 1
        c["threads"].set_state(s["chunks"], declared)

        st_item = c["status"]
        st_item.setText(status.value)
        st_item.setForeground(STATUS_COLOR.get(status, QColor("#000")))
        st_item.setBackground(QBrush(QColor(STATUS_BG[status])))
        f = QFont()
        f.setBold(True)
        st_item.setFont(f)
        st_item.setToolTip(s["error"] or status.value)

        c["actions"].set_status(status)
        c["name"].setForeground(
            QBrush(QColor("#1f2937" if status in (TaskStatus.DOWNLOADING, TaskStatus.PAUSED)
                         else "#64748b")))

    # ---------- 行操作 ----------

    def _on_row_action(self, tag: str, url: str):
        if tag == "primary":
            st = self.mgr.get(url)
            status = st.info.status if st else None
            if status == TaskStatus.DOWNLOADING:
                self.mgr.pause(url)
            elif status == TaskStatus.PAUSED:
                self.mgr.resume(url)
            elif status in (TaskStatus.ERROR, TaskStatus.CANCELLED, TaskStatus.WAITING):
                self.mgr.retry(url)
            elif status == TaskStatus.COMPLETED:
                self._open_task_dir(url)
        else:
            self._cancel(url)
        self._update_table()

    def _cancel(self, url):
        if QMessageBox.question(
                self, "确认",
                "确定移除该任务吗？将删除其所有临时分片目录及未完成的残留文件。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) \
                == QMessageBox.Yes:
            self.mgr.cancel(url)
        self._update_table()

    def _open_task_dir(self, url):
        task = self.mgr.get(url)
        if task:
            self._shell_open(os.path.dirname(task.info.save_path))

    def _open_thread_detail(self, url):
        if self._detail is not None:
            try:
                self._detail.close()
            except RuntimeError:
                pass
            self._detail.deleteLater()
        dlg = ThreadDetailDialog(self, url, self)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        # 闭包捕获 dlg 做身份判断：旧对话框的 deleteLater 异步触发时，
        # self._detail 可能已指向新对话框，不能盲目清空
        dlg.destroyed.connect(
            lambda *_: setattr(self, "_detail", None) if self._detail is dlg else None)
        self._detail = dlg
        dlg.show()

    def _on_double_click(self, row, _col):
        url = self._row_urls[row] if 0 <= row < len(self._row_urls) else None
        if not url:
            return
        task = self.mgr.get(url)
        if not task:
            return
        if task.info.status == TaskStatus.COMPLETED:
            self._shell_open(task.info.save_path)          # 打开文件本身
        else:
            self._open_thread_detail(url)                  # 看各线程速度

    def _row_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if not (0 <= row < len(self._row_urls)):
            return
        url = self._row_urls[row]
        task = self.mgr.get(url)
        if not task:
            return
        menu = QMenu(self)
        if task.info.status == TaskStatus.COMPLETED:
            menu.addAction("打开文件", lambda: self._shell_open(task.info.save_path))
        menu.addAction("打开所在目录",
                       lambda: self._shell_open(os.path.dirname(task.info.save_path)))
        menu.addAction("线程速度明细", lambda: self._open_thread_detail(url))
        menu.addSeparator()
        menu.addAction("复制完整路径",
                       lambda: QApplication.clipboard().setText(task.info.save_path))
        menu.addAction("复制下载链接",
                       lambda: QApplication.clipboard().setText(task.info.url))
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    # ---------- completion ----------

    @pyqtSlot(str)
    def _handle_done(self, path):
        name = os.path.basename(path)
        if self.tray:
            self.tray.showMessage("下载完成", name, QSystemTrayIcon.Information, 5000)
        if self.auto_open.isChecked():
            self._queue_open(os.path.dirname(path))

    def _queue_open(self, folder: str):
        """多个任务接连完成时合并成一次打开，避免连续弹出好几个资源管理器窗口"""
        self._open_queue[folder] = 1
        self._open_timer.start()

    def _flush_open(self):
        for folder in list(self._open_queue):
            self._shell_open(folder)
        self._open_queue.clear()

    # ---------- 打开目录（性能关键路径） ----------

    def _shell_open(self, path: str, must_dir: bool = False):
        """快速打开：Windows 走 ShellExecute（os.startfile），
        不再 fork explorer /select —— 后者启动 explorer.exe 常要1~3 秒。"""
        if not path:
            return
        if path.startswith(("http://", "https://")):
            final = path                       # URL 原样交给 Shell，禁止路径归一化
        else:
            target = os.path.normpath(path)
            if must_dir or os.path.isdir(target) or not os.path.exists(target):
                target = target if os.path.isdir(target) else (
                    os.path.dirname(target) or target)
            final = target

        def run():
            try:
                if sys.platform == "win32":
                    if not os.path.exists(final) and not final.startswith(
                            ("http://", "https://")):
                        os.makedirs(final, exist_ok=True)
                    os.startfile(final)          # 目录/文件/URL 都交给 Shell
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", final])
                else:
                    subprocess.Popen(["xdg-open", final])
            except OSError:
                pass
        threading.Thread(target=run, daemon=True).start()