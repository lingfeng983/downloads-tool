package com.downloader.core;

import javafx.beans.property.*;

import java.time.LocalDateTime;

/**
 * 下载信息实体类
 */
public class DownloadInfo {
    private final StringProperty url = new SimpleStringProperty();
    private final StringProperty fileName = new SimpleStringProperty();
    private final StringProperty savePath = new SimpleStringProperty();
    private final LongProperty totalSize = new SimpleLongProperty();
    private final LongProperty downloadedSize = new SimpleLongProperty();
    private final ObjectProperty<TaskStatus> status = new SimpleObjectProperty<>(TaskStatus.WAITING);
    private final IntegerProperty threadCount = new SimpleIntegerProperty();
    private final LongProperty speed = new SimpleLongProperty();
    private final ObjectProperty<LocalDateTime> startTime = new SimpleObjectProperty<>();
    private final BooleanProperty supportRange = new SimpleBooleanProperty();
    private volatile long[] threadSpeeds = new long[0];

    public DownloadInfo() {
    }

    public DownloadInfo(String url, String fileName, String savePath, long totalSize, int threadCount) {
        this.url.set(url);
        this.fileName.set(fileName);
        this.savePath.set(savePath);
        this.totalSize.set(totalSize);
        this.threadCount.set(threadCount);
        this.startTime.set(LocalDateTime.now());
    }

    // URL
    public String getUrl() {
        return url.get();
    }

    public void setUrl(String url) {
        this.url.set(url);
    }

    public StringProperty urlProperty() {
        return url;
    }

    // FileName
    public String getFileName() {
        return fileName.get();
    }

    public void setFileName(String fileName) {
        this.fileName.set(fileName);
    }

    public StringProperty fileNameProperty() {
        return fileName;
    }

    // SavePath
    public String getSavePath() {
        return savePath.get();
    }

    public void setSavePath(String savePath) {
        this.savePath.set(savePath);
    }

    public StringProperty savePathProperty() {
        return savePath;
    }

    // TotalSize
    public long getTotalSize() {
        return totalSize.get();
    }

    public void setTotalSize(long totalSize) {
        this.totalSize.set(totalSize);
    }

    public LongProperty totalSizeProperty() {
        return totalSize;
    }

    // DownloadedSize
    public long getDownloadedSize() {
        return downloadedSize.get();
    }

    public void setDownloadedSize(long downloadedSize) {
        this.downloadedSize.set(downloadedSize);
    }

    public LongProperty downloadedSizeProperty() {
        return downloadedSize;
    }

    // Status
    public TaskStatus getStatus() {
        return status.get();
    }

    public void setStatus(TaskStatus status) {
        this.status.set(status);
    }

    public ObjectProperty<TaskStatus> statusProperty() {
        return status;
    }

    // ThreadCount
    public int getThreadCount() {
        return threadCount.get();
    }

    public void setThreadCount(int threadCount) {
        this.threadCount.set(threadCount);
    }

    public IntegerProperty threadCountProperty() {
        return threadCount;
    }

    // Speed
    public long getSpeed() {
        return speed.get();
    }

    public void setSpeed(long speed) {
        this.speed.set(speed);
    }

    public LongProperty speedProperty() {
        return speed;
    }

    // StartTime
    public LocalDateTime getStartTime() {
        return startTime.get();
    }

    public void setStartTime(LocalDateTime startTime) {
        this.startTime.set(startTime);
    }

    public ObjectProperty<LocalDateTime> startTimeProperty() {
        return startTime;
    }

    // SupportRange
    public boolean isSupportRange() {
        return supportRange.get();
    }

    public void setSupportRange(boolean supportRange) {
        this.supportRange.set(supportRange);
    }

    public BooleanProperty supportRangeProperty() {
        return supportRange;
    }

    /**
     * 计算下载进度百分比
     */
    public double getProgress() {
        long total = getTotalSize();
        return total > 0 ? (double) getDownloadedSize() / total : 0;
    }

    /**
     * 增加已下载大小
     */
    public synchronized void addDownloadedSize(long size) {
        this.downloadedSize.set(this.downloadedSize.get() + size);
    }

    /**
     * 获取各线程速度
     */
    public long[] getThreadSpeeds() {
        return threadSpeeds;
    }

    /**
     * 设置各线程速度
     */
    public void setThreadSpeeds(long[] threadSpeeds) {
        this.threadSpeeds = threadSpeeds;
    }

    /**
     * 获取指定线程的速度
     */
    public long getThreadSpeed(int threadId) {
        if (threadSpeeds != null && threadId >= 0 && threadId < threadSpeeds.length) {
            return threadSpeeds[threadId];
        }
        return 0;
    }
}
