package com.downloader.core;

import com.downloader.util.FileUtils;
import javafx.application.Platform;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 下载任务 - 管理多个下载线程，协调整个下载过程
 */
public class DownloadTask {
    private static final Logger logger = LoggerFactory.getLogger(DownloadTask.class);

    private final DownloadInfo downloadInfo;
    private ExecutorService threadPool;
    private ScheduledExecutorService speedCalculator;
    private final List<DownloadThread> downloadThreads;
    private RandomAccessFile outputFile;
    private File tempDir;

    private volatile boolean paused;
    private volatile boolean cancelled;
    private final AtomicInteger completedThreads;
    private final AtomicLong lastDownloadedSize;
    private Runnable onCompleteCallback;
    private Runnable onErrorCallback;

    public DownloadTask(DownloadInfo downloadInfo) {
        this.downloadInfo = downloadInfo;
        this.downloadThreads = new ArrayList<>();
        this.paused = false;
        this.cancelled = false;
        this.completedThreads = new AtomicInteger(0);
        this.lastDownloadedSize = new AtomicLong(0);
    }

    /**
     * 启动下载任务
     */
    public void start() throws IOException {
        if (downloadInfo.getTotalSize() <= 0) {
            throw new IOException("无法获取文件大小");
        }

        paused = false;
        cancelled = false;
        completedThreads.set(0);

        // 创建输出文件
        File file = new File(downloadInfo.getSavePath());
        FileUtils.ensureDirectory(file.getParentFile());
        outputFile = new RandomAccessFile(file, "rw");
        outputFile.setLength(downloadInfo.getTotalSize());

        // 创建临时目录存储断点信息
        tempDir = FileUtils.getTempDirectory(file);
        FileUtils.ensureDirectory(tempDir);

        // 计算每个线程的下载范围
        int threadCount = downloadInfo.getThreadCount();
        long fileSize = downloadInfo.getTotalSize();
        long blockSize = fileSize / threadCount;

        downloadThreads.clear();
        threadPool = Executors.newFixedThreadPool(threadCount);

        // 计算已下载的大小（从断点恢复）
        long totalDownloaded = 0;

        for (int i = 0; i < threadCount; i++) {
            long startPos = i * blockSize;
            long endPos = (i == threadCount - 1) ? fileSize - 1 : (i + 1) * blockSize - 1;

            File progressFile = FileUtils.getThreadProgressFile(tempDir, i);
            DownloadThread thread = new DownloadThread(
                    i, downloadInfo.getUrl(), startPos, endPos,
                    outputFile, progressFile, this
            );

            totalDownloaded += thread.getDownloadedBytes();
            downloadThreads.add(thread);
        }

        // 更新已下载大小
        downloadInfo.setDownloadedSize(totalDownloaded);
        lastDownloadedSize.set(totalDownloaded);

        // 提交所有下载线程
        for (DownloadThread thread : downloadThreads) {
            threadPool.execute(thread);
        }

        downloadInfo.setStatus(TaskStatus.DOWNLOADING);
        startSpeedCalculator();

        logger.info("下载任务启动: {} 线程: {}", downloadInfo.getFileName(), threadCount);
    }

    /**
     * 暂停下载
     */
    public void pause() {
        if (paused || cancelled) return;

        paused = true;
        downloadInfo.setStatus(TaskStatus.PAUSED);

        // 保存所有线程进度
        for (DownloadThread thread : downloadThreads) {
            thread.saveProgress();
        }

        shutdownThreadPool();
        stopSpeedCalculator();

        logger.info("下载任务暂停: {}", downloadInfo.getFileName());
    }

    /**
     * 继续下载
     */
    public void resume() throws IOException {
        if (!paused || cancelled) return;

        paused = false;
        completedThreads.set(0);

        // 重新创建线程池
        threadPool = Executors.newFixedThreadPool(downloadInfo.getThreadCount());

        // 重新加载进度并提交线程
        for (DownloadThread thread : downloadThreads) {
            if (!thread.isCompleted()) {
                thread.loadProgress();
                threadPool.execute(thread);
            } else {
                completedThreads.incrementAndGet();
            }
        }

        downloadInfo.setStatus(TaskStatus.DOWNLOADING);
        startSpeedCalculator();

        logger.info("下载任务继续: {}", downloadInfo.getFileName());
    }

