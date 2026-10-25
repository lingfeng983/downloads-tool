"""下载任务：分片线程、自动重试、进度续传与合并"""
import logging
import os
import re
import shutil
import socket
import threading
import time
from dataclasses import dataclass, field

import requests

from .info import DownloadInfo, TaskStatus
from .util import SpeedLimiter, parts_dir, remove_dir

logger = logging.getLogger("downloader")

MAX_RETRIES = 5
CHUNK_SIZE = 64 * 1024
READ_TIMEOUT = 15# 单次 socket 读超时。配合 _abort_responses() 的主动 close，
                    # 暂停/取消能在百毫秒级返回，不必等这个超时
STOP_JOIN_TIMEOUT = 6.0    # 停止分片线程的**总**等待预算（秒），非每线程
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _parse_content_range(value: str) -> tuple | None:
    """解析 'bytes 100-999/1234' → (start, total)；total 未知（*）为 -1；失败 None"""
    if not value:
        return None
    m = re.match(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", value.strip(), re.IGNORECASE)
    if not m:
        return None
    return (int(m.group(1)), -1 if m.group(3) == "*" else int(m.group(3)))


@dataclass
class Chunk:
    cid: int
    start: int           # -1 表示大小未知
    end: int             # -1 表示大小未知
    path: str
    written: int = 0          # 已落盘字节（会话内累计，用于分片测速）
    speed: float = 0.0        # 该分片线程的瞬时速度（字节/秒）
    finished: bool = False    # 分片已完成
    _last_written: int = 0
    # 分片级互斥锁（dataclass 里用 field 工厂创建，避免可变默认值共享）
    write_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def length(self) -> int:
        """分片应得的字节数；-1 表示大小未知（end 为 -1）。"""
        if self.end < 0 or self.start < 0:
            return -1
        return self.end - self.start + 1

    def done_bytes(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def is_done(self) -> bool:
        ln = self.length
        return ln >= 0 and self.done_bytes() >= ln


def split_ranges(total: int, n: int) -> list[tuple[int, int]]:
    """把 total 字节均分成 n 段。段数不会超过总字节数——否则会产生
    (x, -1) 这类退化区间（length=0却看着像合法分片），导致后续
    合并时去找一个根本不会创建的文件。
    """
    if total <= 0:
        return [(0, -1)]
    n = max(1, min(n, total))
    block = total // n
    ranges = []
    for i in range(n):
        start = i * block
        end = total - 1 if i == n - 1 else (i + 1) * block - 1
        ranges.append((start, end))
    return ranges


def _raw_socket(resp):
    """尽最大努力取出 requests 响应底层的 socket，用于中断阻塞读。

    requests/urllib3 各版本的内部结构不同，这里逐层尝试且全程容错——
    拿不到就退回read 超时路径，功能不会因此崩掉。
    """
    try:
        raw = resp.raw
        # urllib3>=2: raw._fp.fp.raw._sock
        for path in (("_fp", "fp", "raw", "_sock"),
                     ("_fp", "fp", "_sock"),
                     ("_connection", "sock"),
                     ("_fp", "fp")):
            obj = raw
            for attr in path:
                obj = getattr(obj, attr, None)
                if obj is None:
                    break
            if hasattr(obj, "shutdown"):
                return obj
        # HTTPS 经代理时连接藏在两层之下
        conn = getattr(raw, "_connection", None)
        if conn is not None:
            for attr in ("sock", "_sock"):
                s = getattr(conn, attr, None)
                if hasattr(s, "shutdown"):
                    return s
    except Exception:
        pass
    return None


class DownloadTask:
    def __init__(self, info: DownloadInfo, limiter: SpeedLimiter):
        self.info = info
        self.limiter = limiter
        self.error_msg = ""
        self.speed = 0.0
        self._last_tick_bytes = 0
        self._last_tick_time = time.monotonic()
        self._lock = threading.Lock()
        self._merge_lock = threading.Lock()   # 串行化 _finalize，避免重复合并
        self._merging = False
        self._paused = threading.Event()
        self._cancelled = threading.Event()
        self._paused.set()          # event set = 允许运行
        self._threads: list[threading.Thread] = []
        self._chunks: list[Chunk] = []
        self._done_count = 0
        self._failed = False
        self._merge_requested = threading.Event()
        self._superseded = threading.Event()   # 检测到服务器忽略 Range，其余分片线程退出
        self._single_owner = -1                # 接管整文件下载的线程 ident
        self._retries = 0                  # 当前分片的连续无进展重试次数（持锁读写）
        self._on_done = None        # 回调（UI/管理器注入）
        # 在途HTTP 响应：暂停/取消时主动 close，让阻塞在 socket 读上的线程
        # 立刻醒来。否则只能等 requests 的读超时（READ_TIMEOUT 秒）才退出，
        # 而 _stop_threads 是同步等待的 —— 这会让 UI 冻结十几秒。
        self._responses: dict[int, object] = {}

    # ---------- 查询 ----------

    @property
    def url(self):
        return self.info.url

    def downloaded(self) -> int:
        if self.info.status == TaskStatus.COMPLETED:
            return max(self.info.total_size, 0) or self._target_size()
        # 分片写入计数器比 stat 更准确（不受缓冲刷盘延迟影响），也省掉逐片 getsize
        total = sum(c.written for c in self._chunks) if self._chunks else 0
        if total == 0 and not self._chunks:
            # 未加载过分片（如重启恢复后尚未开始），尝试从磁盘目录恢复
            total = self._recover_dir_bytes()
        return total

    def _target_size(self) -> int:
        try:
            return os.path.getsize(self.info.save_path)
        except OSError:
            return 0

    def _recover_dir_bytes(self) -> int:
        d = parts_dir(self.info.save_path)
        if not os.path.isdir(d):
            return 0
        try:
            return sum(os.path.getsize(os.path.join(d, f))
                       for f in os.scandir(d) if f.name.endswith(".part"))
        except OSError:
            # isdir 通过后目录可能被并发 cancel() 删除；异常若传到监控线程
            # 会连带 _monitor_loop 死掉（速度/持久化全部停摆）
            return 0

    def progress(self) -> float:
        if self.info.total_size > 0:
            return min(self.downloaded() / self.info.total_size, 1.0)
        return 0.0

    def snapshot(self) -> dict:
        info = self.info
        downloaded = self.downloaded()
        chunks = self._chunks
        return {
            "url": info.url,
            "file_name": info.file_name,
            "save_path": info.save_path,
            "total_size": info.total_size,
            "downloaded": downloaded,
            "progress": self.progress(),
            "speed": self.speed if info.status == TaskStatus.DOWNLOADING else 0,
            "status": info.status,
            "thread_count": info.thread_count,
            "support_range": info.support_range,
            "error": self.error_msg,
            "chunks": self.thread_speeds() if chunks else [],
            # 重连等待中：状态仍是"下载中"但速度为 0，给 UI 一个可展示的提示
            "retrying": self._retries > 0 and info.status == TaskStatus.DOWNLOADING,
        }

    # ---------- 生命周期 ----------

    def _prepare_chunks(self):
        d = parts_dir(self.info.save_path)
        os.makedirs(d, exist_ok=True)
        self._chunks = []
        n = self.info.thread_count if self.info.support_range and self.info.total_size > 0 else 1
        if self.info.total_size > 0:
            for i, (s, e) in enumerate(split_ranges(self.info.total_size, n)):
                self._chunks.append(Chunk(i, s, e, os.path.join(d, f"chunk_{i}.part")))
        else:
            # 大小未知：单线程流式下载
            self._chunks.append(Chunk(0, -1, -1, os.path.join(d, "chunk_0.part")))
        for c in self._chunks:
            c.written = c._last_written = c.done_bytes()   # 续传起点对齐磁盘

    def preload_chunks(self):
        """为尚未开始的任务从磁盘 .parts 预载分片信息（幂等，只读）。

        重启恢复的任务在用户点"继续"前 self._chunks 为空：downloaded() 靠
        扫磁盘兜底，但 thread_speeds() 返回空、进度条与"大小"列数字来源
        不一致。构造后立即预载，让两处数据源统一（分片 written 对齐磁盘）。
        已有分片（下载中/已暂停）不重算，避免与工作线程竞争。
        """
        if self._chunks or self.info.status == TaskStatus.COMPLETED:
            return
        try:
            if not os.path.isdir(parts_dir(self.info.save_path)):
                return
            self._prepare_chunks()
        except OSError:
            self._chunks = []   # 磁盘异常时回退到旧的扫磁盘路径

    def _live_threads(self) -> list[threading.Thread]:
        return [t for t in self._threads if t.is_alive()]

    def start(self, on_done=None):
        """从当前进度开始/继续下载（幂等；等待中的任务由此真正启动）"""
        if self.info.status == TaskStatus.COMPLETED:
            return
        self._on_done = on_done or self._on_done
        # 上一轮若有线程没能在超时内退出（网络栈卡死等极端情况），
        # 必须先确保它们真的停了再起新线程，否则同一分片会被两个线程
        # 同时 "ab" 打开写，字节交错 → 文件损坏。
        if self._live_threads():
            if not self._stop_threads(timeout=STOP_JOIN_TIMEOUT):
                self.error_msg = "上一次下载线程未能正常退出，请稍后重试或取消该任务"
                logger.warning("start() 拒绝启动：%s 个旧分片线程仍存活",
                               len(self._live_threads()))
                self.info.status = TaskStatus.ERROR
                return
        with self._lock:
            # CANCELLED 的任务不允许复活：cancel() 与 start() 并发时，
            # 若在这里 clear 掉取消标志，任务会带着 CANCELLED 状态继续下载
            if self.info.status in (TaskStatus.DOWNLOADING, TaskStatus.CANCELLED):
                return
            self._cancelled.clear()
            self._paused.set()
            self._failed = False
            self.error_msg = ""
            self._superseded.clear()
            self._single_owner = -1
            if not self._chunks:
                self._prepare_chunks()
            self._done_count = sum(1 for c in self._chunks if c.is_done())
            self.info.status = TaskStatus.DOWNLOADING
            self._threads = []
            for c in self._chunks:
                if c.is_done():
                    continue
                t = threading.Thread(target=self._run_chunk, args=(c,), daemon=True,
                                     name=f"dl-{self.info.file_name}-{c.cid}")
                self._threads.append(t)
                t.start()
            need_finalize = not self._threads
        if need_finalize:
            # 必须在锁外调用：_finalize 内部也拿 self._lock（非重入），
            # 锁内调用会同线程二次加锁直接死锁（场景：分片已在磁盘完整但未合并）
            self._finalize()

    def pause(self):
        with self._lock:
            if self.info.status != TaskStatus.DOWNLOADING:
                return
            self.info.status = TaskStatus.PAUSED
            self._paused.clear()
        # 等待线程落盘退出，防止与续传线程重叠写（join 不持锁，避免拖死其他操作）
        if not self._stop_threads(timeout=STOP_JOIN_TIMEOUT):
            logger.warning("pause() 超时：%s 个分片线程仍在运行", len(self._live_threads()))

    def resume(self):
        if self.info.status != TaskStatus.PAUSED:
            return
        self.start()

    def retry(self):
        """重新下载：清空已下载分片，从头开始"""
        self._cancelled.set()
        self._paused.set()
        # 线程未在预算内退出时拒绝重试：继续删目录会让残留线程重建文件、
        # 与新起的线程双写同一分片（write_lock 只能防字节交错，防不了
        # 新旧线程各写各的字节段后 is_done 误判完成）。与 start() 的保护对齐。
        if not self._stop_threads():
            self._cancelled.clear()
            self._paused.set()
            self.error_msg = "下载线程未能正常退出，请稍后重试或取消该任务"
            logger.warning("retry() 拒绝执行：%s 个旧分片线程仍存活",
                           len(self._live_threads()))
            with self._lock:
                if self.info.status != TaskStatus.COMPLETED:
                    self.info.status = TaskStatus.ERROR
            return
        remove_dir(parts_dir(self.info.save_path))
        try:
            os.remove(self.info.save_path)
        except OSError:
            pass
        with self._lock:
            self.info.status = TaskStatus.WAITING
            self._chunks = []
            self.speed = 0.0
        self._cancelled.clear()
        self.start()

    def cancel(self):
        """取消并清理该任务的所有临时文件（.parts 目录、半截文件；保留已完成的最终文件）"""
        self._cancelled.set()
        self._paused.set()
        stopped = self._stop_threads()    # join 不持锁
        # 线程仍在写时不能删目录，否则它们会重建文件/写入失败；
        # 目录残留由 shutdown() 兜底清理。
        if stopped:
            remove_dir(parts_dir(self.info.save_path))
            if self.info.status != TaskStatus.COMPLETED:
                try:
                    os.remove(self.info.save_path)   # 合并中途失败的残留文件
                except OSError:
                    pass
        else:
            logger.warning("cancel() 超时：%s 个分片线程仍在运行，延后清理临时文件",
                           len(self._live_threads()))
        with self._lock:
            self._chunks = []
            if self.info.status != TaskStatus.COMPLETED:
                self.info.status = TaskStatus.CANCELLED

    def shutdown(self):
        self._paused.set()
        self._cancelled.set()
        self._stop_threads(timeout=STOP_JOIN_TIMEOUT)

    def _abort_responses(self):
        """中断所有在途连接，让阻塞在 socket 读上的线程立刻醒来退出。

        实测（Windows + Python 3.14）：
        - shutdown(SHUT_RDWR) 对"无未读数据"的阻塞 recv **不生效**（会挂到
          读超时），而且 shutdown 之后再 close 也无法中断挂起的 recv；
        - 普通 sock.close() 因 http.client 的 makefile 引用（_io_refs）只减
          计数、不真正关闭 fd；
        - 唯一可靠的是 `_real_close()` 直接 closesocket，阻塞的 recv 立刻以
          OSError(10038) 醒来。响应对象的收尾交给后台线程 r.close()。
        """
        with self._lock:
            items = list(self._responses.values())
            self._responses.clear()
        if not items:
            return
        for r in items:
            sock = _raw_socket(r)
            if sock is not None:
                real_close = getattr(sock, "_real_close", None)
                if real_close is not None:
                    try:
                        real_close()
                    except OSError:
                        pass
                else:
                    try:
                        sock.close()
                    except OSError:
                        pass
        def _close_all():
            for r in items:
                try:
                    r.close()
                except Exception:
                    pass
        threading.Thread(target=_close_all, daemon=True,
                         name="dl-abort").start()

    def _stop_threads(self, timeout=STOP_JOIN_TIMEOUT):
        """停止所有分片线程。返回 True 表示已全部退出。

        timeout 是**总预算**而非每线程预算：原实现是逐个 join(timeout)，
        16 线程最坏要等 15×16=240 秒。这里改成共享一份时间预算，
        并先中断在途连接把等待压到百毫秒级。
        """
        if not self._threads:
            return True
        self._abort_responses()
        deadline = time.monotonic() + timeout
        for t in self._threads:
            remain = deadline - time.monotonic()
            if remain <= 0:
                break
            t.join(timeout=remain)
        alive = self._live_threads()
        # 保留未退出线程的引用（不能直接清空），否则它们会脱离管理，
        # 与后续新起的线程同时写同一个分片。
        self._threads = alive
        return not alive

    # ---------- 工作线程 ----------

    def _track_response(self, r):
        with self._lock:
            self._responses[threading.get_ident()] = r

    def _untrack_response(self):
        with self._lock:
            self._responses.pop(threading.get_ident(), None)

    def _run_chunk(self, chunk: Chunk):
        sess = requests.Session()
        sess.headers["User-Agent"] = UA
        # 禁止传输层压缩：requests 默认带 Accept-Encoding: gzip 且透明解压，
        # 解压后字节数 ≠ Range 偏移，分片文件会超出 length 且内容错位 → 合并损坏
        sess.headers["Accept-Encoding"] = "identity"
        retries = 0
        try:
            while retries < MAX_RETRIES:
                if self._wait_flags():
                    return
                cur = chunk.done_bytes()
                if chunk.is_done():
                    self._mark_done(chunk)
                    return
                headers = {}
                range_ok = chunk.end >= 0 and self.info.support_range
                if range_ok:
                    headers["Range"] = f"bytes={chunk.start + cur}-{chunk.end}"
                before = chunk.written
                try:
                    r = sess.get(self.info.url, headers=headers, stream=True,
                                 timeout=(10, READ_TIMEOUT), allow_redirects=True)
                    self._track_response(r)
                    try:
                        with r:
                            if range_ok and r.status_code == 200:
                                # 服务器忽略 Range：整个任务降级为单线程整文件下载
                                # （无论有没有 Content-Range，200 响应体都是从 0 开始的完整内容）
                                if not self._convert_single(chunk):
                                    return
                                continue
                            if r.status_code == 206:
                                # 续传前校验 Content-Range 与本地记录一致，
                                # 防止远端文件已变更（大小/偏移不同）导致新旧内容错位拼接
                                cr = _parse_content_range(r.headers.get("Content-Range"))
                                if cr is not None:
                                    cstart, ctotal = cr
                                    if cstart != chunk.start + cur or (
                                            ctotal > 0 and self.info.total_size > 0
                                            and ctotal != self.info.total_size):
                                        self._fail("远端文件已变化（Content-Range 与本地记录不符），"
                                                   "请移除任务后重新添加")
                                        return
                            if r.status_code not in (200, 206):
                                raise requests.HTTPError(f"HTTP {r.status_code}",
                                                         response=r)
                            mode = "wb" if cur == 0 or not range_ok else "ab"
                            if mode == "wb" and cur > 0:
                                cur = 0  # 不支持续传则重写
                            # 分片级写锁：正常情况下同一分片只有一个线程在写，
                            # 这是防止"上一轮线程未退出 + 新一轮线程启动"造成
                            # 双写交错损坏文件的最后一道防线。
                            with chunk.write_lock:
                                with open(chunk.path, mode) as fh:
                                    if mode == "ab":
                                        fh.seek(cur)
                                    # 计数器与磁盘对齐，供分片测速
                                    chunk.written = cur
                                    chunk._last_written = cur
                                    chunk.speed = 0.0
                                    for block in r.iter_content(CHUNK_SIZE):
                                        if self._wait_flags():
                                            fh.flush()
                                            return
                                        self.limiter.consume(len(block))
                                        fh.write(block)
                                        chunk.written += len(block)
                                        if self._cancelled.is_set():
                                            return
                    finally:
                        self._untrack_response()
                except (requests.RequestException, OSError) as e:
                    if self._cancelled.is_set() or not self._paused.is_set():
                        return
                    if isinstance(e, requests.HTTPError) and e.response is not None \
                            and e.response.status_code in (400, 401, 403, 404, 410):
                        # 永久性错误重试无意义（原实现会退避重试 5 次共约 1 分钟）
                        self._fail(f"HTTP {e.response.status_code}："
                                   f"服务器拒绝该请求（链接失效或需要权限）")
                        return
                    if isinstance(e, OSError) and not isinstance(
                            e, (requests.RequestException, ConnectionError)):
                        # 裸 OSError 基本是本地写盘问题（磁盘满/权限），重试不会好转
                        self._fail(f"本地写入失败: {e}")
                        return
                    if chunk.written > before:
                        retries = 0      # 本次连接有实际进展，只统计连续无进展的失败
                        self._set_retries(0, "")
                    else:
                        retries += 1
                        self._set_retries(retries, f"{type(e).__name__}: {e}")
                    self._backoff(min(2 ** retries, 30))
                    continue

                # 流正常结束
                if chunk.end < 0:
                    # 未知大小：以实际字节数补全信息
                    actual = chunk.done_bytes()
                    self.info.total_size = actual
                    chunk.start, chunk.end = 0, actual - 1
                    self._mark_done(chunk)
                    return
                if chunk.is_done():
                    self._mark_done(chunk)
                    return
                retries = 0 if chunk.written > before else retries + 1
                self._set_retries(retries,
                                  f"连接中断，已重连重试（{retries}/{MAX_RETRIES}）")
                self._backoff(min(2 ** retries, 30))

            if not self._cancelled.is_set() and self._paused.is_set():
                self._fail("重试次数超限: " + self.error_msg)
        finally:
            sess.close()

    def _set_retries(self, n: int, msg: str):
        """线程安全记录重试状态；n 归零时同时清掉提示信息。

        此处 error_msg 只在重试等待期间有意义（snapshot 的 retrying 标记），
        归零清空避免上一轮的"第 N 次重试"残留到任务完成后。
        """
        with self._lock:
            self._retries = n
            if msg:
                self.error_msg = msg
            elif n == 0:
                self.error_msg = ""

    def _convert_single(self, chunk: Chunk) -> bool:
        """服务器声称支持 Range 却忽略 Range（返回整文件 200）时，
        由第一个发现的线程接管为单线程整文件下载，其余分片线程退出。
        返回 False 表示已有其他线程接管，本线程应退出。"""
        with self._lock:
            if self._superseded.is_set():
                return False
            total = self.info.total_size
            chunk.start = 0
            chunk.end = total - 1 if total > 0 else -1
            chunk.path = os.path.join(parts_dir(self.info.save_path), "chunk_full.part")
            self._chunks = [chunk]
            self._done_count = 0
            self.info.support_range = False
            self.info.thread_count = 1
            self._single_owner = threading.get_ident()
            self._superseded.set()
        logger.info("服务器忽略 Range 请求，已降级为单线程下载: %s", self.info.file_name)
        return True

    def _wait_flags(self) -> bool:
        """返回 True 表示需要退出线程（取消/暂停/已被单线程接管）。
        暂停时线程直接退出，进度已落盘到 .part；继续时由 start() 重新起线程续传。"""
        if self._cancelled.is_set() or not self._paused.is_set():
            return True
        return (self._superseded.is_set()
                and threading.get_ident() != self._single_owner)

    def _backoff(self, seconds: float):
        """可中断退避：暂停/取消时立即返回"""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._wait_flags():
                return
            time.sleep(0.1)

    def _mark_done(self, chunk: Chunk | None = None):
        with self._lock:
            if chunk is not None and chunk not in self._chunks:
                return          # 单线程接管后，旧分片线程的完成信号作废
            if chunk is not None:
                chunk.finished = True
                chunk.speed = 0.0
            self._done_count += 1
            all_done = self._done_count >= len(self._chunks)
        if all_done:
            self._finalize()

    def _fail(self, msg: str):
        self.error_msg = msg
        self._paused.set()
        with self._lock:
            if self.info.status == TaskStatus.DOWNLOADING:
                self.info.status = TaskStatus.ERROR

    # ---------- 合并 ----------

    def _finalize(self):
        """合并分片到最终文件。可被多个路径触发，用 _merge_lock 保证只跑一次。"""
        with self._lock:
            if self._merging:
                return
            self._merging = True
        target = self.info.save_path
        try:
            with self._merge_lock:
                if len(self._chunks) == 1 and self._chunks[0].length < 0:
                    os.replace(self._chunks[0].path, target)
                    remove_dir(parts_dir(target))
                else:
                    total_written = 0
                    with open(target, "wb") as out:
                        for c in sorted(self._chunks, key=lambda x: x.cid):
                            # length<=0 的分片本就不该存在（split_ranges 已裁掉），
                            # 这里再挡一层：缺失文件跳过而不是让整个合并失败
                            if c.length == 0 or not os.path.exists(c.path):
                                logger.warning("合并时跳过分片 %s（文件缺失）", c.cid)
                                continue
                            if c.length > 0 and os.path.getsize(c.path) != c.length:
                                # 分片字节数与预期不符（如传输层被压缩/远端文件变更），
                                # 合并只会产出损坏文件，宁可报错也不静默损坏
                                raise OSError(
                                    f"分片 {c.cid} 大小不符（{os.path.getsize(c.path)}"
                                    f"/{c.length}），已放弃合并")
                            with open(c.path, "rb") as f:
                                shutil.copyfileobj(f, out, 1024 * 1024)
                            total_written += c.length
                    if self.info.total_size > 0 and total_written != self.info.total_size:
                        raise OSError(
                            f"合并总量 {total_written} 与文件大小 {self.info.total_size} 不符")
                    remove_dir(parts_dir(target))
            with self._lock:
                self.info.status = TaskStatus.COMPLETED
            self.info.total_size = max(self.info.total_size, self._target_size())
            self.speed = 0.0
            cb = self._on_done
            if cb:
                threading.Thread(target=cb, args=(self.info.save_path,), daemon=True).start()
        except OSError as e:
            self._fail(f"合并文件失败: {e}")
        finally:
            with self._lock:
                self._merging = False

    # ---------- 速度 ----------

    def tick_speed(self):
        """由管理器定时调用（约每秒），计算瞬时速度（滑动平均防跳零）。
        同时逐分片计算各下载线程自己的速度。"""
        now = time.monotonic()
        cur = self.downloaded()
        dt = max(now - self._last_tick_time, 1e-3)
        active = self.info.status == TaskStatus.DOWNLOADING
        if active:
            inst = (cur - self._last_tick_bytes) / dt
            if self.speed > 0:
                self.speed = round(0.5 * self.speed + 0.5 * inst, 1)
            else:
                self.speed = round(inst, 1)
        else:
            self.speed = 0.0
        for c in self._chunks:
            if not active or c.finished:
                c.speed = 0.0
            else:
                raw = (c.written - c._last_written) / dt
                c.speed = round(0.5 * c.speed + 0.5 * max(raw, 0.0), 1) if c.speed > 0 \
                    else round(max(raw, 0.0), 1)
            c._last_written = c.written
        self._last_tick_bytes = cur
        self._last_tick_time = now

    def thread_speeds(self) -> list[dict]:
        """各分片线程的实时速度，供 UI 展示"""
        out = []
        for c in sorted(self._chunks, key=lambda x: x.cid):
            total = c.length
            done = c.written
            out.append({
                "cid": c.cid,
                "speed": c.speed if not c.finished else 0.0,
                "done": done,
                "total": total,
                "percent": (min(done / total, 1.0) * 100 if total > 0 else 0.0),
                "finished": c.finished,
            })
        return out
