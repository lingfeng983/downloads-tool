#!/usr/bin/env bash
# 一键出包 + 发布到 GitHub + Gitee 双发行版
# 用法:  ./release.sh [tag] [标题] [正文文件]
#        tag 省略时基于 downloader.__version__ 自动取 vX.Y.Z
# 凭证:  Gitee: 环境变量 GITEE_TOKEN 或 .gitee_token 文件
#        GitHub: Windows 凭证管理器 git:https://github.com 条目（或 GITHUB_TOKEN）
# 注意:  应用内在线更新只认 GitHub 发行版，GitHub 发布失败即整体失败
set -euo pipefail
cd "$(dirname "$0")"

OWNER="lingfeng983"
REPO="downloads-tool"
SPEC="DownloadsTool.spec"
EXE="dist/多线程下载工具.exe"
INSTALLER="dist/多线程下载工具安装包.exe"

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
git ls-remote -q github refs/heads/master > /dev/null || die "无法访问 github 远程（缺少 github remote？）"

TAG="${1:-}"
if [ -z "$TAG" ]; then
  # tag 与 downloader/__init__.py 的 __version__ 对齐（在线更新按版本号比较）
  ver="$(python -c "import downloader;print(downloader.__version__)")"
  [ -n "$ver" ] || die "无法读取 downloader.__version__"
  TAG="v$ver"
  if git ls-remote --tags origin "refs/tags/$TAG" | grep -q "refs/tags/$TAG"; then
    # 已存在的 tag 指向当前 HEAD 时复用（如发版中断后重跑）；否则才算冲突
    # （步骤 0 已 git fetch --tags，本地 tag 即远端状态）
    [ "$(git rev-parse "$TAG^{commit}")" = "$(git rev-parse HEAD)" ] \
      || die "标签 $TAG 已存在且不指向当前提交：发新版前请先更新 downloader/__init__.py 的 __version__（并同步 version.txt）"
    echo "==> 标签 $TAG 已存在且指向当前提交，复用"
  fi
fi
TITLE="${2:-多线程下载工具 ${TAG}}"

# ---------- 1. 打包 ----------
echo "==> [1/4] PyInstaller 打包（约 2 分钟）"
rm -f "$EXE" "$INSTALLER"
python -m PyInstaller --noconfirm --clean "$SPEC" 2>&1 | tail -3 || true
[ -f "$EXE" ] || die "打包失败（输出见上）"
SIZE=$(stat -c %s "$EXE")
echo "    产物 $EXE（$SIZE 字节）"

# ---------- 1b. Inno Setup 安装包（可选：本机没装 ISCC 就只发免安装版） ----------
ISCC=""
for c in "/c/Program Files/Inno Setup 7/ISCC.exe" \
         "/c/Program Files (x86)/Inno Setup 7/ISCC.exe" \
         "/c/Program Files/Inno Setup 6/ISCC.exe" \
         "/c/Program Files (x86)/Inno Setup 6/ISCC.exe"; do
  # set -e 下不能用 "[ -f ] && break" 形式：循环整体返回非零会误杀脚本
  if [ -f "$c" ]; then
    ISCC="$c"
    break
  fi
done
if [ -n "$ISCC" ]; then
  echo "==> [1b/4] Inno Setup 构建安装包"
  VER="$(python -c "import downloader;print(downloader.__version__)")"
  "$ISCC" //DAppVersion="$VER" installer/DownloadsTool.iss > build/iscc.log 2>&1 \
    || { tail -20 build/iscc.log; die "安装包构建失败（详见 build/iscc.log）"; }
  [ -f "$INSTALLER" ] || die "安装包构建失败（未见 $INSTALLER）"
  echo "    产物 $INSTALLER（$(stat -c %s "$INSTALLER") 字节）"
else
  echo "==> [1b/4] 未找到 Inno Setup（ISCC.exe），跳过安装包构建，仅发布免安装版"
fi

# ---------- 2. 打 tag 并推送（两个远程都要有） ----------
echo "==> [2/4] 推送标签 $TAG"
if ! git ls-remote --tags origin "refs/tags/$TAG" | grep -q "refs/tags/$TAG"; then
  git tag -a "$TAG" -m "Release $TAG"
fi
git push origin "refs/tags/$TAG" || die "推送标签到 Gitee 失败"
git push github "refs/tags/$TAG" || die "推送标签到 GitHub 失败"

# ---------- 3. 创建/更新发行版 ----------
echo "==> [3/4] 创建或更新发行版 $TAG（中文内容经 UTF-8 文件传递）"
mkdir -p build
printf '%s' "$TITLE" > build/rel_title.txt
if [ -n "${3:-}" ] && [ -f "$3" ]; then
  cp "$3" build/rel_body.txt
else
  cat > build/rel_body.txt <<EOF
多线程下载工具 ${TAG}。

- 免安装版：下载 \`多线程下载工具.exe\` 双击即可运行（约 24MB，无需 Python）
- 安装包版：下载 \`多线程下载工具安装包.exe\`，安装后带开始菜单/桌面快捷方式与卸载器
- 支持多线程分片、断点续传、限速、线程速度可视化、关闭最小化到托盘
- 源码即 master 分支，${TAG} 标签对应当前提交
- 在线更新源已切换为 GitHub Releases（Gitee 为镜像）
EOF
fi
python release_publish.py "$TAG" build/rel_title.txt build/rel_body.txt

# ---------- 3b. GitHub 发行版（在线更新源，必须成功） ----------
echo "==> [3b/4] 发布 GitHub 发行版 $TAG"
python github_release.py create "$TAG" build/rel_title.txt build/rel_body.txt \
  || die "GitHub 发行版发布失败（在线更新只认 GitHub，必须成功）"
rm -f build/rel_title.txt build/rel_body.txt
