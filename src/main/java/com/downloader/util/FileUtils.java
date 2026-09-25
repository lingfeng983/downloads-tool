package com.downloader.util;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;

/**
 * 文件工具类
 */
public class FileUtils {

    /**
     * 删除目录及其内容
     */
    public static void deleteDirectory(File directory) throws IOException {
        if (directory == null || !directory.exists()) {
            return;
        }

        Files.walk(directory.toPath())
                .sorted(Comparator.reverseOrder())
                .map(Path::toFile)
                .forEach(File::delete);
    }

    /**
     * 确保目录存在
     */
    public static void ensureDirectory(File directory) {
        if (directory != null && !directory.exists()) {
            directory.mkdirs();
        }
    }

    /**
     * 获取临时目录路径
     */
    public static File getTempDirectory(File targetFile) {
        return new File(targetFile.getParent(), "." + targetFile.getName() + ".temp");
    }

    /**
     * 获取线程进度文件
     */
    public static File getThreadProgressFile(File tempDir, int threadId) {
        return new File(tempDir, "thread_" + threadId + ".dat");
    }

    /**
     * 检查磁盘空间是否足够
     */
    public static boolean hasEnoughSpace(File directory, long requiredSize) {
        if (directory == null) {
            return false;
        }
        File checkDir = directory.exists() ? directory : directory.getParentFile();
        if (checkDir == null) {
            return false;
        }
        return checkDir.getUsableSpace() > requiredSize;
    }
}
