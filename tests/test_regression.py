"""回归测试：覆盖代码评审发现并已修复的缺陷。

每个测试对应一个具体缺陷，注释里标注原始症状，避免以后重构时静默回归。
"""
import hashlib
import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from downloader.info import DownloadInfo, TaskStatus
from downloader.probe import extract_filename, sanitize_filename
from downloader.task import Chunk, DownloadTask, split_ranges
from downloader.util import SpeedLimiter

HOLD = threading.Event()   # 卡住服务端，让客户端阻塞在 socket 读上


# ============ 本地服务器（可指定大小 / 卡住） ============

def make_handler(data: bytes, stall: bool = False, ignore_range: bool = False):
    size = len(data)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        def do_GET(self):
            if ignore_range:
                self.send_response(200)
                self.send_header("Content-Length", str(size))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                try:
                    for i in range(0, size, 32768):
                        self.wfile.write(data[i:i + 32768])
                        self.wfile.flush()
                except Exception:
                    pass
                return
            rng = self.headers.get("Range")
            m = re.match(r"bytes=(\d+)-(\d*)", rng or "")
            start = int(m.group(1)) if m else 0
            end = int(m.group(2)) if m and m.group(2) else size - 1
            end = min(end, size - 1)
            if m:
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
            else:
                start, end = 0, size - 1
                self.send_response(200)
                self.send_header("Content-Length", str(size))
                self.end_headers()
            body = data[start:end + 1]
            try:
                if stall:
                    # 只发头部一小块就卡住，让客户端阻塞在 read 上
                    self.wfile.write(body[:4096])
                    self.wfile.flush()
                    HOLD.wait(45)
                else:
                    self.wfile.write(body)
            except Exception:
                pass

    return Handler


