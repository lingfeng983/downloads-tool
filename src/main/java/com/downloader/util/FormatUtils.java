package com.downloader.util;

/**
 * 格式化工具类
 */
public class FormatUtils {

    /**
     * 格式化文件大小
     */
    public static String formatFileSize(long size) {
        if (size < 0) {
            return "未知";
        }
        if (size < 1024) {
            return size + " B";
        } else if (size < 1024 * 1024) {
            return String.format("%.2f KB", size / 1024.0);
        } else if (size < 1024L * 1024 * 1024) {
            return String.format("%.2f MB", size / (1024.0 * 1024));
        } else {
            return String.format("%.2f GB", size / (1024.0 * 1024 * 1024));
        }
    }

    /**
     * 格式化下载速度
     */
    public static String formatSpeed(long bytesPerSecond) {
        if (bytesPerSecond <= 0) {
            return "0 B/s";
        }
        return formatFileSize(bytesPerSecond) + "/s";
    }

    /**
     * 格式化剩余时间
     */
    public static String formatRemainingTime(long remainingBytes, long speed) {
        if (speed <= 0) {
            return "计算中...";
        }

        long seconds = remainingBytes / speed;
        long hours = seconds / 3600;
        long minutes = (seconds % 3600) / 60;
        long secs = seconds % 60;

        if (hours > 0) {
            return String.format("%d小时%d分钟", hours, minutes);
        } else if (minutes > 0) {
            return String.format("%d分钟%d秒", minutes, secs);
        } else {
            return secs + "秒";
        }
    }

    /**
     * 格式化进度百分比
     */
    public static String formatProgress(double progress) {
        return String.format("%.1f%%", progress * 100);
    }
}
