"""在线更新测试：版本比较纯函数 + Gitee 公共 API 结构 + 本地下载链路（不发真实请求）"""
import hashlib
import os
import sys
import tempfile
import threading
import wsgiref.simple_server

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication
from downloader import update as up

_TMP = tempfile.mkdtemp(prefix="dlupd_")
from downloader import config
config.apply_data_dir(_TMP, remember=False)

REL = {
    "id": 1, "tag_name": "v3", "name": "多线程下载工具 v3",
    "body": "更新说明", "prerelease": False,
    "assets": [
        {"name": "v3.zip", "browser_download_url": "https://x/v3.zip"},
        {"name": "多线程下载工具.exe",
         "browser_download_url": "https://x/tool.exe"},
    ],
}


def test_version_parsing():
    assert up.version_tuple("v2.0.1") == (2, 0, 1)
    assert up.parse_release_tag("v3") == "3"
    assert up.parse_release_tag("release/v10.2") == "10.2"
    assert up.parse_release_tag("badg") == ""
    print("PASS test_version_parsing")


def test_is_newer():
    # 2 段 tag 与打包 3 段版本（末尾 .0）等价
    assert not up.is_newer("2.0", "2.0.0")
    assert not up.is_newer("3", "3.0.0")
    assert up.is_newer("3", "2.0.0")
    assert up.is_newer("2.1", "2.0.9")
    assert not up.is_newer("2.0.1", "2.1")
    assert up.is_newer("2.0.1", "2.0.0")
    assert not up.is_newer("", "2.0.0")
    assert not up.is_newer("2.0", "")
    print("PASS test_is_newer")


def test_pick_latest():
    old = dict(REL, id=0, tag_name="v2.0", created_at="2026-09-01T00:00:00+08:00")
    new = dict(REL, id=1, tag_name="v10", created_at="2026-10-01T00:00:00+08:00")
    pre = dict(REL, id=2, tag_name="v99", prerelease=True)
    noexe = dict(REL, id=3, tag_name="v50",
                 assets=[{"name": "src.zip", "browser_download_url": "u"}])
    picked = up.pick_latest([old, pre, noexe, new])
    assert picked.get("tag_name") == "v10", picked.get("tag_name")
    assert up.exe_asset(picked)["name"].endswith(".exe")
    assert up.pick_latest([]) == {}
    print("PASS test_pick_latest")


def test_exe_asset_prefers_portable():
    """发行版同时上传安装包附件时，自动更新必须认准便携版 exe"""
    rel = dict(REL, assets=[
        {"name": "多线程下载工具安装包.exe", "browser_download_url": "u1"},
        {"name": "多线程下载工具.exe", "browser_download_url": "u2"},
    ])
    assert up.exe_asset(rel)["name"] == "多线程下载工具.exe"
    # 只有安装包、没有便携版的发行版不作为自动更新来源
    installer_only = dict(REL, assets=[
        {"name": "多线程下载工具安装包.exe", "browser_download_url": "u1"}])
    assert up.exe_asset(installer_only)["name"] == "多线程下载工具安装包.exe"
    assert up.pick_latest([installer_only]) == {}
    print("PASS test_exe_asset_prefers_portable")


def test_fetch_update_offline(monkey=False):
    """API 不可达时应返回 ok=False 而不是抛异常"""
    orig = up._request_json
    def boom(url):
        raise OSError("network down")
    up._request_json = boom
    try:
        r = up.fetch_update("2.0.0")
        assert r["ok"] is False and "error" in r
    finally:
        up._request_json = orig
    # API 正常、版本已最新 → available=False
    orig2 = up._request_json
    up._request_json = lambda url: [dict(REL, tag_name="v2.0")]
    try:
        r = up.fetch_update("2.0.0")
        assert r["ok"] and r["available"] is False, r
    finally:
        up._request_json = orig2
    # API 正常、有新版
    up._request_json = lambda url: [REL]
    try:
        r = up.fetch_update("2.0.0")
        assert r["ok"] and r["available"], r
        assert r["version"] == "3" and r["download_url"].endswith("tool.exe")
        assert "多线程下载工具.exe" in r["file_name"]
    finally:
        up._request_json = orig2
    print("PASS test_fetch_update_offline")


DATA = os.urandom(300_000)


