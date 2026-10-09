# 多线程下载工具（Python 重写版）

对原 Java/JavaFX 版的重写与优化，界面使用 PyQt5，下载引擎为纯 Python 多线程实现。

## 运行

```bash
pip install -r requirements.txt
python -m downloader
```

## 配置文件保存位置（可自定义）

任务列表（`tasks.json`）与用户设置（`settings.json`）的存放目录解析优先级：

1. 命令行参数：`python -m downloader --data-dir D:\MyToolData`
2. 环境变量：`DOWNLOADS_TOOL_DATA=D:\MyToolData`
3. UI 内设置：菜单「设置 → 配置文件保存位置…」，选一次即记住（写指针文件）
4. 默认：`~/.downloads_tool`

通过 1/2/3 换目录时会自动把旧目录的 `tasks.json`/`settings.json` 迁移过去，任务不丢。
下载保存目录、线程数、限速、完成后是否打开文件夹等设置也会持久化，重启自动恢复。

## 打包为单个 exe

```bash
pip install pyinstaller
python -m PyInstaller --noconfirm --clean DownloadsTool.spec
```

产物为 `dist/多线程下载工具.exe`（约 24MB，单文件、免控制台、内置图标、文件属性为中文）。
`DownloadsTool.spec` 会裁剪 QML/Quick/软件 OpenGL 渲染等 Qt Widgets 用不到的组件（42MB → 24MB）。
exe 同样支持自定义数据目录：`多线程下载工具.exe --data-dir D:\MyToolData`。

预编译的 exe 不在仓库源码中，发布在 Gitee「发行版（Releases）」页面，直接下载安装即可。

一键出包并发布到 Gitee 发行版（Git Bash 中运行）：

```bash
./release.sh            # 自动递增 tag（当前 v2.0 → v3）
./release.sh v3.1 标题   # 或显式指定 tag 与发行版标题
```

脚本依次完成：打包 exe → 推送 tag → 创建/复用发行版 → 上传 exe 附件。需先配置 Gitee 个人访问令牌（勾选 projects 权限）：环境变量 `GITEE_TOKEN` 或仓库根目录 `.gitee_token` 文件（已 gitignore）。因 Gitee 未官方开放附件上传接口，若脚本提示附件接口不可用，它会给出发行版编辑页链接，手动拖入 `dist/多线程下载工具.exe` 即可。

最终用户使用手册见 [使用说明.md](使用说明.md)。

## 与原 Java 版对应的功能

- 多线程分片下载（Range 请求），自动探测服务器是否支持断点续传，不支持则回退单线程（服务器忽略 Range 时运行中即时降级）
- 暂停 / 继续 / 重试 / 取消 / 移除（移除连带清理临时分片目录），速度统计（每秒刷新）
- 多行链接批量添加，多任务并发且可单独暂停/继续
- 从 `Content-Disposition` / URL 提取文件名，表格化任务列表，500ms 定时刷新

## 相对 Java 版的优化

| 优化点 | 说明 |
|---|---|
| 分片独立文件 + 合并 | 每个分片写入 `*.part` 文件，进度即文件大小：无需原版跨线程共享 `RandomAccessFile` 的锁竞争，也无需额外进度文件；完成后按序合并 |
| 断线自动重试 | 分片失败按指数退避自动重试（最多 5 次），服务器忽略 Range 时自动重写该分片，不再直接置错 |
| 任务持久化 | 任务列表保存在 `~/.downloads_tool/tasks.json`，重启应用后恢复列表与进度（继续即从磁盘断点续传） |
| 全局限速 | 滑动窗口限速器，所有任务/线程共享带宽上限 |
| 完成通知 | 系统托盘气泡通知；可选"完成后自动打开所在文件夹"（走 ShellExecute，比拉起 explorer.exe 快得多） |
| 快速失败 | 添加时探测 404/磁盘空间不足立即报错，不产生坏任务；文件名自动清洗 Windows 非法字符 |
| 重名保护 | 目标文件已存在时自动追加 `(1)(2)` 后缀，不覆盖 |
| 暂停不卡界面 | 暂停/取消时主动 `shutdown()` 在途 socket 打断阻塞读，实测从 29 秒降到毫秒级 |
| 健壮续传 | 停止下载时等待所有分片线程真正退出才允许继续，分片带写锁，杜绝两个线程同时写一个分片 |

## 目录结构

```
app.py            PyInstaller 打包入口
downloader/
  __main__.py     入口（python -m downloader）
  config.py       数据目录解析（--data-dir/环境变量/指针文件）与 settings.json
  manager.py      DownloadManager 单例：任务注册、速度监控、持久化
  task.py       DownloadTask：分片线程、重试、暂停/继续、合并
  probe.py      HTTP 探测：大小 / Range 支持 / 文件名
  info.py       TaskStatus 枚举 + DownloadInfo 实体
  util.py       格式化、磁盘检查、SpeedLimiter 限速器
  ui.py         PyQt5 主窗口
  worker.py     后台添加任务（探测不阻塞 UI）
tests/
  test_core.py  本地 Range 服务器端到端测试（多线程/续传/限速/单线程）
  test_regression.py  回归测试：小文件多线程、暂停及时性、线程泄漏、文件名清洗
```

## 测试

```bash
python tests/test_core.py
python tests/test_regression.py
cd tests && python test_thread_speed.py    # import test_core，需在 tests 目录下运行
cd tests && python test_ui.py             # 离屏 UI 冒烟
```
