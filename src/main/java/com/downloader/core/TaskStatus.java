package com.downloader.core;

/**
 * 下载任务状态枚举
 */
public enum TaskStatus {
    WAITING("等待中"),
    DOWNLOADING("下载中"),
    PAUSED("已暂停"),
    COMPLETED("已完成"),
    ERROR("错误"),
    CANCELLED("已取消");

    private final String description;

    TaskStatus(String description) {
        this.description = description;
    }

    public String getDescription() {
        return description;
    }
}
