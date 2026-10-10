"""在线更新：从 Gitee 发行版检查新版本、下载新版 exe、替换自身并重启。

版本源：https://gitee.com/api/v5/repos/<owner>/<repo>/releases（公开接口，无需令牌）。
tag 约定 vN / vN.N / vN.N.N，取其数字段做语义化比较；比较时把本机 __version__
末尾的 .0 视作 2 段（"2.0.0" 对应标签 v2.0），避免等于最新 tag 的已发布包
被误报为可更新。
"""
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

from PyQt5.QtCore import QObject, pyqtSignal

logger = logging.getLogger("downloader")

OWNER = "lingfeng983"
REPO = "downloads-tool"
API = "https://gitee.com/api/v5"
RELEASE_PAGE = f"https://gitee.com/{OWNER}/{REPO}/releases"
CHECK_INTERVAL = 24 * 3600        # 自动检查的最小间隔（秒）
DOWNLOAD_CEILING = 200 * 1024 * 1024   # 内容长度上限，防异常响应写满磁盘

UA = "Mozilla/5.0 (compatible; DownloadsTool-Updater)"


def version_tuple(tag: str) -> tuple:
    return tuple(int(p) for p in re.findall(r"\d+", str(tag or "")))


def _release_score(rel: dict) -> tuple:
    return (version_tuple(rel.get("tag_name", "")),
            str(rel.get("created_at", "")), int(rel.get("id") or 0))


def pick_latest(releases: list) -> dict:
    """选出版本号最大且带 exe 附件的正式发行版"""
    candidates = [r for r in releases
                  if not r.get("prerelease")
                  and any(str(a.get("name", "")).lower().endswith(".exe")
                          for a in (r.get("assets") or []))]
    return max(candidates, key=_release_score) if candidates else {}


def exe_asset(rel: dict) -> dict:
    assets = [a for a in (rel.get("assets") or [])
              if str(a.get("name", "")).lower().endswith(".exe")]
    return assets[0] if assets else {}


def parse_release_tag(tag: str) -> str:
    """v3 -> 3；解析不出数字返回空串"""
    return ".".join(str(p) for p in version_tuple(tag))


def is_newer(latest: str, current: str) -> bool:
    """语义化比较，缺段补 0；latest 为空表示无可用版本"""
    if not latest or not current:
        return False
    lp = [int(p) for p in latest.split(".") if p.isdigit()]
    cp = [int(p) for p in current.split(".") if p.isdigit()]
    if len(cp) == 3 and cp[2] == 0:
        cp = cp[:2]
    n = max(len(lp), len(cp))
    lp += [0] * (n - len(lp))
    cp += [0] * (n - len(cp))
    return lp > cp


def _request_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_update(local_version: str) -> dict:
    """返回 {"ok": bool, "available": bool, ...}；网络失败 ok=False"""
    url = f"{API}/repos/{OWNER}/{REPO}/releases?per_page=100"
    try:
        releases = _request_json(url)
        if isinstance(releases, dict):      # 个别接口只返回单个对象
            releases = [releases]
        rel = pick_latest(releases)
    except Exception as e:
        logger.warning("检查更新失败: %s", e)
        return {"ok": False, "error": f"无法连接 Gitee 获取最新版本（{e}）"}
    if not rel:
        return {"ok": True, "available": False}
    latest = parse_release_tag(rel.get("tag_name", ""))
    if not is_newer(latest, local_version):
        return {"ok": True, "available": False}
    html = rel.get("html_url") or f"{RELEASE_PAGE}/tag/{rel.get('tag_name', '')}"
    asset = exe_asset(rel)
    name = asset.get("name") or "新版本.exe"
    dl = asset.get("browser_download_url") or _asset_fallback_url(rel, name)
    return {
        "ok": True,
        "available": True,
        "tag": rel.get("tag_name", ""),
        "version": latest,
        "title": rel.get("name") or rel.get("tag_name", ""),
        "body": (rel.get("body") or "")[:4000],
        "page_url": html,
        "file_name": name,
        "download_url": dl,
    }


def _asset_fallback_url(rel: dict, name: str) -> str:
    """Gitee 附件直链有固定形态：/releases/download/<tag>/<urlencoded 文件名>"""
    return (f"https://gitee.com/{OWNER}/{REPO}/releases/download/"
            f"{urllib.parse.quote(str(rel.get('tag_name', '')))}/"
            f"{urllib.parse.quote(name)}")


