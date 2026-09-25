"""后台添加下载任务（probe 是阻塞网络调用，避免卡 UI）"""
import threading

from PyQt5.QtCore import QObject, pyqtSignal


class AddWorker(QObject):
    done = pyqtSignal(str, str)      # url, file_name
    failed = pyqtSignal(str)         # error message
    finished = pyqtSignal(int, str)  # 成功数量, 失败摘要

    def __init__(self, manager):
        super().__init__()
        self.mgr = manager

    def add(self, url: str, save_dir: str, thread_count: int):
        self.add_batch([url], save_dir, thread_count)

    def add_batch(self, urls: list[str], save_dir: str, thread_count: int):
        def run():
            errors = []
            ok = 0
            for u in urls:
                try:
                    task = self.mgr.add_download(u, save_dir, thread_count)
                    ok += 1
                    self.done.emit(u, task.info.file_name)
                except Exception as e:
                    errors.append(f"{u}\n  → {e}")
            self.finished.emit(ok, "\n".join(errors))
        threading.Thread(target=run, daemon=True).start()
