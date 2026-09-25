package com.downloader;

/**
 * 启动器类 - 用于打包为可执行 JAR
 * 
 * JavaFX 在非模块化环境运行时，如果主类直接继承 Application 会出现错误。
 * 通过这个非 Application 子类的 Launcher 来启动应用可以绑过此限制。
 */
public class Launcher {
    public static void main(String[] args) {
        DownloaderApp.main(args);
    }
}
