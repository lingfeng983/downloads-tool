"""任务状态与下载信息实体"""
import time
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(Enum):
    WAITING = "等待中"
    DOWNLOADING = "下载中"
    PAUSED = "已暂停"
    COMPLETED = "已完成"
    ERROR = "错误"
    CANCELLED = "已取消"


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
    def target_path(self) -> str:
        return self.save_path

    @property
    def parts_dir(self) -> str:
        return self.save_path + ".parts"

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
            total_size=d["total_size"],
            thread_count=d["thread_count"],
            support_range=d["support_range"],
            status=TaskStatus[d.get("status", "WAITING")],
            start_time=d.get("start_time", time.time()),
        )
