"""下载任务：分片线程、自动重试、进度续传与合并"""
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass

import requests

from .info import DownloadInfo, TaskStatus
from .util import SpeedLimiter, parts_dir, remove_dir

logger = logging.getLogger("downloader")

MAX_RETRIES = 5
CHUNK_SIZE = 64 * 1024
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


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

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    def done_bytes(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def is_done(self) -> bool:
        return self.length >= 0 and self.done_bytes() >= self.length


def split_ranges(total: int, n: int) -> list[tuple[int, int]]:
    block = total // n
    ranges = []
    for i in range(n):
        start = i * block
        end = total - 1 if i == n - 1 else (i + 1) * block - 1
        ranges.append((start, end))
    return ranges


class DownloadTask:
    def __init__(self, info: DownloadInfo, limiter: SpeedLimiter):
        self.info = info
        self.limiter = limiter
        self.error_msg = ""
        self.speed = 0.0
        self._last_tick_bytes = 0
        self._last_tick_time = time.monotonic()
        self._lock = threading.Lock()
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
        self._on_done = None        # 回调（UI/管理器注入）

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
        return sum(os.path.getsize(os.path.join(d, f))
                   for f in os.scandir(d) if f.name.endswith(".part"))

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

    def start(self, on_done=None):
        """从当前进度开始/继续下载（幂等；等待中的任务由此真正启动）"""
        if self.info.status == TaskStatus.COMPLETED:
            return
        self._on_done = on_done or self._on_done
        with self._lock:
            if self.info.status == TaskStatus.DOWNLOADING:
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
            if not self._threads:
                self._finalize()

    def pause(self):
        if self.info.status != TaskStatus.DOWNLOADING:
            return
        self._paused.clear()
        self._stop_threads(timeout=15)   # 等待线程落盘退出，防止与续传线程重叠写
        self.info.status = TaskStatus.PAUSED

    def resume(self):
        if self.info.status != TaskStatus.PAUSED:
            return
        self.start()

    def retry(self):
        """重新下载：清空已下载分片，从头开始"""
        self._stop_threads()
        remove_dir(parts_dir(self.info.save_path))
        try:
            os.remove(self.info.save_path)
        except OSError:
            pass
        with self._lock:
            self.info.status = TaskStatus.WAITING
            self._chunks = []
            self.speed = 0.0
        self.start()

    def cancel(self):
        """取消并清理该任务的所有临时文件（.parts 目录、半截文件；保留已完成的最终文件）"""
        self._cancelled.set()
        self._paused.set()
        self._stop_threads()
        remove_dir(parts_dir(self.info.save_path))
        if self.info.status != TaskStatus.COMPLETED:
            try:
                os.remove(self.info.save_path)   # 合并中途失败的残留文件
            except OSError:
                pass
        self._chunks = []
        if self.info.status != TaskStatus.COMPLETED:
            self.info.status = TaskStatus.CANCELLED

    def shutdown(self):
        self._paused.set()
        self._cancelled.set()
        self._stop_threads(timeout=3)

    def _stop_threads(self, timeout=10):
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads = []

    # ---------- 工作线程 ----------

    def _run_chunk(self, chunk: Chunk):
        sess = requests.Session()
        sess.headers["User-Agent"] = UA
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
                try:
                    r = sess.get(self.info.url, headers=headers, stream=True,
                                 timeout=(10, 30), allow_redirects=True)
                    with r:
                        if range_ok and r.status_code == 200 \
                                and "Content-Range" not in r.headers:
                            # 服务器忽略了 Range 请求：整个任务降级为单线程整文件下载
                            if not self._convert_single(chunk):
                                return
                            continue
                        if r.status_code not in (200, 206):
                            raise requests.HTTPError(f"HTTP {r.status_code}")
                        mode = "wb" if cur == 0 or not range_ok else "ab"
                        if mode == "wb" and cur > 0:
                            cur = 0  # 不支持续传则重写
                        with open(chunk.path, mode) as fh:
                            if mode == "ab":
                                fh.seek(cur)
                            chunk.written = cur      # 计数器与磁盘对齐，供分片测速
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
                except (requests.RequestException, OSError) as e:
                    if self._cancelled.is_set() or not self._paused.is_set():
                        return
                    retries += 1
                    self.error_msg = f"{type(e).__name__}: {e}（第 {retries} 次重试）"
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
                retries += 1
                self.error_msg = f"连接中断，已重连重试（{retries}/{MAX_RETRIES}）"
                self._backoff(min(2 ** retries, 30))

            if not self._cancelled.is_set() and self._paused.is_set():
                self._fail("重试次数超限: " + self.error_msg)
        finally:
            sess.close()

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
        target = self.info.save_path
        try:
            if len(self._chunks) == 1 and self._chunks[0].length < 0:
                os.replace(self._chunks[0].path, target)
                remove_dir(parts_dir(target))
            else:
                with open(target, "wb") as out:
                    for c in sorted(self._chunks, key=lambda x: x.cid):
                        with open(c.path, "rb") as f:
                            shutil.copyfileobj(f, out, 1024 * 1024)
                remove_dir(parts_dir(target))
            self.info.status = TaskStatus.COMPLETED
            self.info.total_size = max(self.info.total_size, self._target_size())
            self.speed = 0.0
            cb = self._on_done
            if cb:
                threading.Thread(target=cb, args=(self.info.save_path,), daemon=True).start()
        except OSError as e:
            self._fail(f"合并文件失败: {e}")

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
