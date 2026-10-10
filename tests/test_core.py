"""核心下载引擎测试：本地 Range 服务器验证多线程分片、续传、合并、限速。"""
import hashlib
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from downloader.manager import DownloadManager
from downloader.util import SpeedLimiter
from downloader.info import DownloadInfo, TaskStatus
from downloader.task import DownloadTask

import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir():
    """每个测试用独立的配置目录：任务完成会 save_state，避免污染其他测试
    （import 期的目录隔离无效——pytest 先收集全部模块再运行测试）"""
    from downloader import config
    config.apply_data_dir(tempfile.mkdtemp(prefix="dlcfg_"), remember=False)
    yield


DATA = os.urandom(3 * 1024 * 1024)  # 3MB
MD5 = hashlib.md5(DATA).hexdigest()
_tmp = tempfile.mkdtemp(prefix="dltest_")
with open(os.path.join(_tmp, "blob"), "wb") as f:
    f.write(DATA)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(DATA)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else len(DATA) - 1
            chunk = DATA[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(chunk)
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(DATA)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(DATA)


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def wait(task, timeout=30):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if task.info.status in (TaskStatus.COMPLETED, TaskStatus.ERROR):
            return
        time.sleep(0.2)
    raise AssertionError("timeout, status=" + task.info.status.value)


def test_multithread_download():
    srv, port = serve()
    out = tempfile.mkdtemp(prefix="dlout_")
    mgr = DownloadManager()
    mgr.start_monitor = lambda: None
    task = mgr.add_download(f"http://127.0.0.1:{port}/blob", out, thread_count=4)
    task._stop_threads(timeout=20)
    wait(task)
    path = task.info.save_path
    assert os.path.exists(path), "output missing"
    got = hashlib.md5(open(path, "rb").read()).hexdigest()
    assert got == MD5, f"md5 mismatch {got} != {MD5}"
    assert not os.path.exists(path + ".parts"), "parts dir not cleaned"
    srv.shutdown()
    print("PASS test_multithread_download")


def test_speed_and_progress():
    srv, port = serve()
    out = tempfile.mkdtemp(prefix="dlout_")
    info = DownloadInfo(f"http://127.0.0.1:{port}/blob", "blob", 
                        os.path.join(out, "blob"), len(DATA), 3, True)
    limiter = SpeedLimiter(0)
    task = DownloadTask(info, limiter)
    task.start()
    import time
    time.sleep(0.5)
    task.tick_speed()
    snap = task.snapshot()
    wait(task)
    assert hashlib.md5(open(snap["save_path"], "rb").read()).hexdigest() == MD5
    srv.shutdown()
    print("PASS test_speed_and_progress")


def test_resume():
    """暂停后 part 文件保留，继续可续传"""
    srv, port = serve()
    out = tempfile.mkdtemp(prefix="dlout_")
    info = DownloadInfo(f"http://127.0.0.1:{port}/blob", "blob",
                        os.path.join(out, "blob"), len(DATA), 2, True)
    task = DownloadTask(info, SpeedLimiter(400 * 1024))  # 限速制造中途暂停
    task.start()
    import time
    time.sleep(0.6)
    task.pause()
    task._stop_threads(timeout=10)
    paused_bytes = sum(c.done_bytes() for c in task._chunks)
    assert 0 < paused_bytes < len(DATA), f"paused_bytes={paused_bytes}"
    task.start()  # resume
    wait(task)
    assert hashlib.md5(open(info.save_path, "rb").read()).hexdigest() == MD5
    srv.shutdown()
    print("PASS test_resume")


def test_single_thread_no_range():
    srv, port = serve()
    out = tempfile.mkdtemp(prefix="dlout_")
    info = DownloadInfo(f"http://127.0.0.1:{port}/blob", "blob",
                        os.path.join(out, "blob"), len(DATA), 1, False)
    task = DownloadTask(info, SpeedLimiter(0))
    task.start()
    wait(task)
    assert hashlib.md5(open(info.save_path, "rb").read()).hexdigest() == MD5
    srv.shutdown()
    print("PASS test_single_thread_no_range")


class LyingHandler(BaseHTTPRequestHandler):
    """HEAD 声称支持 Range，但 GET 忽略 Range 直接返回整文件 200（无 Content-Range）。"""
    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(DATA)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(DATA)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(DATA)


def serve_lying():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), LyingHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_range_ignored_fallback_single():
    """探测以为支持 Range，实际服务器忽略 Range → 应降级为单线程并得到正确文件"""
    srv, port = serve_lying()
    out = tempfile.mkdtemp(prefix="dlout_")
    info = DownloadInfo(f"http://127.0.0.1:{port}/blob", "blob",
                        os.path.join(out, "blob"), len(DATA), 4, True)
    task = DownloadTask(info, SpeedLimiter(0))
    task.start()
    wait(task)
    assert task.info.status == TaskStatus.COMPLETED, task.info.status
    assert task.info.support_range is False, "应已标记不支持 Range"
    assert len(task._chunks) == 1, "应已降级为单分片"
    assert hashlib.md5(open(info.save_path, "rb").read()).hexdigest() == MD5
    assert not os.path.exists(info.save_path + ".parts"), "parts 目录未清理"
    srv.shutdown()
    print("PASS test_range_ignored_fallback_single")


if __name__ == "__main__":
    test_multithread_download()
    test_speed_and_progress()
    test_resume()
    test_single_thread_no_range()
    test_range_ignored_fallback_single()
    print("ALL CORE TESTS PASSED")
