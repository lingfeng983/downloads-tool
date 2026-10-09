#!/usr/bin/env bash
# 一键出包 + 发布到 Gitee 发行版
# 用法:  ./release.sh [tag] [标题] [正文文件]
#        tag 省略时基于 origin 最大 vN 标签自动 +1（如 v2 -> v3）
# 凭证:  环境变量 GITEE_TOKEN，或仓库根目录 .gitee_token 文件
#        （在 https://gitee.com/profile/personal_access_tokens 创建，勾选 projects 权限）
set -euo pipefail
cd "$(dirname "$0")"

OWNER="lingfeng983"
REPO="downloads-tool"
API="https://gitee.com/api/v5"
SPEC="DownloadsTool.spec"
EXE="dist/多线程下载工具.exe"

die() { echo "!! $*" >&2; exit 1; }

# ---------- 0. 前置检查 ----------
TOKEN="${GITEE_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f .gitee_token ]; then
  TOKEN="$(tr -d '\r\n' < .gitee_token)"
fi
[ -n "$TOKEN" ] || die "缺少 Gitee 令牌：设置 GITEE_TOKEN 环境变量，或把个人访问令牌写入 .gitee_token"

[ -z "$(git status --porcelain)" ] || die "工作区有未提交改动，请先提交"
git fetch -q origin master --tags || die "无法访问 origin（网络/凭证问题？）"
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/master)" ] || die "本地 HEAD 与 origin/master 不一致，请先提交并 push"

TAG="${1:-}"
if [ -z "$TAG" ]; then
  max="$(git ls-remote --tags origin | sed -n 's|.*refs/tags/v\([0-9]\{1,\}\)[0-9.]*$|\1|p' | sort -n | tail -1)"
  [ -n "$max" ] || die "origin 上没有 vN 标签，请显式传入 tag，如 ./release.sh v3"
  TAG="v$((max + 1))"
fi
TITLE="${2:-多线程下载工具 ${TAG}}"

# ---------- 1. 打包 ----------
echo "==> [1/4] PyInstaller 打包（约 2 分钟）"
rm -f "$EXE"
python -m PyInstaller --noconfirm --clean "$SPEC" 2>&1 | tail -3 || true
[ -f "$EXE" ] || die "打包失败（输出见上）"
SIZE=$(stat -c %s "$EXE")
echo "    产物 $EXE（$SIZE 字节）"

# ---------- 2. 打 tag 并推送 ----------
echo "==> [2/4] 推送标签 $TAG"
if ! git ls-remote --tags origin "refs/tags/$TAG" | grep -q "refs/tags/$TAG"; then
  git tag -a "$TAG" -m "Release $TAG"
  git push origin "refs/tags/$TAG" || die "推送标签失败"
else
  echo "    标签 $TAG 已存在，复用"
fi

# ---------- 3. 创建/复用发行版 ----------
echo "==> [3/4] 确保发行版存在"
pyjq() { python -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') if isinstance(d,dict) else '')"; }

RID=$(curl -s "$API/repos/$OWNER/$REPO/releases/tags/$TAG" | pyjq id)
if [ -z "$RID" ]; then
  BODY_FILE="${3:-}"
  if [ -n "$BODY_FILE" ] && [ -f "$BODY_FILE" ]; then BODY=$(cat "$BODY_FILE"); else
    BODY="多线程下载工具 ${TAG} 单文件免安装版。

- 下载 \`${EXE##*/}\` 双击即可运行（约 24MB，无需 Python）
- 支持多线程分片、断点续传、限速、关闭最小化到托盘
- 源码即 master 分支，$TAG 标签对应当前提交"
  fi
  RID=$(curl -s -X POST "$API/repos/$OWNER/$REPO/releases" \
    --data-urlencode "access_token=$TOKEN" \
    --data-urlencode "tag_name=$TAG" \
    --data-urlencode "name=$TITLE" \
    --data-urlencode "body=$BODY" | pyjq id)
  [ -n "$RID" ] || die "创建发行版失败（检查令牌权限/tag 是否已推送）"
  echo "    已创建发行版 #$RID"
else
  echo "    发行版已存在（#$RID），追加附件"
fi

# ---------- 4. 上传 exe 附件 ----------
echo "==> [4/4] 上传附件 $(basename "$EXE")"
NAME_ENC=$(python -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$(basename "$EXE")")
RESP=$(curl -s -X POST \
  -H "Content-Type: application/octet-stream" \
  --data-binary "@$EXE" \
  "$API/repos/$OWNER/$REPO/releases/$RID/assets?access_token=$TOKEN&name=$NAME_ENC")
if echo "$RESP" | grep -q '"attach_url"'; then
  URL=$(echo "$RESP" | pyjq attach_url)
  echo "    上传成功：$URL"
else
  echo "    附件接口未成功（Gitee 未官方开放该接口），响应：$RESP"
  echo "    请手动上传：打开 https://gitee.com/$OWNER/$REPO/releases/$TAG/edit 拖入 $EXE"
fi

echo "==> 完成。发行页：https://gitee.com/$OWNER/$REPO/releases/tag/$TAG"
