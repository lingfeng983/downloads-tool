package com.downloader;

import com.downloader.controller.MainController;
import javafx.application.Application;
import javafx.fxml.FXMLLoader;
import javafx.scene.Parent;
import javafx.scene.Scene;
import javafx.stage.Stage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;

/**
 * 多线程下载工具主程序
 */
public class DownloaderApp extends Application {
    private static final Logger logger = LoggerFactory.getLogger(DownloaderApp.class);
    private MainController mainController;

    @Override
    public void start(Stage primaryStage) {
        try {
            FXMLLoader loader = new FXMLLoader(getClass().getResource("/fxml/main.fxml"));
            Parent root = loader.load();
            mainController = loader.getController();

            Scene scene = new Scene(root);
            primaryStage.setTitle("多线程下载工具");
            primaryStage.setScene(scene);
            primaryStage.setMinWidth(800);
            primaryStage.setMinHeight(500);

            // 窗口关闭事件
            primaryStage.setOnCloseRequest(event -> {
                logger.info("应用程序关闭");
                if (mainController != null) {
                    mainController.stopRefreshTimer();
                }
            });

            primaryStage.show();
            logger.info("应用程序启动成功");

        } catch (IOException e) {
            logger.error("启动应用程序失败", e);
            throw new RuntimeException("无法加载主界面", e);
        }
    }

    @Override
    public void stop() {
        logger.info("应用程序停止");
        if (mainController != null) {
            mainController.stopRefreshTimer();
        }
    }

    public static void main(String[] args) {
        launch(args);
    }
}
