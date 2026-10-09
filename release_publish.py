# -*- coding: utf-8 -*-
"""Gitee 发行版发布助手（被 release.sh 调用；也可单独运行）。

python release_publish.py <tag> <title_file> <body_file>
title_file/body_file 为 UTF-8 编码的文本文件。
行为：tag 无对应发行版则创建，已存在则更新标题/正文；
随后把 dist 下最新的 exe 作为附件上传（同名旧附件先删除，保证可重复执行）。

注意：中文必须经 UTF-8 文件或原生 Unicode 路径传递，不能走 curl/控制台参数
（GBK 环境会把中文破坏成 U+FFFD）。
"""
import glob
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid

OWNER = "lingfeng983"
REPO = "downloads-tool"
API = "https://gitee.com/api/v5"

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


def main():
    tag, title_file, body_file = sys.argv[1:4]
    with open(title_file, encoding="utf-8") as f:
        name = f.read().strip()
    with open(body_file, encoding="utf-8") as f:
        body = f.read().strip()

    exes = glob.glob(os.path.join("dist", "*.exe"))
    if not exes:
        sys.exit("no exe found in dist/")
    exe = max(exes, key=os.path.getmtime)

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
    upload_attachment(rid, exe)
    print(f"page=https://gitee.com/{OWNER}/{REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
