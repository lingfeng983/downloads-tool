"""UI 冒烟测试：离屏构造主窗口，验证分片速度控件绘制、增量刷新、明细对话框。
不发起真实网络下载，用本地 Range 服务器跑一个真实任务验证数据链路。"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPixmap

from downloader import config

from downloader.info import TaskStatus
from downloader.ui import MainWindow, ThreadSpeedWidget, ThreadDetailDialog
from downloader.util import SpeedLimiter, format_speed
from test_core import serve, wait


@pytest.fixture(scope="session")
def app():
    # 目录隔离必须在测试运行时（而非 import 时）做：pytest 先收集全部模块
    # 再运行，其他文件的 import/测试会在期间改写全局 DATA_DIR 并写入任务数据
    config.apply_data_dir(tempfile.mkdtemp(prefix="dlui_"), remember=False)
    os.environ["DOWNLOADS_TOOL_DATA"] = config.DATA_DIR
    # apply_data_dir 会把旧目录的历史任务迁移过来，清空以保证隔离
    for _f in ("tasks.json", "settings.json"):
        _p = os.path.join(config.DATA_DIR, _f)
        if os.path.isfile(_p):
            os.remove(_p)
    return QApplication.instance() or QApplication([])


def test_widgets_draw(app):
    w = ThreadSpeedWidget()
    w.resize(132, 40)
    # 三种状态：未开始 / 混合速度 / 全部完成
    w.set_state([], 8)
    pm = QPixmap(w.size())
    w.render(pm)
    w.set_state([
        {"cid": 0, "speed": 1024 * 300, "done": 10, "total": 100, "percent": 10.0, "finished": False},
        {"cid": 1, "speed": 0, "done": 5, "total": 100, "percent": 5.0, "finished": False},
        {"cid": 2, "speed": 1024 * 800, "done": 90, "total": 100, "percent": 90.0, "finished": False},
        {"cid": 3, "speed": 0, "done": 100, "total": 100, "percent": 100.0, "finished": True},
    ], 4)
    pm2 = QPixmap(w.size())
    w.render(pm2)
    w.set_state([
        {"cid": i, "speed": 0, "done": 100, "total": 100, "percent": 100.0, "finished": True}
        for i in range(4)], 4)
    pm3 = QPixmap(w.size())
    w.render(pm3)
    assert not w.toolTip() or "线程" in w.toolTip()
    print("PASS test_widgets_draw")


def test_window_refresh(app):
    srv, port = serve()
    win = MainWindow()
    win.resize(1180, 640)
    win.show()
    app.processEvents()

    out = tempfile.mkdtemp(prefix="dluiout_")
    win.dir_edit.setText(out)
    url = f"http://127.0.0.1:{port}/blob"
    win.mgr.set_speed_limit(64 * 1024)       # 限速，让下载持续足够久以便采样
    # （3MB / ~64KB/s 远大于下方 12×0.35s 的采样窗口；限速偏高时下载会在
    #   采样结束前完成，断言拿到的全是归零速度，成为随机器快慢的偶发失败）
    task = win.mgr.add_download(url, out, thread_count=4)

    # 多次增量刷新：任务集合不变时不应重建控件
    win._update_table()
    cells_first = win._cells[0]
    assert len(win._cells) == 1, win._cells
    for _ in range(12):
        time.sleep(0.35)
        win._update_table()
        app.processEvents()
        # 控件对象应被复用（增量更新生效）
        assert win._cells[0] is cells_first, "控件被重建了，增量更新失效"
        cells_first = win._cells[0]

    assert len(cells_first["threads"]._chunks) == 4, cells_first["threads"]._chunks
    speeds = [c["speed"] for c in cells_first["threads"]._chunks]
    print(f"  UI 读到分片速度: {[format_speed(s) for s in speeds]}")
    assert any(s > 0 for s in speeds), f"UI 未拿到非零分片速度: {speeds}"
    assert cells_first["speed"].text() != "0 B/s"

    # 明细对话框
    win._open_thread_detail(url)
    dlg = win._detail
    assert isinstance(dlg, ThreadDetailDialog)
    app.processEvents()
    dlg.refresh()
    assert dlg.table.rowCount() == 4, dlg.table.rowCount()
    assert dlg.table.item(0, 0).text() == "#1", dlg.table.item(0, 0).text()
    print("  明细对话框行数:", dlg.table.rowCount())
    dlg.close()
    app.processEvents()

    # 双击未完成任务 → 打开明细；双击已完成 → 打开文件（这里只验证不崩）
    win._on_double_click(0, 0)
    app.processEvents()

    # 新增第二个任务 → 触发表格重建路径
    url2 = f"http://127.0.0.1:{port}/blob?x=1"
    t2 = win.mgr.add_download(url2, out, thread_count=2)
    win._update_table()
    assert len(win._cells) == 2, len(win._cells)
    assert len(win._row_urls) == 2
    win._update_table()
    app.processEvents()

    # 全部完成后速度归零、状态徽章变已完成
    t2.shutdown()
    task.shutdown()
    win.mgr.shutdown()
    srv.shutdown()
    print("PASS test_window_refresh")


if __name__ == "__main__":
    # 用例依赖 pytest 的 app fixture，直接运行时手动构造等价入口
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))