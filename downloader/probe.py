"""下载前的 HTTP 探测：文件大小、Range 支持、文件名"""
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse, unquote

import requests

from .info import DownloadInfo
from .util import has_enough_space, unique_path

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = (10, 15)


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
        name = unquote(m.group(1).strip().strip('"').strip())
        name = os.path.basename(name.replace("\\", "/"))
        if name:
            return name
    path = unquote(urlparse(url).path)
    name = os.path.basename(path)
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
            size = int(r.headers.get("Content-Length", -1))
            support_range = r.headers.get("Accept-Ranges", "").lower() == "bytes"
        if size > 0:
            return FileInfo(extract_filename(url, r.headers), size, support_range)
    except requests.RequestException:
        pass

    r = requests.get(url, headers={**headers, "Range": "bytes=0-0"},
                     timeout=TIMEOUT, stream=True, allow_redirects=True)
    try:
        if r.status_code >= 400:
            raise OSError(f"服务器返回 HTTP {r.status_code}")
        size, support_range = -1, False
        cr = r.headers.get("Content-Range")
        if r.status_code == 206 and "/" in cr:
            try:
                size = int(cr.rsplit("/", 1)[1])
            except ValueError:
                pass
            support_range = True
        elif r.ok:
            size = int(r.headers.get("Content-Length", -1))
        return FileInfo(extract_filename(url, r.headers), size, support_range)
    finally:
        r.close()


def build_info(url: str, save_dir: str, thread_count: int) -> DownloadInfo:
    """探测并创建 DownloadInfo，校验磁盘空间"""
    fi = probe(url)
    target = unique_path(save_dir, fi.file_name)
    if fi.file_size > 0 and not has_enough_space(save_dir, fi.file_size):
        raise OSError(f"磁盘空间不足，无法保存 {target}")
    if not fi.support_range:
        thread_count = 1
    return DownloadInfo(
        url=url, file_name=os.path.basename(target), save_path=target,
        total_size=fi.file_size, thread_count=thread_count,
        support_range=fi.support_range,
    )
