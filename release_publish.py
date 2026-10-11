# -*- coding: utf-8 -*-
"""Gitee 发行版发布助手（被 release.sh 调用；也可单独运行）。

python release_publish.py <tag> <title_file> <body_file>
title_file/body_file 为 UTF-8 编码的文本文件。
行为：tag 无对应发行版则创建，已存在则更新标题/正文；
随后把 dist 下最新的 exe 作为附件上传（同名旧附件先删除，保证可重复执行）。

注意：中文必须经 UTF-8 文件或原生 Unicode 路径传递，不能走 curl/控制台参数
（GBK 环境会把中文破坏成 U+FFFD）。
"""
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid

OWNER = "lingfeng983"
REPO = "downloads-tool"
API = "https://gitee.com/api/v5"

PORTABLE_EXE = os.path.join("dist", "多线程下载工具.exe")
INSTALLER_EXE = os.path.join("dist", "多线程下载工具安装包.exe")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def token():
    env = os.environ.get("GITEE_TOKEN", "").strip()
    if env:
        return env
    with open(".gitee_token", encoding="utf-8") as f:
        return f.read().strip()


def request(url, params=None, data=None, content_type=None, method=None):
    if params is not None:
        params = dict(params, access_token=token())
        data = urllib.parse.urlencode(params).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def get_release(tag):
    url = f"{API}/repos/{OWNER}/{REPO}/releases/tags/{urllib.parse.quote(tag)}"
    try:
        d = request(url)
        return d if "id" in d else None
    except Exception:
        return None


def upload_attachment(rid, exe):
    fname = os.path.basename(exe)
    # 去重：先删同名旧附件，重复执行安全
    for att in request(f"{API}/repos/{OWNER}/{REPO}/releases/{rid}/attach_files"):
        if att.get("name") == fname:
            request(
                f"{API}/repos/{OWNER}/{REPO}/releases/{rid}/attach_files/{att['id']}"
                f"?access_token={urllib.parse.quote(token())}",
                method="DELETE")
            print(f"removed old attachment {fname} (id={att['id']})")
    data = open(exe, "rb").read()
    bid = uuid.uuid4().hex

    def part(name, value, filename=None):
        line = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            line += f'; filename="{filename}"'
        body = value if isinstance(value, bytes) else value.encode("utf-8")
        return (f"--{bid}\r\n{line}\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8") + body + b"\r\n"

    payload = (part("access_token", token()) + part("release_id", str(rid))
               + part("file", data, fname) + f"--{bid}--\r\n".encode())
    resp = request(
        f"{API}/repos/{OWNER}/{REPO}/releases/{rid}/attach_files",
        data=payload, content_type=f"multipart/form-data; boundary={bid}")
    print(f"uploaded {resp['name']} ({resp['size']} bytes)")
    print("download=" + resp["browser_download_url"])


def _body_has_sha(body: str, digest: str) -> bool:
    return f"SHA256:{digest}" in body.replace(" ", "").replace("：", ":").lower()


def _upsert_sha_line(body: str, digest: str) -> str:
    """把 SHA256 行插入正文：已有一行就原位替换，否则追加到末尾"""
    marker = "sha256:"
    for line in body.splitlines():
        if marker in line.replace(" ", "").replace("：", ":").lower():
            return body.replace(line, f"SHA256: {digest}")
    return (body.rstrip() + f"\n\nSHA256: {digest}").strip()


def main():
    tag, title_file, body_file = sys.argv[1:4]
    with open(title_file, encoding="utf-8") as f:
        name = f.read().strip()
    with open(body_file, encoding="utf-8") as f:
        body = f.read().strip()

    # 免安装版 exe 必须存在；安装包版可选（Inno Setup 未装/未构建时跳过）
    exes = [p for p in (PORTABLE_EXE, INSTALLER_EXE) if os.path.isfile(p)]
    if PORTABLE_EXE not in exes:
        sys.exit(f"portable exe not found: {PORTABLE_EXE}")
    exe = PORTABLE_EXE

    # 把便携版 exe 的 SHA256 写进发行版说明，更新侧下载后校验完整性。
    # 自动更新只下载便携版，所以校验值只对应它（安装包由用户手动下载）。
    sha = hashlib.sha256()
    with open(exe, "rb") as f:
        for blk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(blk)
    digest = sha.hexdigest()
    if not _body_has_sha(body, digest):
        body = _upsert_sha_line(body, digest)

    rel = get_release(tag)
    path = f"/repos/{OWNER}/{REPO}/releases"
    if rel is None:
        rel = request(API + path, {
            "tag_name": tag, "target_commitish": "master",
            "name": name, "body": body,
        })
        action = "created"
    else:
        # Gitee 的 PATCH 更新要求 tag_name 与 body 必传
        rel = request(API + path + f"/{rel['id']}", {
            "tag_name": tag, "target_commitish": "master",
            "name": name, "body": body,
        }, method="PATCH")
        action = "updated"
    rid = rel.get("id")
    print(f"{action} release id={rid} tag={tag}")
    for e in exes:
        upload_attachment(rid, e)
    print(f"page=https://gitee.com/{OWNER}/{REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
