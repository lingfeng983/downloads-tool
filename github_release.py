# -*- coding: utf-8 -*-
"""GitHub Release 发布助手（被 release.sh 调用；也可单独运行）。

用法:
  python github_release.py create <tag> <title_file> <body_file>
  python github_release.py check [tag]

行为：tag 无对应发行版则创建，已存在则更新标题/正文；
把 dist 下的 exe 作为资产上传（同名旧资产先删除，保证可重复执行）。
GitHub 资产名仅限 ASCII，中文产物名自动转写：
  多线程下载工具.exe        -> DownloadsTool-<tag>.exe
  多线程下载工具安装包.exe  -> DownloadsTool-Setup-<tag>.exe
正文里引用的下载文件名也会同步替换（Gitee 侧仍用中文名，两平台正文独立）。

凭证：环境变量 GITHUB_TOKEN，或 Windows 凭证管理器标准条目
git:https://github.com（git push 凭证同源，GCM 亦可读）。
"""
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request

OWNER = "lingfeng983"
REPO = "downloads-tool"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"

PORTABLE_EXE = os.path.join("dist", "多线程下载工具.exe")
INSTALLER_EXE = os.path.join("dist", "多线程下载工具安装包.exe")

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


def get_release(tag):
    try:
        return request(f"{API}/releases/tags/{tag}")
    except urllib.error.HTTPError:
        return None


def _ascii_asset_name(fname: str, tag: str) -> str:
    return (fname.replace("多线程下载工具安装包", f"DownloadsTool-Setup-{tag}")
                 .replace("多线程下载工具", f"DownloadsTool-{tag}"))


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blk)
    return h.hexdigest()


def _upsert_sha_line(body: str, label: str, digest: str) -> str:
    """把带标签的 SHA256 行写入正文：已有同类行原位替换，没有则追加。

    用户自定义正文里可能已带校验值（release.sh 支持传正文文件），
    无脑追加会产生重复行（P2-5）。
    """
    tag = f"SHA256（{label}）"
    for line in body.splitlines():
        if "sha256" in line.lower() and (
                label in line or ("免安装" in line and "安装包" not in line)
                or ("安装包" in line)):
            return body.replace(line, f"{tag}: {digest}")
    return body.rstrip() + f"\n{tag}: {digest}"


def create(tag, title_file, body_file):
    with open(title_file, encoding="utf-8") as f:
        title = f.read().strip()
    with open(body_file, encoding="utf-8") as f:
        body = f.read().strip()

    # 免安装版 exe 必须存在；安装包版可选（Inno Setup 未装/未构建时跳过）
    exes = [p for p in (PORTABLE_EXE, INSTALLER_EXE) if os.path.isfile(p)]
    if PORTABLE_EXE not in exes:
        sys.exit(f"portable exe not found: {PORTABLE_EXE}")

    # 正文：中文产物名替换为 ASCII 资产名 + 写入两份 SHA256（防重复 upsert）
    for p in exes:
        cn = os.path.basename(p)
        body = body.replace(cn, _ascii_asset_name(cn, tag))
    for p, label in ((PORTABLE_EXE, "免安装版"), (INSTALLER_EXE, "安装包版")):
        if p in exes:
            body = _upsert_sha_line(body, label, _file_sha256(p))
    body = body.rstrip() + (
        f"\n\n国内用户可从 Gitee 镜像下载同版本产物："
        f"https://gitee.com/{OWNER}/{REPO}/releases/tag/{tag}")

    rel = get_release(tag)
    if rel is None:
        rel = request(f"{API}/releases", data=json.dumps({
            "tag_name": tag, "target_commitish": "master",
            "name": title, "body": body, "draft": False, "prerelease": False,
        }).encode("utf-8"), content_type="application/json")
        print(f"created release id={rel['id']}")
    else:
        print(f"release exists id={rel['id']}, will reuse")

    rid = rel["id"]
    existing = {a["name"]: a["id"] for a in request(f"{API}/releases/{rid}/assets")}
    for junk in ("default.exe",):  # 中文名曾被 GitHub 改写成的错名资产
        if junk in existing:
            request(f"{API}/releases/assets/{existing[junk]}", method="DELETE")
            print(f"removed junk asset {junk}")
            del existing[junk]
    for path in exes:
        upload_name = _ascii_asset_name(os.path.basename(path), tag)
        if upload_name in existing:
            request(f"{API}/releases/assets/{existing[upload_name]}", method="DELETE")
            print(f"removed old asset {upload_name}")
        data = open(path, "rb").read()
        status, headers, _ = request(
            f"https://uploads.github.com/repos/{OWNER}/{REPO}/releases/{rid}/assets"
            f"?name={urllib.parse.quote(upload_name)}",
            data=data, raw=True,
            content_type="application/octet-stream",
            extra_headers={"Content-Type": "application/octet-stream"})
        print(f"uploaded {upload_name} ({len(data)} bytes, HTTP {status})")

    # 标题/正文（含最终资产名与校验值）同步到发行版
    request(f"{API}/releases/{rid}", data=json.dumps({
        "tag_name": tag, "name": title, "body": body}).encode("utf-8"),
        method="PATCH", content_type="application/json")
    print("body updated")
    print(f"page=https://github.com/{OWNER}/{REPO}/releases/tag/{tag}")


def check(tag=None):
    rel = get_release(tag) if tag else _latest()
    if rel is None:
        print("no release found")
        return
    print(f"release id={rel['id']} name={rel['name']} draft={rel.get('draft')} "
          f"tag={rel.get('tag_name')}")
    for a in request(f"{API}/releases/{rel['id']}/assets"):
        print(f"  asset: {a['name']} {a['size']} bytes -> {a['browser_download_url']}")


def _latest():
    rels = request(f"{API}/releases?per_page=1")
    return rels[0] if rels else None


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "create":
        create(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "check":
        check(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        sys.exit(f"unknown command: {cmd}")
