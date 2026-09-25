# 多线程下载工具（Python 重写版）

对原 Java/JavaFX 版的重写与优化，界面使用 PyQt5，下载引擎为纯 Python 多线程实现。

## 运行

```bash
pip install -r requirements.txt
python -m downloader        # 或双击 run.bat
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

产物为 `dist/DownloadsTool.exe`（约 24MB，单文件、免控制台、内置图标）。
`DownloadsTool.spec` 会裁剪 QML/Quick/软件 OpenGL 渲染等 Qt Widgets 用不到的组件（42MB → 24MB）。
exe 同样支持自定义数据目录：`DownloadsTool.exe --data-dir D:\MyToolData`。

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
| 完成通知 | 系统托盘气泡通知；可选"完成后自动打开所在文件夹"（explorer 定位选中文件） |
| 快速失败 | 添加时探测 404/磁盘空间不足立即报错，不产生坏任务 |
| 重名保护 | 目标文件已存在时自动追加 `(1)(2)` 后缀，不覆盖 |

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
```

## 测试

```bash
python tests/test_core.py
```
