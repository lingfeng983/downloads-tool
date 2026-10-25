"""配置与数据目录管理。

数据目录（存放 tasks.json / settings.json）解析优先级：
1. 命令行 --data-dir <路径>
2. 环境变量 DOWNLOADS_TOOL_DATA
3. 指针文件（上次通过 1/2/UI 设置过的自定义位置）
4. 默认 ~/.downloads_tool
"""
import argparse
import json
import logging
import os
import shutil
import threading

logger = logging.getLogger("downloader")

DEFAULT_DATA_DIR = os.path.join(os.path.expanduser("~"), ".downloads_tool")
POINTER_FILE = os.path.join(DEFAULT_DATA_DIR, "pointer.json")
ENV_KEY = "DOWNLOADS_TOOL_DATA"

DEFAULT_SETTINGS = {
    "save_dir": os.path.join(os.path.expanduser("~"), "Downloads"),
    "thread_count": 8,
    "speed_limit": 0,
    "auto_open": False,
    # 在线更新（GitHub 发行版）：上次检查时间 / 已跳过的版本 / 永久忽略更新提示
    "last_update_check": 0.0,
    "skipped_update_version": "",
    "update_ignored": False,
}

DATA_DIR = DEFAULT_DATA_DIR
STATE_FILE = os.path.join(DATA_DIR, "tasks.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")


def apply_data_dir(path: str, remember: bool = True):
    """切换数据目录；remember=True 时写指针文件，后续启动自动沿用"""
    global DATA_DIR, STATE_FILE, SETTINGS_FILE
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(path, exist_ok=True)
    old = DATA_DIR
    DATA_DIR = path
    STATE_FILE = os.path.join(path, "tasks.json")
    SETTINGS_FILE = os.path.join(path, "settings.json")
    if remember and path != DEFAULT_DATA_DIR:
        os.makedirs(DEFAULT_DATA_DIR, exist_ok=True)
        _write_json(POINTER_FILE, {"data_dir": path})
    elif remember and path == DEFAULT_DATA_DIR and os.path.isfile(POINTER_FILE):
        os.remove(POINTER_FILE)
    # 迁移旧位置的数据，任务列表不丢
    if old != path and os.path.isfile(os.path.join(old, "tasks.json")) \
            and not os.path.exists(STATE_FILE):
        for name in ("tasks.json", "settings.json"):
            src = os.path.join(old, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(path, name))


def init(argv=None):
    """解析命令行/环境变量/指针文件，确定数据目录"""
    parser = argparse.ArgumentParser(prog="downloader", add_help=True,
                                     description="多线程下载工具（Python 版）")
    parser.add_argument("--data-dir", help="自定义配置/任务数据保存目录")
    args, _ = parser.parse_known_args(argv)

    chosen = args.data_dir or os.environ.get(ENV_KEY)
    if not chosen and os.path.isfile(POINTER_FILE):
        try:
            with open(POINTER_FILE, encoding="utf-8") as f:
                chosen = json.load(f).get("data_dir")
        except (OSError, json.JSONDecodeError):
            chosen = None
    if chosen:
        apply_data_dir(chosen, remember=True)


def _write_json(path: str, data):
    """原子写。与 manager.save_state 可能并发，临时文件名带 pid 以免互相覆盖。"""
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            settings.update(json.load(f))
    except (OSError, json.JSONDecodeError):
        pass
    return settings


def save_settings(settings: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        _write_json(SETTINGS_FILE, settings)
    except OSError as e:
        # 静默吞掉会让用户以为设置已保存，重启后才发现没生效
        logger.warning("保存设置失败: %s", e)
