"""任务状态与下载信息实体"""
import time
from dataclasses import dataclass, field
from enum import Enum

from .util import parts_dir


class TaskStatus(Enum):
    WAITING = "等待中"
    DOWNLOADING = "下载中"
    PAUSED = "已暂停"
    COMPLETED = "已完成"
    ERROR = "错误"
    CANCELLED = "已取消"

    @classmethod
    def parse(cls, value) -> "TaskStatus":
        """从持久化数据解析状态，兼容英文键（DOWNLOADING）与中文值（下载中）。

        任务列表文件是用户可见、可手工编辑的，不能因为一个字段写法不同
        就把整个任务丢掉。
        """
        if isinstance(value, cls):
            return value
        s = str(value or "").strip()
        if not s:
            return cls.WAITING
        try:
            return cls[s]                      # 标准写法：英文名
        except KeyError:
            pass
        try:
            return cls[s.upper()]             # 容错：小写英文
        except KeyError:
            pass
        for m in cls:                # 容错：中文显示值
            if m.value == s:
                return m
        raise ValueError(f"无法识别的任务状态: {value!r}")


@dataclass
class DownloadInfo:
    url: str
    file_name: str
    save_path: str          # 最终文件完整路径
    total_size: int         # -1 表示未知
    thread_count: int
    support_range: bool
    status: TaskStatus = TaskStatus.WAITING
    start_time: float = field(default_factory=time.time)

    @property
    def parts_dir(self) -> str:
        return parts_dir(self.save_path)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "file_name": self.file_name,
            "save_path": self.save_path,
            "total_size": self.total_size,
            "thread_count": self.thread_count,
            "support_range": self.support_range,
            "status": self.status.name,
            "start_time": self.start_time,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DownloadInfo":
        return cls(
            url=d["url"],
            file_name=d["file_name"],
            save_path=d["save_path"],
            total_size=int(d["total_size"]),
            thread_count=max(1, int(d["thread_count"])),
            support_range=bool(d["support_range"]),
            status=TaskStatus.parse(d.get("status", "WAITING")),
            start_time=float(d.get("start_time", time.time())),
        )
