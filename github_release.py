# -*- coding: utf-8 -*-
"""GitHub Release 发布助手（一次性使用：把 dist 产物发布到 GitHub v3.2.1 Release）。

用法: python github_release.py create | check
凭证: 复用 release_publish.py 的 Gitee token 读取方式不行（那是 Gitee 的），
      这里从 Windows 凭证管理器标准条目 git:https://github.com 读取 PAT
      （由发布流程此前写入，GCM 亦可读）。
"""
import json
import os
import sys
import urllib.parse
import urllib.request

OWNER = "lingfeng983"
REPO = "downloads-tool"
TAG = "v3.2.1"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
TITLE = "多线程下载工具 v3.2.1"
BODY_FILE = os.path.join("build", "github_body.txt")
ASSETS = [
    os.path.join("dist", "多线程下载工具.exe"),
    os.path.join("dist", "多线程下载工具安装包.exe"),
]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def token():
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"].strip()
    # Windows 凭证管理器：CredRead W 条目 git:https://github.com
    import ctypes
    import ctypes.wintypes as wt

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", ctypes.c_uint), ("Type", ctypes.c_uint),
            ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p),
            ("LastWritten", wt.FILETIME), ("CredentialBlobSize", ctypes.c_uint),
            ("CredentialBlob", ctypes.c_void_p), ("Persist", ctypes.c_uint),
            ("AttributeCount", ctypes.c_uint), ("Attributes", ctypes.c_void_p),
            ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p),
        ]

    advapi = ctypes.windll.advapi32
    advapi.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_uint,
                                 ctypes.POINTER(ctypes.c_void_p)]
    ptr = ctypes.c_void_p()
    if not advapi.CredReadW("git:https://github.com", 1, 0, ctypes.byref(ptr)):
        sys.exit("no token: set GITHUB_TOKEN or store git:https://github.com credential")
    cred = ctypes.cast(ptr, ctypes.POINTER(CREDENTIAL)).contents
    blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
    advapi.CredFree(ptr)
    return blob.decode("utf-16-le")


def request(url, data=None, method=None, content_type=None, raw=False, extra_headers=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token()}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "downloads-tool-release")
    if content_type:
        req.add_header("Content-Type", content_type)
    for k, v in (extra_headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req) as resp:
        body = resp.read()
        if raw:
            return resp.status, dict(resp.headers), body
        return json.loads(body.decode("utf-8")) if body else {}


def get_release():
    try:
        return request(f"{API}/releases/tags/{TAG}")
    except urllib.error.HTTPError:
        return None


def create():
    with open(BODY_FILE, encoding="utf-8") as f:
        body = f.read().strip()
    rel = get_release()
    if rel is None:
        rel = request(f"{API}/releases", data=json.dumps({
            "tag_name": TAG, "target_commitish": "master",
            "name": TITLE, "body": body, "draft": False, "prerelease": False,
        }).encode("utf-8"), content_type="application/json")
        print(f"created release id={rel['id']}")
    else:
        print(f"release exists id={rel['id']}, will reuse")

    rid = rel["id"]
    # 上传附件（GitHub Release assets；同名/错名旧资产先删除，可重复执行）
    import uuid
    existing = {a["name"]: a["id"] for a in request(f"{API}/releases/{rid}/assets")}
    for junk in ("default.exe",):  # 中文名被 GitHub 改写成的错名资产
        if junk in existing:
            request(f"{API}/releases/assets/{existing[junk]}", method="DELETE")
            print(f"removed junk asset {junk}")
            del existing[junk]
    for path in ASSETS:
        if not os.path.isfile(path):
            sys.exit(f"asset not found: {path}")
        fname = os.path.basename(path)
        if fname in existing:
            request(f"{API}/releases/assets/{existing[fname]}", method="DELETE")
            print(f"removed old asset {fname}")
        data = open(path, "rb").read()
        # GitHub 要求文件名仅 ASCII：中文名上传前转写为英文带版本号
        upload_name = (fname.replace("多线程下载工具安装包", f"DownloadsTool-Setup-{TAG}")
                            .replace("多线程下载工具", f"DownloadsTool-{TAG}"))
        status, headers, _ = request(
            f"https://uploads.github.com/repos/{OWNER}/{REPO}/releases/{rid}/assets"
            f"?name={urllib.parse.quote(upload_name)}",
            data=data, raw=True,
            content_type="application/octet-stream",
            extra_headers={"Content-Type": "application/octet-stream"})
        print(f"uploaded {upload_name} ({len(data)} bytes, HTTP {status})")
    # 用最终文件名同步正文
    with open(BODY_FILE, encoding="utf-8") as f:
        body = f.read().strip()
    request(f"{API}/releases/{rid}", data=json.dumps({
        "tag_name": TAG, "name": TITLE, "body": body}).encode("utf-8"),
        method="PATCH", content_type="application/json")
    print("body updated")
    print(f"page=https://github.com/{OWNER}/{REPO}/releases/tag/{TAG}")


def check():
    rel = get_release()
    if rel is None:
        print("no release yet")
        return
    print(f"release id={rel['id']} name={rel['name']} draft={rel.get('draft')}")
    for a in request(f"{API}/releases/{rel['id']}/assets"):
        print(f"  asset: {a['name']} {a['size']} bytes -> {a['browser_download_url']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "create":
        create()
    else:
        check()