def is_frozen_exe() -> bool:
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def _run_detached(cmd: list):
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs = {"creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                  "close_fds": True}
    else:
        kwargs = {"start_new_session": True, "close_fds": True}
    subprocess.Popen(cmd, **kwargs)


def write_restart_script(new_exe: str, target_exe: str) -> str:
    """生成替换脚本：等旧进程释放 exe 锁 → 覆盖 → 重启新版"""
    script = os.path.join(os.path.dirname(new_exe) or ".", "_update_restart.bat")
    body = f"""@echo off
rem wait up to 5 min for {os.path.basename(target_exe)} to be released
set /a tries=0
:wait
del "{target_exe}" 2>nul
if exist "{target_exe}" (
    set /a tries+=1
    if %tries% geq 300 (
        copy /y "{new_exe}" "{target_exe}.new" >nul 2>nul
        exit /b 2
    )
    ping -n 2 127.0.0.1 >nul
    goto wait
)
copy /y "{new_exe}" "{target_exe}" >nul
if errorlevel 1 (
    copy /y "{new_exe}" "{target_exe}.new" >nul 2>nul
    del "{new_exe}" 2>nul
    exit /b 3
)
del "{new_exe}" 2>nul
start "" "{target_exe}"
del "%~f0" 2>nul
exit /b 0
"""
    try:
        with open(script, "w", encoding="mbcs", errors="replace") as f:
            f.write(body)
    except (LookupError, OSError):
        with open(script, "w", encoding="gbk", errors="replace") as f:
            f.write(body)
    return script


def apply_update(new_exe: str, info: dict, parent=None):
    """下载完成后的落地动作：打包版生成替换脚本并返回脚本路径；否则打开所在目录"""
    if not new_exe or not os.path.isfile(new_exe):
        return None
    if not is_frozen_exe():
        return None
    target = sys.executable
    script = write_restart_script(new_exe, target)
    _run_detached(["cmd.exe", "/c", script])
    return script


class UpdateWorker(QObject):
    """检查与下载都在后台线程执行，信号回主线程"""
    checked = pyqtSignal(dict)
    download_progress = pyqtSignal(int, int)       # 已下载字节, 总字节（未知为 -1）
    download_done = pyqtSignal(str)                # 落盘路径
    download_failed = pyqtSignal(str)

    def __init__(self, local_version: str = ""):
        super().__init__()
        if not local_version:
            from . import __version__ as local_version
        self.local_version = local_version
        self._cancel = False
        self._thread = None

    def check(self):
        self._spawn("update-check", lambda: self._do_check())

    def start_download(self, url: str, file_name: str):
        self._cancel = False
        self._spawn("update-download", lambda: self._do_download(url, file_name))

    def cancel_download(self):
        self._cancel = True

    def _spawn(self, name: str, fn):
        def run():
            try:
                fn()
            except Exception as e:      # 兜底：线程内异常不能静默丢失
                logger.warning("%s 线程异常: %s", name, e)
                self.download_failed.emit(str(e))
        self._thread = threading.Thread(target=run, daemon=True, name=name)
        self._thread.start()

    def _do_check(self):
        info = fetch_update(self.local_version)
        if info.get("available"):
            info["local_version"] = self.local_version
        self.checked.emit(info)

    def _download_dir(self) -> str:
        from . import config
        path = os.path.join(config.DATA_DIR, "update")
        os.makedirs(path, exist_ok=True)
        return path

    def _do_download(self, url: str, file_name: str):
        safe = os.path.basename(file_name) or "update.exe"
        dest = os.path.join(self._download_dir(), safe)
        tmp = dest + ".part"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as out:
                total = int(resp.headers.get("Content-Length") or 0)
                if total > DOWNLOAD_CEILING:
                    raise ValueError(f"响应体积异常（{total} 字节），已中止")
                done = 0
                while True:
                    if self._cancel:
                        raise InterruptedError("已取消下载")
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if done > DOWNLOAD_CEILING:
                        raise ValueError("下载体积超出预期，已中止")
                    self.download_progress.emit(done, total or -1)
            os.replace(tmp, dest)
            self.download_done.emit(dest)
        except Exception as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            logger.warning("更新下载失败: %s", e)
            self.download_failed.emit(f"下载失败：{e}")