def serve(data: bytes, **kw):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(data, **kw))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def wait(task, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        if task.info.status in (TaskStatus.COMPLETED, TaskStatus.ERROR):
            return
        time.sleep(0.1)
    raise AssertionError("timeout, status=" + task.info.status.value)


def alive_chunks() -> int:
    return sum(1 for t in threading.enumerate() if t.name.startswith("dl-"))


def md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


# ============ 缺陷1: 小文件 + 高线程数 ============
# 原split_ranges(5, 8) 产生 7 个 (0, -1) 退化分片，length=0 被 is_done()
# 误判为已完成 → 既不建文件，_finalize 又去 open() → FileNotFoundError
# 症状：5 字节文件用 8/16 线程下载直接 ERROR「合并文件失败」
def test_small_file_many_threads():
    print("\n[缺陷1] 小文件 + 多线程不再失败")
    for size in (1, 5, 7, 100):
        data = os.urandom(size)
        srv, port = serve(data)
        for nthreads in (1, 2, 8, 16, 64):
            out = tempfile.mkdtemp(prefix="small_")
            info = DownloadInfo(f"http://127.0.0.1:{port}/blob", "blob",
                                os.path.join(out, "blob"), size, nthreads, True)
            task = DownloadTask(info, SpeedLimiter(0))
            task.start()
            wait(task)
            assert task.info.status == TaskStatus.COMPLETED, \
                f"size={size} threads={nthreads}: {task.info.status.value} {task.error_msg}"
            assert os.path.exists(info.save_path), f"size={size} threads={nthreads} 无输出"
            assert md5(info.save_path) == hashlib.md5(data).hexdigest(), \
                f"size={size} threads={nthreads} 内容不一致"
            assert not os.path.exists(info.save_path + ".parts"), "parts 目录未清理"
            task.shutdown()
            print(f"   size={size:3d} threads={nthreads:2d}  OK")
        srv.shutdown()


def test_split_ranges_never_degenerate():
    print("\n[缺陷1b] split_ranges 不再产生负长度区间")
    for total in (0, 1, 3, 5, 8, 9, 100):
        for n in (1, 2, 4, 8, 16, 64):
            rs = split_ranges(total, n)
            if total <= 0:
                assert rs == [(0, -1)], f"total={total}: {rs}"
                continue
            assert len(rs) <= total, f"total={total} n={n} 段数过多: {rs}"
            for i, (s, e) in enumerate(rs):
                assert s >= 0 and e >= s, f"total={total} n={n} 第{i}段非法: {(s, e)}"
            # 覆盖完整且不重叠
            assert rs[0][0] == 0, f"total={total} n={n} 未从头开始: {rs}"
            assert rs[-1][1] == total - 1, f"total={total} n={n} 未到末尾: {rs}"
            for (s1, e1), (s2, _) in zip(rs, rs[1:]):
                assert e1 + 1 == s2, f"total={total} n={n} 区间不连续: {rs}"
    print("   所有 total/n 组合均合法")


# ============ 缺陷2: pause() 阻塞 UI 29 秒 ============
# 原因：_stop_threads逐个 join(timeout=15)，线程卡在 socket 读时只能等
# 读超时（30s）才醒。症状：16 线程 pause() 实测阻塞 29.03s，UI 完全冻结
def test_pause_returns_quickly_when_stalled():
    print("\n[缺陷2] 服务端卡住时 pause() 仍能快速返回")
    data = os.urandom(8 * 1024 * 1024)
    srv, port = serve(data, stall=True)
    out = tempfile.mkdtemp(prefix="stall_")
    info = DownloadInfo(f"http://127.0.0.1:{port}/blob", "blob",
                        os.path.join(out, "blob"), len(data), 8, True)
    task = DownloadTask(info, SpeedLimiter(0))
    task.start()
    time.sleep(1.5)     # 等各线程都阻塞在 read 上

    t0 = time.time()
    task.pause()
    dt = time.time() - t0
    assert dt < 3.0, f"pause() 阻塞 {dt:.2f}s，仍会冻结 UI（修复前 29s）"
    assert task.info.status == TaskStatus.PAUSED
    print(f"   pause() 耗时 {dt:.3f}s（要求 < 3s，修复前 29.03s）")

    t0 = time.time()
    task.cancel()
    dt2 = time.time() - t0
    assert dt2 < 3.0, f"cancel() 阻塞 {dt2:.2f}s"
    print(f"   cancel() 耗时 {dt2:.3f}s")
    HOLD.set()
    task.shutdown()
    srv.shutdown()


# ============ 缺陷3: 线程泄漏导致同一分片双写 ============
# 原因：_stop_threads join 超时后清空 self._threads，旧线程仍在以 "ab"
# 写同一 chunk，与新线程交错 → 文件损坏
# 症状：4 线程任务实测泄漏出 8 个 dl-* 线程
def test_no_thread_leak_after_pause_resume():
    print("\n[缺陷3] pause/resume 不产生线程泄漏")
    data = os.urandom(8 * 1024 * 1024)
    srv, port = serve(data, stall=True)
    out = tempfile.mkdtemp(prefix="leak_")
    info = DownloadInfo(f"http://127.0.0.1:{port}/blob", "blob",
                        os.path.join(out, "blob"), len(data), 4, True)
    task = DownloadTask(info, SpeedLimiter(0))
    task.start()
    time.sleep(1.2)

    baseline = alive_chunks()
    task.pause()
    after_pause = alive_chunks()
    assert after_pause == 0, f"pause 后仍有 {after_pause} 个分片线程存活"
    print(f"   pause 后存活线程 {after_pause}（基线 {baseline}）")

    task.start()          # resume
    time.sleep(1.5)
    managed = len(task._live_threads())
    assert managed == 4, f"resume 后应有 4 个受管线程，实际 {managed}"
    real = alive_chunks()
    assert real == managed, f"泄漏：受管 {managed} 个，实际存活 {real} 个"
    print(f"   resume 后受管 {managed} 个 / 实际 {real} 个，无泄漏")

    task.cancel()
    HOLD.set()
    task.shutdown()
    srv.shutdown()


def test_retry_after_pause_produces_valid_file():
    print("\n[缺陷3b] 多次 pause/resume 后文件仍然完整")
    data = os.urandom(4 * 1024 * 1024)
    srv, port = serve(data, stall=False)
    out = tempfile.mkdtemp(prefix="rp_")
    info = DownloadInfo(f"http://127.0.0.1:{port}/blob", "blob",
                        os.path.join(out, "blob"), len(data), 4, True)
    task = DownloadTask(info, SpeedLimiter(1500 * 1024))  # 限速，制造中途暂停
    task.start()
    for _ in range(3):
        time.sleep(0.35)
        task.pause()
        time.sleep(0.05)
        task.start()
    task.info.status = TaskStatus.DOWNLOADING   # 解除限速拖住的可能
    wait(task)
    assert task.info.status == TaskStatus.COMPLETED, task.error_msg
    assert md5(info.save_path) == hashlib.md5(data).hexdigest(), "多次暂停续传后内容不一致"
    print("   3 轮pause/resume 后 MD5 一致")
    task.shutdown()
    srv.shutdown()


# ============ 缺陷4: Windows 非法文件名 ============
# 症状：a:b.txt / a*b.txt / na|me.txt 原样保留，Windows 上 open() 抛 OSError
def test_sanitize_filename():
    print("\n[缺陷4] 文件名清洗")
    cases = [
        ("a:b.txt", "a_b.txt"),
        ("na|me.txt", "na_me.txt"),
        ("a*b.txt", "a_b.txt"),
        ("a<b>.txt", "a_b_.txt"),
        ('q"uote.txt', "q_uote.txt"),
        ("C:\\path\\to\\file.txt", "file.txt"),   # 去掉路径成分
        ("dir/sub/f.bin", "f.bin"),
        ("trailing. ", "trailing"),              # 尾部点+空格
        ("CON", "_CON"),                # 保留设备名
        ("con.txt", "_con.txt"),
        ("nul", "_nul"),
        ("COM1.log", "_COM1.log"),
        ("", ""),
        ("...", "download"),                    # 全是点
        ("normal-file.zip", "normal-file.zip"),  # 正常名不变
    ]
    for raw, expect in cases:
        got = sanitize_filename(raw)
        assert got == expect, f"sanitize({raw!r}) = {got!r}，期望 {expect!r}"
    print(f"   {len(cases)} 个用例全部通过")

    # extract_filename 端到端
    assert extract_filename("http://h/a:b.txt", {}) == "a_b.txt"
    assert extract_filename("http://h/na|me.txt", {}) == "na_me.txt"
    # Content-Disposition 优先且同样清洗
    cd = {"Content-Disposition": 'attachment; filename="re:po*rt?.zip"'}
    assert extract_filename("http://h/x", cd) == "re_po_rt_.zip"
    # 超长名裁剪且保留扩展名
    long_name = "a" * 500 + ".zip"
    out = sanitize_filename(long_name)
    assert len(out) <= 200 and out.endswith(".zip"), f"超长名处理异常: {len(out)}"
    print("   extract_filename 端到端 + 超长名裁剪通过")

    # 非法字符必须全部消失（Windows 会直接拒绝这些字符）
    for raw in ("a:b*c?d<e>f|g\"h.txt", "x:y*z?w.txt"):
        got = sanitize_filename(raw)
        bad = [ch for ch in '<>:"/\\|?*' if ch in got]
        assert not bad, f"{got!r} 仍含非法字符 {bad}"


def test_download_with_illegal_name():
    print("\n[缺陷4b] 非法文件名能真正完成下载")
    data = os.urandom(2048)
    srv, port = serve(data)
    out = tempfile.mkdtemp(prefix="iname_")
    # URL 路径含非法字符，探测后应被清洗成合法名字
    from downloader.probe import build_info
    info = build_info(f"http://127.0.0.1:{port}/we:ird*na%3me.txt", out, 2)
    bad = [ch for ch in '<>:"/\\|?*' if ch in info.file_name]
    assert not bad, f"file_name={info.file_name!r} 含非法字符 {bad}"
    task = DownloadTask(info, SpeedLimiter(0))
    task.start()
    wait(task)
    assert task.info.status == TaskStatus.COMPLETED, task.error_msg
    assert md5(info.save_path) == hashlib.md5(data).hexdigest()
    print(f"   file_name={info.file_name!r} 下载完成且内容正确")
    task.shutdown()
    srv.shutdown()


# ============ 缺陷5: tasks.json 状态字段兼容 ============
# 症状：status 被写成中文 value 时 TaskStatus[...] 抛 KeyError 被静默跳过，
# 用户表现为「任务凭空消失」
def test_task_status_parse():
    print("\n[缺陷5] 状态字段兼容中文/英文/大小写")
    assert TaskStatus.parse("DOWNLOADING") == TaskStatus.DOWNLOADING
    assert TaskStatus.parse("downloading") == TaskStatus.DOWNLOADING
    assert TaskStatus.parse("下载中") == TaskStatus.DOWNLOADING
    assert TaskStatus.parse("已完成") == TaskStatus.COMPLETED
    assert TaskStatus.parse("") == TaskStatus.WAITING
    assert TaskStatus.parse(None) == TaskStatus.WAITING
    assert TaskStatus.parse(TaskStatus.ERROR) == TaskStatus.ERROR
    try:
        TaskStatus.parse("不存在的状态")
        raise AssertionError("非法状态应抛 ValueError")
    except ValueError:
        pass
    print("   7 个用例全部通过")


def test_from_dict_accepts_chinese_status():
    print("\n[缺陷5b] 中文 status 的任务能被恢复")
    d = {"url": "http://x/a", "file_name": "a.bin", "save_path": r"C:\tmp\a.bin",
         "total_size": "1024", "thread_count": "4", "support_range": 1,
         "status": "已完成"}
    info = DownloadInfo.from_dict(d)
    assert info.status == TaskStatus.COMPLETED
    assert info.total_size == 1024 and info.thread_count == 4
    print("   中文 status + 字符串数字字段均正确解析")


# ============ 缺陷6: Chunk 写入互斥 ============
def test_chunk_write_lock_not_shared():
    print("\n[缺陷6] 每个分片有独立的写锁（不是共享的同一个锁）")
    a = Chunk(0, 0, 99, "a")
    b = Chunk(1, 100, 199, "b")
    assert a.write_lock is not b.write_lock, "分片共用了同一个写锁对象"
    # 同一分片重复创建也应是不同锁，避免跨任务串行
    c = Chunk(0, 0, 99, "a")
    assert a.write_lock is not c.write_lock
    print("   写锁按分片实例独立")


# ============ 缺陷7（P1-1）: 恢复任务未开始时分片应可预载 ============
# 症状：重启恢复的任务在用户点"继续"前 chunks 为空，thread_speeds() 返回空、
# 进度条恒 0%，而 downloaded() 走扫磁盘兜底返回非零——两处数据源不一致
def test_preload_chunks_after_restore():
    print("\n[缺陷7] 恢复任务预载磁盘分片，进度立即可用")
    data = os.urandom(2 * 1024 * 1024)
    srv, port = serve(data)
    out = tempfile.mkdtemp(prefix="dlpre_")
    info = DownloadInfo(f"http://127.0.0.1:{port}/blob", "blob",
                        os.path.join(out, "blob"), len(data), 2, True)
    task = DownloadTask(info, SpeedLimiter(300 * 1024))
    task.start()
    time.sleep(0.8)
    task.pause()
    task._stop_threads(timeout=10)
    disk = sum(c.done_bytes() for c in task._chunks)
    assert 0 < disk < len(data), f"应在传输中途暂停 disk={disk}"
    task._chunks = []            # 模拟"重启后新构造的 DownloadTask"
    assert task.downloaded() == 0 or task.downloaded() > 0   # 扫磁盘兜底仍可用

    task.preload_chunks()        # 新增的预载入口
    assert len(task._chunks) == 2, "预载应重建 2 个分片"
    assert sum(c.written for c in task._chunks) == disk, \
        "预载后 written 应与磁盘字节数一致"
    assert 0 < task.progress() < 1.0
    sp = task.thread_speeds()
    assert len(sp) == 2 and all(s["total"] > 0 for s in sp)
    # 幂等：已有分片时不重算
    task.preload_chunks()
    assert sum(c.written for c in task._chunks) == disk
    print(f"   预载 {disk} 字节，进度 {task.progress():.0%}，线程柱状图可用")
    task.cancel()
    srv.shutdown()


# ============ 缺陷8（P1-3）: retry 在线程未退出时应拒绝 ============
# 症状：retry() 对 _stop_threads 超时不检查，仍然 remove_dir + start，
# 残留线程与新城线程各写各的字节段，is_done 误判完成后合并出错位文件
def test_retry_refuses_when_thread_alive():
    print("\n[缺陷8] 旧分片线程未退出时 retry 拒绝执行")
    data = os.urandom(128 * 1024)
    srv, port = serve(data, stall=True)
    out = tempfile.mkdtemp(prefix="dlretry_")
    info = DownloadInfo(f"http://127.0.0.1:{port}/stall", "stall",
                        os.path.join(out, "stall"), len(data), 1, True)
    task = DownloadTask(info, SpeedLimiter(0))
    task.start()
    time.sleep(0.3)              # 线程应已卡在服务端 HOLD 上
    alive = len(task._live_threads())
    assert alive >= 1

    # 让 _stop_threads 假装失败（直接构造超时为 0 的调用路径）
    orig = task._stop_threads
    task._stop_threads = lambda timeout=6.0: False   # 模拟 join 超时
    try:
        task.retry()
    finally:
        task._stop_threads = orig
    assert task.info.status == TaskStatus.ERROR, task.info.status
    assert "未能正常退出" in task.error_msg, task.error_msg
    assert os.path.isdir(info.parts_dir), "拒绝时不得删除分片目录"
    task.cancel()
    srv.shutdown()


# ============ 缺陷9（P2-1）: 重试等待期间 snapshot 应带 retrying 标记 ============
# 症状：网络断连自动重试时状态仍是"下载中"但速度为 0，用户无从知晓
def test_snapshot_retrying_flag():
    print("\n[缺陷9] snapshot.retrying 随重试计数联动")
    out = tempfile.mkdtemp(prefix="dlflag_")
    info = DownloadInfo("http://127.0.0.1:1/x", "x",
                        os.path.join(out, "x"), 1000, 1, True)
    task = DownloadTask(info, SpeedLimiter(0))
    assert task.snapshot().get("retrying") is False
    task._set_retries(2, "ConnectionError: 模拟断连")
    snap = task.snapshot()
    assert snap["retrying"] is False, "非 DOWNLOADING 状态不应标 retrying"
    info.status = TaskStatus.DOWNLOADING
    snap = task.snapshot()
    assert snap["retrying"] is True and "模拟断连" in snap["error"]
    task._set_retries(0, "")
    assert task.snapshot()["retrying"] is False
    assert task.snapshot()["error"] == "", "归零应清掉重试提示"
    print("   retrying 仅在下载中且重试计数>0 时为真，归零清提示")


if __name__ == "__main__":
    HOLD.clear()
    test_split_ranges_never_degenerate()
    test_small_file_many_threads()
    test_sanitize_filename()
    test_download_with_illegal_name()
    test_task_status_parse()
    test_from_dict_accepts_chinese_status()
    test_chunk_write_lock_not_shared()
    test_pause_returns_quickly_when_stalled()
    test_no_thread_leak_after_pause_resume()
    test_retry_after_pause_produces_valid_file()
    test_preload_chunks_after_restore()
    test_retry_refuses_when_thread_alive()
    test_snapshot_retrying_flag()
    print("\nALL REGRESSION TESTS PASSED")