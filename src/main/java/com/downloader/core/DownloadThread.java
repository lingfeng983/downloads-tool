package com.downloader.core;

import com.downloader.util.FileUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.*;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * 下载线程 - 负责下载文件的一个分片
 */
public class DownloadThread implements Runnable {
    private static final Logger logger = LoggerFactory.getLogger(DownloadThread.class);
    private static final int BUFFER_SIZE = 8192;
    private static final int CONNECT_TIMEOUT = 10000;
    private static final int READ_TIMEOUT = 30000;

    private final int threadId;
    private final String url;
    private final long startPos;
    private final long endPos;
    private volatile long currentPos;
    private final RandomAccessFile outputFile;
    private final File progressFile;
    private final DownloadTask task;
    private volatile boolean completed;
    private volatile long speed;
    private volatile long lastDownloadedBytes;
    private volatile long downloadedInLastSecond;

    public DownloadThread(int threadId, String url, long startPos, long endPos,
                          RandomAccessFile outputFile, File progressFile, DownloadTask task) {
        this.threadId = threadId;
        this.url = url;
        this.startPos = startPos;
        this.endPos = endPos;
        this.currentPos = startPos;
        this.outputFile = outputFile;
        this.progressFile = progressFile;
        this.task = task;
        this.completed = false;
        this.speed = 0;
        this.lastDownloadedBytes = 0;
        this.downloadedInLastSecond = 0;

        // 尝试加载之前的进度
        loadProgress();
    }

    @Override
    public void run() {
        if (currentPos > endPos) {
            completed = true;
            task.onThreadCompleted(threadId);
            return;
        }

        HttpURLConnection conn = null;
        InputStream is = null;

        try {
            URL downloadUrl = new URL(url);
            conn = (HttpURLConnection) downloadUrl.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(CONNECT_TIMEOUT);
            conn.setReadTimeout(READ_TIMEOUT);
            conn.setRequestProperty("Range", "bytes=" + currentPos + "-" + endPos);
            conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36");

            int responseCode = conn.getResponseCode();
            if (responseCode == HttpURLConnection.HTTP_PARTIAL || responseCode == HttpURLConnection.HTTP_OK) {
                is = conn.getInputStream();
                byte[] buffer = new byte[BUFFER_SIZE];
                int len;

                while ((len = is.read(buffer)) != -1) {
                    if (Thread.currentThread().isInterrupted() || task.isPaused() || task.isCancelled()) {
                        saveProgress();
                        logger.info("线程 {} 被中断，当前位置: {}", threadId, currentPos);
                        return;
                    }

                    synchronized (outputFile) {
                        outputFile.seek(currentPos);
                        outputFile.write(buffer, 0, len);
                    }

                    currentPos += len;
                    downloadedInLastSecond += len;
                    task.addDownloadedSize(len);

                    // 定期保存进度
                    if (currentPos % (BUFFER_SIZE * 100) == 0) {
                        saveProgress();
                    }
                }

                completed = true;
                saveProgress();
                logger.info("线程 {} 下载完成，范围: {} - {}", threadId, startPos, endPos);
                task.onThreadCompleted(threadId);
            } else {
                logger.error("线程 {} 服务器返回错误: {}", threadId, responseCode);
                task.onThreadError(threadId, new IOException("HTTP error: " + responseCode));
            }
        } catch (IOException e) {
            saveProgress();
            logger.error("线程 {} 下载出错: {}", threadId, e.getMessage());
            task.onThreadError(threadId, e);
        } finally {
            try {
                if (is != null) {
                    is.close();
                }
            } catch (IOException e) {
                logger.warn("关闭输入流失败", e);
            }
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    /**
     * 保存下载进度到文件
     */
    public void saveProgress() {
        try {
            FileUtils.ensureDirectory(progressFile.getParentFile());
            try (DataOutputStream dos = new DataOutputStream(new FileOutputStream(progressFile))) {
                dos.writeLong(currentPos);
            }
        } catch (IOException e) {
            logger.warn("保存进度失败: {}", e.getMessage());
        }
    }

    /**
     * 从文件加载下载进度
     */
    public void loadProgress() {
        if (progressFile.exists()) {
            try (DataInputStream dis = new DataInputStream(new FileInputStream(progressFile))) {
                long savedPos = dis.readLong();
                if (savedPos >= startPos && savedPos <= endPos) {
                    currentPos = savedPos;
                    logger.info("线程 {} 恢复进度: {}", threadId, currentPos);
                }
            } catch (IOException e) {
                logger.warn("加载进度失败，从头开始: {}", e.getMessage());
                currentPos = startPos;
            }
        }
    }

    public boolean isCompleted() {
        return completed;
    }

    public int getThreadId() {
        return threadId;
    }

    public long getCurrentPos() {
        return currentPos;
    }

    public long getDownloadedBytes() {
        return currentPos - startPos;
    }

    /**
     * 获取当前速度
     */
    public long getSpeed() {
        return speed;
    }

    /**
     * 计算并更新速度（每秒调用一次）
     */
    public void calculateSpeed() {
        speed = downloadedInLastSecond;
        downloadedInLastSecond = 0;
    }

    /**
     * 获取线程总大小
     */
    public long getTotalBytes() {
        return endPos - startPos + 1;
    }

    /**
     * 获取线程进度
     */
    public double getProgress() {
        long total = getTotalBytes();
        return total > 0 ? (double) getDownloadedBytes() / total : 0;
    }
}