def _serve_blob():
    def app(env, start):
        start("200 OK", [("Content-Length", str(len(DATA))),
                         ("Content-Type", "application/octet-stream")])
        return [DATA]
    srv = wsgiref.simple_server.make_server("127.0.0.1", 0, app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_port


def test_download_worker():
    app = QApplication.instance() or QApplication([])
    srv, port = _serve_blob()
    w = up.UpdateWorker("2.0.0")
    got = {}
    w.download_progress.connect(lambda d, t: got.update(progress=(d, t)))
    w.download_done.connect(lambda p: got.update(path=p))
    w.download_failed.connect(lambda m: got.update(err=m))
    w.start_download(f"http://127.0.0.1:{port}/tool.exe", "多线程下载工具.exe")
    for _ in range(200):
        if "path" in got or "err" in got:
            break
        app.processEvents()
        threading.Event().wait(0.05)
    srv.shutdown()
    assert "err" not in got, got.get("err")
    path = got["path"]
    assert hashlib.md5(open(path, "rb").read()).hexdigest() == \
        hashlib.md5(DATA).hexdigest()
    assert path.endswith("多线程下载工具.exe")
    assert not os.path.exists(path + ".part")
    app.processEvents()
    print("PASS test_download_worker")


def test_download_with_sha256():
    """下载完成后应执行 SHA256 校验。

    回归：曾把模块级 _file_sha256 写成 self._file_sha256，
    带 SHA256 的发行版（v3.2.0 起）下载完成即 AttributeError、更新必失败。
    """
    app = QApplication.instance() or QApplication([])
    srv, port = _serve_blob()
    expect = hashlib.sha256(DATA).hexdigest()
    # 1) 校验和匹配 → download_done
    w = up.UpdateWorker("2.0.0")
    got = {}
    w.download_done.connect(lambda p: got.update(path=p))
    w.download_failed.connect(lambda m: got.update(err=m))
    w.start_download(f"http://127.0.0.1:{port}/tool.exe", "多线程下载工具.exe",
                     expect)
    for _ in range(200):
        if "path" in got or "err" in got:
            break
        app.processEvents()
        threading.Event().wait(0.05)
    assert "err" not in got, got.get("err")
    assert hashlib.sha256(open(got["path"], "rb").read()).hexdigest() == expect
    # 2) 校验和不匹配 → download_failed，且文件被删除
    w2 = up.UpdateWorker("2.0.0")
    got2 = {}
    w2.download_done.connect(lambda p: got2.update(path=p))
    w2.download_failed.connect(lambda m: got2.update(err=m))
    w2.start_download(f"http://127.0.0.1:{port}/tool.exe", "bad.exe",
                      "0" * 64)
    for _ in range(200):
        if "path" in got2 or "err" in got2:
            break
        app.processEvents()
        threading.Event().wait(0.05)
    assert "err" in got2 and "SHA256" in got2["err"], got2
    assert not os.path.exists(os.path.join(w2._download_dir(), "bad.exe"))
    srv.shutdown()
    app.processEvents()
    print("PASS test_download_with_sha256")


def test_download_cancel_and_ceiling():
    app = QApplication.instance() or QApplication([])
    w = up.UpdateWorker("2.0.0")
    got = {}
    w.download_failed.connect(lambda m: got.update(err=m))
    w.start_download("http://127.0.0.1:1/nope.exe", "x.exe")   # 端口不可达
    for _ in range(100):
        if "err" in got:
            break
        app.processEvents()
        threading.Event().wait(0.05)
    assert "err" in got, "连接失败应上报 download_failed"
    assert not os.path.exists(os.path.join(w._download_dir(), "x.exe"))
    print("PASS test_download_cancel_and_ceiling")


def test_restart_script():
    d = tempfile.mkdtemp(prefix="dlupd_scr_")
    new = os.path.join(d, "多线程下载工具.exe")
    target = os.path.join(d, "old", "多线程下载工具.exe")
    open(new, "wb").write(b"x")
    script = up.write_restart_script(new, target)
    raw = open(script, "rb").read()
    text = raw.decode("gbk", "replace")
    assert new in text and target in text and "copy /y" in text
    assert "\ufffd" not in text, "脚本路径中文必须可用本地编码往返"
    print("PASS test_restart_script")


def test_update_dialog_handlers():
    """更新对话框按钮逻辑：忽略此版本/不再提示 持久化，_show_update_dialog 单例"""
    from PyQt5.QtWidgets import QMessageBox
    from downloader.ui import MainWindow, UpdateDialog
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.show()
    app.processEvents()

    info = {"ok": True, "available": True, "tag": "v9.9.9", "version": "9.9.9",
            "local_version": "3.1.0", "title": "t", "body": "b",
            "page_url": "https://example.com", "file_name": "x.exe",
            "download_url": ""}
    dlg = UpdateDialog(info, win)
    dlg._skip_version()               # 忽略此版本：写设置并关闭
    assert win.mgr.settings["skipped_update_version"] == "9.9.9"
    assert not dlg.isVisible()

    QMESG = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        dlg2 = UpdateDialog(info, win)
        dlg2._ignore_updates()        # 不再提示：写设置并关闭
        assert win.mgr.settings["update_ignored"] is True
        assert not dlg2.isVisible()
    finally:
        QMessageBox.question = QMESG

    # 不再提示后，自动检查直接跳过
    win._auto_check_update()
    assert win._update_dialog is None

    # 手动检查不受影响：available 时弹对话框（单例）
    win.mgr.settings["update_ignored"] = False
    win._on_update_checked(dict(info), manual=True)
    assert win._update_dialog is not None
    assert win._update_dialog.info["tag"] == "v9.9.9"
    win._show_update_dialog(info)     # 第二次请求复用已有对话框
    win._update_dialog.close()
    app.processEvents()

    win.mgr.shutdown()
    print("PASS test_update_dialog_handlers")


if __name__ == "__main__":
    test_version_parsing()
    test_is_newer()
    test_pick_latest()
    test_exe_asset_prefers_portable()
    test_fetch_update_offline()
    test_download_worker()
    test_download_with_sha256()
    test_download_cancel_and_ceiling()
    test_restart_script()
    test_update_dialog_handlers()
    print("ALL UPDATE TESTS PASSED")
