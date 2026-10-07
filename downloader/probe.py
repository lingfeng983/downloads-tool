"""下载前的 HTTP 探测：文件大小、Range 支持、文件名"""
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse, unquote

import requests

from .info import DownloadInfo
from .util import format_size, has_enough_space, unique_path

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = (10, 15)

# Windows 文件名非法字符（同时含POSIX 禁止的 / 和控制字符）
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# 去掉尾部空格与点：Windows 会静默丢弃，导致后续 os.path.exists 判断失真
_TRAILING = re.compile(r"[ .]+$")
# Windows 保留设备名（不区分大小写），即使带扩展名也不能用
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)
}
MAX_NAME_LEN = 200#留出余量，避免撞上 255 的路径上限


def sanitize_filename(name: str) -> str:
    """把任意来源的文件名清洗成 Windows 上可安全创建的名字。

    不做清洗时，`a:b.txt` / `na|me.txt` 这类名字会让 open() 直接抛 OSError，
    任务从"下载成功"变成"莫名失败"。
    """
    if not name:
        return ""
    # 去掉路径成分（basename 前先统一分隔符，兼容 Content-Disposition 里的路径）
    name = name.replace("\\", "/").split("/")[-1]
    name = _ILLEGAL.sub("_", name)
    name = _TRAILING.sub("", name)
    # 保留扩展名的前提下裁长度
    if len(name) > MAX_NAME_LEN:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 16:
            name = stem[:MAX_NAME_LEN - len(ext) - 1] + dot + ext
        else:
            name = name[:MAX_NAME_LEN]
    # 保留设备名（含扩展名的形式也要处理），加前缀规避
    stem = name.split(".")[0].upper()
    if stem in _RESERVED:
        name = "_" + name
    if not name.strip("._"):
        name = "download"
    return name


@dataclass
class FileInfo:
    file_name: str
    file_size: int
    support_range: bool


def extract_filename(url: str, headers: dict) -> str:
    disp = headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*\s*=\s*[\w-]*''([^;]+)", disp, re.I)
    if not m:
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', disp, re.I)
    if m:
        name = sanitize_filename(unquote(m.group(1).strip().strip('"').strip()))
        if name:
            return name
    path = unquote(urlparse(url).path)
    name = sanitize_filename(os.path.basename(path))
    if name:
        return name
    ct = (headers.get("Content-Type") or "").split(";")[0].strip()
    ext = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg",
           "application/zip": ".zip", "video/mp4": ".mp4", "application/gzip": ".gz",
           "application/x-msdownload": ".exe"}.get(ct, "")
    return f"download_{int(time.time())}{ext}"


def probe(url: str) -> FileInfo:
    """HEAD 探测，失败时回退 GET Range 探测"""
    headers = {"User-Agent": USER_AGENT}
    size, support_range = -1, False
    try:
        r = requests.head(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        if r.ok:
            size = _to_int(r.headers.get("Content-Length"), -1)
            support_range = r.headers.get("Accept-Ranges", "").lower() == "bytes"
        if size > 0:
            return FileInfo(extract_filename(url, r.headers), size, support_range)
    except requests.RequestException:
        pass

    # HEAD 不可用（部分服务器禁用 HEAD，或返回 405），回退 GET + Range 探测。
    # 这段必须有异常处理：网络不可达时要在探测阶段就给出清晰的中文错误，
    # 而不是把 requests 的原始异常抛给用户。
    try:
        r = requests.get(url, headers={**headers, "Range": "bytes=0-0"},
                         timeout=TIMEOUT, stream=True, allow_redirects=True)
    except requests.RequestException as e:
        raise OSError(f"无法连接到服务器：{e}") from e
    try:
        if r.status_code >= 400:
            raise OSError(f"服务器返回 HTTP {r.status_code}")
        size, support_range = -1, False
        cr = r.headers.get("Content-Range")
        if r.status_code == 206 and cr and "/" in cr:
            size = _to_int(cr.rsplit("/", 1)[1], -1)
            support_range = True
        elif r.ok:
            size = _to_int(r.headers.get("Content-Length"), -1)
        return FileInfo(extract_filename(url, r.headers), size, support_range)
    finally:
        r.close()


def _to_int(v, default: int = -1) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def build_info(url: str, save_dir: str, thread_count: int) -> DownloadInfo:
    """探测并创建 DownloadInfo，校验磁盘空间"""
    fi = probe(url)
    target = unique_path(save_dir, fi.file_name)
    if fi.file_size > 0:
        # 下载期间 .parts 分片与最终文件同时占用空间（合并阶段是峰值），
        # 只查 1 份会在合并时写满磁盘。
        if not has_enough_space(save_dir, fi.file_size * 2):
            raise OSError(f"磁盘空间不足，无法保存 {target}"
                          f"（需要约 {format_size(fi.file_size * 2)}可用空间）")
    if not fi.support_range:
        thread_count = 1
    return DownloadInfo(
        url=url, file_name=os.path.basename(target), save_path=target,
        total_size=fi.file_size, thread_count=thread_count,
        support_range=fi.support_range,
    )
