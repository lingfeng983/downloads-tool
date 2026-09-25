"""工具函数：格式化与文件操作（对应原 FormatUtils / FileUtils）"""
import os
import shutil
import sys
import threading
import time


def resource_path(name: str) -> str:
    """随程序分发的资源文件路径（兼容源码运行与 PyInstaller 单文件）"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "assets", name)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "assets", name)


def format_size(size: int | float | None) -> str:
    if size is None or size < 0:
        return "未知"
    if size < 1024:
        return f"{int(size)} B"
    if size < 1024**2:
        return f"{size / 1024:.2f} KB"
    if size < 1024**3:
        return f"{size / 1024**2:.2f} MB"
    return f"{size / 1024**3:.2f} GB"


def format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec <= 0:
        return "0 B/s"
    return format_size(bytes_per_sec) + "/s"


def format_remaining(remaining_bytes: int, speed: float) -> str:
    if speed <= 0:
        return "计算中..."
    seconds = int(remaining_bytes / speed)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}小时{m}分钟"
    if m > 0:
        return f"{m}分钟{s}秒"
    return f"{s}秒"


def format_progress(ratio: float) -> str:
    return f"{ratio * 100:.1f}%"


def unique_path(directory: str, filename: str) -> str:
    """目标文件已存在且不属于本任务时，追加 (1)(2)... 避免覆盖"""
    path = os.path.join(directory, filename)
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(filename)
    i = 1
    while os.path.exists(os.path.join(directory, f"{stem}({i}){ext}")):
        i += 1
    return os.path.join(directory, f"{stem}({i}){ext}")


def has_enough_space(directory: str, required_bytes: int) -> bool:
    check = directory
    while check and not os.path.isdir(check):
        check = os.path.dirname(check)
    if not check:
        return False
    return shutil.disk_usage(check).free > required_bytes


def parts_dir(target_path: str) -> str:
    return target_path + ".parts"


def remove_dir(path: str):
    shutil.rmtree(path, ignore_errors=True)


class SpeedLimiter:
    """线程安全的令牌桶限速：每秒连续补充 rate 字节，rate<=0 表示不限速。
    支持部分授予，避免"整秒窗口+整块配额"造成多线程互相饿死、
    速度呈停顿/突发脉冲（进而导致剩余时间显示不出来）。"""

    def __init__(self, rate: float = 0):
        self.rate = rate
        self._lock = threading.Lock()
        self._tokens = rate
        self._last = time.monotonic()

    def set_rate(self, rate: float):
        with self._lock:
            self.rate = rate
            self._tokens = max(rate, 0)
            self._last = time.monotonic()

    def consume(self, nbytes: int):
        while nbytes > 0:
            with self._lock:
                if self.rate <= 0:
                    return
                now = time.monotonic()
                self._tokens = min(self.rate,
                                   self._tokens + (now - self._last) * self.rate)
                self._last = now
                take = min(nbytes, self._tokens)
                if take > 0:
                    self._tokens -= take
                    nbytes -= take
                    continue
                sleep_for = nbytes / self.rate
            time.sleep(min(max(sleep_for, 0.005), 0.1))
