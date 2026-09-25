package com.downloader.core;

import javafx.collections.FXCollections;
import javafx.collections.ObservableList;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 下载管理器 - 单例模式，管理所有下载任务
 */
public class DownloadManager {
    private static final Logger logger = LoggerFactory.getLogger(DownloadManager.class);
    private static final int DEFAULT_THREAD_COUNT = 8;
    private static final String DEFAULT_SAVE_PATH = System.getProperty("user.home") + "/Downloads";

    private static volatile DownloadManager instance;
    private final Map<String, DownloadTask> tasks;
    private final ObservableList<DownloadInfo> downloadList;
    private String savePath;
    private int defaultThreadCount;

    private DownloadManager() {
        tasks = new ConcurrentHashMap<>();
        downloadList = FXCollections.observableArrayList();
        savePath = DEFAULT_SAVE_PATH;
        defaultThreadCount = DEFAULT_THREAD_COUNT;
    }

    public static DownloadManager getInstance() {
        if (instance == null) {
            synchronized (DownloadManager.class) {
                if (instance == null) {
                    instance = new DownloadManager();
                }
            }
        }
        return instance;
    }

    /**
     * 创建并启动下载任务
     */
    public DownloadInfo createDownload(String url, String savePath, int threadCount) throws IOException {
        // 检查URL是否已在下载
        if (tasks.containsKey(url)) {
            throw new IOException("该URL已在下载列表中");
        }

        // 获取文件信息
        FileInfo fileInfo = getFileInfo(url);

        // 创建下载信息
        DownloadInfo info = new DownloadInfo();
        info.setUrl(url);
        info.setFileName(fileInfo.fileName);
        info.setSavePath(savePath + File.separator + fileInfo.fileName);
        info.setTotalSize(fileInfo.fileSize);
        info.setDownloadedSize(0);
        info.setStatus(TaskStatus.WAITING);
        info.setThreadCount(fileInfo.supportRange ? threadCount : 1);
        info.setSupportRange(fileInfo.supportRange);
        info.setStartTime(LocalDateTime.now());

        // 如果不支持 Range，设置线程数为1
        if (!fileInfo.supportRange) {
            logger.warn("服务器不支持断点续传，使用单线程下载");
        }

        // 创建下载任务
        DownloadTask task = new DownloadTask(info);
        tasks.put(url, task);
        downloadList.add(info);

        logger.info("创建下载任务: {} 大小: {} 线程数: {}",
                info.getFileName(), info.getTotalSize(), info.getThreadCount());

        return info;
    }

    /**
     * 开始下载
     */
    public void startDownload(String url) throws IOException {
        DownloadTask task = tasks.get(url);
        if (task != null) {
            task.start();
        }
    }

    /**
     * 暂停下载
     */
    public void pauseDownload(String url) {
        DownloadTask task = tasks.get(url);
        if (task != null) {
            task.pause();
        }
    }

    /**
     * 继续下载
     */
    public void resumeDownload(String url) throws IOException {
        DownloadTask task = tasks.get(url);
        if (task != null) {
            task.resume();
        }
    }

    /**
     * 重新下载
     */
    public void restartDownload(String url) throws IOException {
        DownloadTask task = tasks.get(url);
        if (task != null) {
            task.restart();
        }
    }

    /**
     * 取消下载
     */
    public void cancelDownload(String url) {
        DownloadTask task = tasks.get(url);
        if (task != null) {
            task.cancel();
        }
    }

    /**
     * 移除下载任务
     */
    public void removeDownload(String url) {
        DownloadTask task = tasks.remove(url);
        if (task != null) {
            task.cancel();
            downloadList.removeIf(info -> info.getUrl().equals(url));
        }
    }

    /**
     * 获取文件信息
     */
    private FileInfo getFileInfo(String urlString) throws IOException {
        URL url = new URL(urlString);
        HttpURLConnection conn = null;

        try {
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("HEAD");
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(10000);
            conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36");
            conn.connect();

            int responseCode = conn.getResponseCode();
            if (responseCode != HttpURLConnection.HTTP_OK) {
                throw new IOException("HTTP 错误: " + responseCode);
            }

            long fileSize = conn.getContentLengthLong();
            boolean supportRange = "bytes".equalsIgnoreCase(conn.getHeaderField("Accept-Ranges"));
            String fileName = extractFileName(urlString, conn);

            // 如果 HEAD 请求获取不到大小，尝试 GET 请求
            if (fileSize <= 0) {
                conn.disconnect();
                conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(10000);
                conn.setRequestProperty("Range", "bytes=0-0");
                conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36");
                conn.connect();

                String contentRange = conn.getHeaderField("Content-Range");
                if (contentRange != null && contentRange.contains("/")) {
                    String sizeStr = contentRange.substring(contentRange.lastIndexOf("/") + 1);
                    try {
                        fileSize = Long.parseLong(sizeStr);
                        supportRange = true;
                    } catch (NumberFormatException e) {
                        logger.warn("无法解析 Content-Range: {}", contentRange);
                    }
                }
            }

            return new FileInfo(fileName, fileSize, supportRange);
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    /**
     * 从URL或响应头提取文件名
     */
    private String extractFileName(String urlString, HttpURLConnection conn) {
        // 首先尝试从 Content-Disposition 获取
        String disposition = conn.getHeaderField("Content-Disposition");
        if (disposition != null && disposition.contains("filename")) {
            String[] parts = disposition.split(";");
            for (String part : parts) {
                part = part.trim();
                if (part.startsWith("filename=")) {
                    String fileName = part.substring(9).replace("\"", "").replace("'", "");
                    try {
                        return URLDecoder.decode(fileName, StandardCharsets.UTF_8);
                    } catch (Exception e) {
                        return fileName;
                    }
                } else if (part.startsWith("filename*=")) {
                    // RFC 5987 编码
                    String fileName = part.substring(10);
                    if (fileName.toLowerCase().startsWith("utf-8''")) {
                        fileName = fileName.substring(7);
                        try {
                            return URLDecoder.decode(fileName, StandardCharsets.UTF_8);
                        } catch (Exception e) {
                            return fileName;
                        }
                    }
                }
            }
        }

        // 从URL路径获取
        try {
            String path = new URL(urlString).getPath();
            String fileName = path.substring(path.lastIndexOf('/') + 1);
            if (!fileName.isEmpty()) {
                return URLDecoder.decode(fileName, StandardCharsets.UTF_8);
            }
        } catch (Exception e) {
            logger.warn("无法从URL解析文件名: {}", e.getMessage());
        }

        // 默认文件名
        return "download_" + System.currentTimeMillis();
    }

    public ObservableList<DownloadInfo> getDownloadList() {
        return downloadList;
    }

    public String getSavePath() {
        return savePath;
    }

    public void setSavePath(String savePath) {
        this.savePath = savePath;
    }

    public int getDefaultThreadCount() {
        return defaultThreadCount;
    }

    public void setDefaultThreadCount(int defaultThreadCount) {
        this.defaultThreadCount = defaultThreadCount;
    }

    public DownloadTask getTask(String url) {
        return tasks.get(url);
    }

    /**
     * 文件信息内部类
     */
    private static class FileInfo {
        final String fileName;
        final long fileSize;
        final boolean supportRange;

        FileInfo(String fileName, long fileSize, boolean supportRange) {
            this.fileName = fileName;
            this.fileSize = fileSize;
            this.supportRange = supportRange;
        }
    }
}
