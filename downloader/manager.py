"""下载管理器：任务注册表、速度监控、任务列表持久化"""
import json
import logging
import os
import threading

from . import config
from .info import DownloadInfo, TaskStatus
from .probe import build_info
from .task import DownloadTask
from .util import SpeedLimiter

logger = logging.getLogger("downloader")

ACTIVE_STATUSES = (TaskStatus.DOWNLOADING, TaskStatus.WAITING)


def _silent_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


class DownloadManager:
    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self.settings = config.load_settings()
        self._lock = threading.RLock()
        self._save_lock = threading.Lock()   # 串行化 save_state，避免多线程写同一个 .tmp
        self._tasks: dict[str, DownloadTask] = {}
        self._order: list[str] = []
        self.limiter = SpeedLimiter(self.settings.get("speed_limit", 0) or 0)
        self.save_dir = self.settings.get("save_dir") or config.DEFAULT_SETTINGS["save_dir"]
        self.default_thread_count = self.settings.get("thread_count", 8)
        self._stop = threading.Event()
        self._monitor = threading.Thread(target=self._monitor_loop, daemon=True,
                                         name="dl-monitor")
        self._on_task_done = None   # 注入通知回调（UI 层）

    @classmethod
    def instance(cls) -> "DownloadManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ---------- 任务操作 ----------

    def add_download(self, url: str, save_dir: str | None = None,
                     thread_count: int | None = None) -> DownloadTask:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("请输入有效的 HTTP/HTTPS 链接")
        # 先在锁内判定，再在锁外做 cancel/清理 —— cancel 内部要 join 线程，
        # 放在锁内会把持有者锁的 UI 快照/持久化一起拖住。
        with self._lock:
            old = self._tasks.get(url)
            occupied = old is not None and old.info.status in (
                TaskStatus.DOWNLOADING, TaskStatus.PAUSED, TaskStatus.WAITING)
        if occupied:
            raise ValueError("该URL已在下载列表中")
        if old is not None:
            # 已结束(完成/取消/错误)的历史任务让位重下
            self.cancel(url)
        info = build_info(url, save_dir or self.save_dir,
                          thread_count or self.default_thread_count)
        task = DownloadTask(info, self.limiter)
        task._on_done = self._done_callback
        with self._lock:
            # 探测期间同一 URL 可能被重复添加（批量粘贴并发），这里做最终判定
            if url in self._tasks:
                raise ValueError("该URL已在下载列表中")
            self._tasks[url] = task
            self._order.append(url)
        logger.info("创建下载任务: %s 大小=%s 线程=%s 断点续传=%s",
                    info.file_name, info.total_size, info.thread_count, info.support_range)
        task.start()
        self.save_state()
        return task

    def get(self, url: str) -> DownloadTask | None:
        with self._lock:
            return self._tasks.get(url)

    def tasks(self) -> list[DownloadTask]:
        with self._lock:
            return [self._tasks[u] for u in self._order]

    def snapshots(self) -> list[dict]:
        return [t.snapshot() for t in self.tasks()]

    def pause(self, url): self._call(url, "pause")
    def resume(self, url): self._call(url, "resume")
    def retry(self, url): self._call(url, "retry")

    def _call(self, url, action):
        task = self.get(url)
        if task:
            getattr(task, action)()
            self.save_state()

    def cancel(self, url):
        """取消并从列表移除，清理临时分片"""
        with self._lock:
            task = self._tasks.pop(url, None)
            if url in self._order:
                self._order.remove(url)
        if task:
            task.cancel()
            self.save_state()

    def total_speed(self) -> float:
        return sum(t.speed for t in self.tasks()
                   if t.info.status == TaskStatus.DOWNLOADING)

    def set_speed_limit(self, bytes_per_sec: float):
        self.limiter.set_rate(bytes_per_sec)

    def update_settings(self, **kwargs):
        """持久化用户设置（保存目录/线程数/限速/完成后打开目录）"""
        self.settings.update(kwargs)
        config.save_settings(self.settings)

    def set_on_task_done(self, cb):
        self._on_task_done = cb

    def _done_callback(self, path: str):
        self.save_state()
        if self._on_task_done:
            try:
                self._on_task_done(path)
            except Exception:
                logger.exception("完成任务回调失败")

    # ---------- 监控线程 ----------

    def start_monitor(self):
        if self._monitor.is_alive():
            return
        # shutdown 后线程对象不能再 start()，重建线程并复位停止标记以支持复用
        self._stop.clear()
        self._monitor = threading.Thread(target=self._monitor_loop, daemon=True,
                                         name="dl-monitor")
        self._monitor.start()

    def _monitor_loop(self):
        while not self._stop.wait(1.0):
            for t in self.tasks():
                t.tick_speed()
            self.save_state()

    def shutdown(self):
        self._stop.set()
        with self._lock:
            tasks = list(self._tasks.values())
        for t in tasks:
            t.shutdown()
            if t.info.status in ACTIVE_STATUSES:
                t.info.status = TaskStatus.PAUSED
        self.save_state()

    # ---------- 持久化 ----------

    def save_state(self):
        """原子写任务列表。

        监控线程每秒调一次，而 _done_callback / 行操作也会调，多个线程会
        同时往同一个 .tmp 文件写 —— 在 Windows 上会撞出Permission denied，
        且 os.replace 可能把写了一半的内容换上去。故用独立的写锁串行化，
        并让临时文件名带上线程 id 兜底。
        """
        try:
            with self._lock:
                data = [t.info.to_dict() for t in self.tasks()]
            with self._save_lock:
                state_file = config.STATE_FILE
                os.makedirs(os.path.dirname(state_file), exist_ok=True)
                tmp = f"{state_file}.{os.getpid()}.{threading.get_ident()}.tmp"
                try:
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=1)
                    os.replace(tmp, state_file)
                except OSError:
                    _silent_remove(tmp)
                    raise
        except OSError as e:
            logger.warning("保存任务列表失败: %s", e)

    def load_state(self):
        state_file = config.STATE_FILE
        if not os.path.isfile(state_file):
            return
        try:
            with open(state_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("读取任务列表失败: %s", e)
            return
        restored = skipped = 0
        for d in data:
            if not isinstance(d, dict):
                skipped += 1
                continue
            try:
                info = DownloadInfo.from_dict(d)
            except (KeyError, ValueError, TypeError) as e:
                # 静默跳过会让用户看到"任务凭空消失"，必须留下日志线索
                skipped += 1
                logger.warning("任务列表中存在无法解析的条目，已跳过: %s (%s)",
                               (d or {}).get("url", "<无 url>"), e)
                continue
            if info.status in ACTIVE_STATUSES:
                info.status = TaskStatus.PAUSED   # 重启后不自动续传，等待用户继续
            task = DownloadTask(info, self.limiter)
            task._on_done = self._done_callback
            if info.status == TaskStatus.COMPLETED and not os.path.exists(info.save_path):
                info.status = TaskStatus.CANCELLED
            with self._lock:
                if info.url in self._tasks:
                    continue
                self._tasks[info.url] = task
                self._order.append(info.url)
            restored += 1
        logger.info("已恢复 %s 个历史任务（跳过 %s 个无法解析的条目）", restored, skipped)
        if skipped:
            logger.warning("有 %s 个任务未能恢复，可在 %s 中查看原始数据",
                           skipped, state_file)