    /**
     * 重新下载
     */
    public void restart() throws IOException {
        cancel();

        // 等待线程池关闭
        try {
            Thread.sleep(500);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }

        // 清理临时文件和目标文件
        if (tempDir != null && tempDir.exists()) {
            FileUtils.deleteDirectory(tempDir);
        }

        File targetFile = new File(downloadInfo.getSavePath());
        if (targetFile.exists()) {
            targetFile.delete();
        }

        // 重置状态
        downloadInfo.setDownloadedSize(0);
        downloadInfo.setSpeed(0);
        downloadInfo.setStatus(TaskStatus.WAITING);
        cancelled = false;
        paused = false;
        downloadThreads.clear();

        start();

        logger.info("下载任务重新开始: {}", downloadInfo.getFileName());
    }

    /**
     * 取消下载
     */
    public void cancel() {
        cancelled = true;
        downloadInfo.setStatus(TaskStatus.CANCELLED);

        shutdownThreadPool();
        stopSpeedCalculator();

        try {
            if (outputFile != null) {
                outputFile.close();
                outputFile = null;
            }
        } catch (IOException e) {
            logger.warn("关闭文件失败", e);
        }

        logger.info("下载任务取消: {}", downloadInfo.getFileName());
    }

    /**
     * 增加已下载大小
     */
    public void addDownloadedSize(long size) {
        downloadInfo.addDownloadedSize(size);
    }

    /**
     * 线程完成回调
     */
    public void onThreadCompleted(int threadId) {
        int completed = completedThreads.incrementAndGet();
        logger.debug("线程 {} 完成，已完成 {}/{}", threadId, completed, downloadInfo.getThreadCount());

        if (completed >= downloadInfo.getThreadCount()) {
            onAllThreadsCompleted();
        }
    }

    /**
     * 线程错误回调
     */
    public void onThreadError(int threadId, Exception e) {
        logger.error("线程 {} 出错: {}", threadId, e.getMessage());

        // 可以在这里实现重试逻辑
        Platform.runLater(() -> {
            if (!cancelled && !paused) {
                downloadInfo.setStatus(TaskStatus.ERROR);
                if (onErrorCallback != null) {
                    onErrorCallback.run();
                }
            }
        });
    }

    /**
     * 所有线程完成
     */
    private void onAllThreadsCompleted() {
        stopSpeedCalculator();

        try {
            if (outputFile != null) {
                outputFile.close();
                outputFile = null;
            }
        } catch (IOException e) {
            logger.warn("关闭文件失败", e);
        }

        // 删除临时目录
        if (tempDir != null && tempDir.exists()) {
            try {
                FileUtils.deleteDirectory(tempDir);
            } catch (IOException e) {
                logger.warn("删除临时目录失败", e);
            }
        }

        shutdownThreadPool();

        Platform.runLater(() -> {
            downloadInfo.setStatus(TaskStatus.COMPLETED);
            downloadInfo.setSpeed(0);
            if (onCompleteCallback != null) {
                onCompleteCallback.run();
            }
        });

        logger.info("下载完成: {}", downloadInfo.getFileName());
    }

    /**
     * 启动速度计算器
     */
    private void startSpeedCalculator() {
        stopSpeedCalculator();

        speedCalculator = Executors.newSingleThreadScheduledExecutor();
        speedCalculator.scheduleAtFixedRate(() -> {
            if (paused || cancelled) return;

            long currentSize = downloadInfo.getDownloadedSize();
            long speed = currentSize - lastDownloadedSize.get();
            lastDownloadedSize.set(currentSize);

            // 计算每个线程的速度
            long[] threadSpeeds = new long[downloadThreads.size()];
            for (int i = 0; i < downloadThreads.size(); i++) {
                DownloadThread thread = downloadThreads.get(i);
                thread.calculateSpeed();
                threadSpeeds[i] = thread.getSpeed();
            }

            Platform.runLater(() -> {
                downloadInfo.setSpeed(speed);
                downloadInfo.setThreadSpeeds(threadSpeeds);
            });
        }, 1, 1, TimeUnit.SECONDS);
    }

    /**
     * 停止速度计算器
     */
    private void stopSpeedCalculator() {
        if (speedCalculator != null && !speedCalculator.isShutdown()) {
            speedCalculator.shutdownNow();
            speedCalculator = null;
        }
    }

    /**
     * 关闭线程池
     */
    private void shutdownThreadPool() {
        if (threadPool != null && !threadPool.isShutdown()) {
            threadPool.shutdownNow();
            try {
                threadPool.awaitTermination(5, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
    }

    public boolean isPaused() {
        return paused;
    }

    public boolean isCancelled() {
        return cancelled;
    }

    public DownloadInfo getDownloadInfo() {
        return downloadInfo;
    }

    public void setOnCompleteCallback(Runnable callback) {
        this.onCompleteCallback = callback;
    }

    public void setOnErrorCallback(Runnable callback) {
        this.onErrorCallback = callback;
    }

    public List<DownloadThread> getDownloadThreads() {
        return downloadThreads;
    }
}
