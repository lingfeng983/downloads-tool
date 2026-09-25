package com.downloader.controller;

import com.downloader.core.DownloadInfo;
import com.downloader.core.DownloadManager;
import com.downloader.core.TaskStatus;
import com.downloader.util.FormatUtils;
import javafx.animation.KeyFrame;
import javafx.animation.Timeline;
import javafx.application.Platform;
import javafx.beans.property.SimpleStringProperty;
import javafx.collections.FXCollections;
import javafx.fxml.FXML;
import javafx.geometry.Pos;
import javafx.scene.control.*;
import javafx.scene.layout.HBox;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.util.Duration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.io.IOException;

/**
 * 主界面控制器
 */
public class MainController {
    private static final Logger logger = LoggerFactory.getLogger(MainController.class);

    @FXML private TextField urlField;
    @FXML private TextField savePathField;
    @FXML private ComboBox<String> threadComboBox;
    @FXML private TableView<DownloadInfo> downloadTable;
    @FXML private TableColumn<DownloadInfo, String> nameColumn;
    @FXML private TableColumn<DownloadInfo, String> sizeColumn;
    @FXML private TableColumn<DownloadInfo, Void> progressColumn;
    @FXML private TableColumn<DownloadInfo, String> speedColumn;
    @FXML private TableColumn<DownloadInfo, String> remainingColumn;
    @FXML private TableColumn<DownloadInfo, String> statusColumn;
    @FXML private TableColumn<DownloadInfo, Void> actionColumn;
    @FXML private Label statusLabel;
    @FXML private Label totalSpeedLabel;

    private DownloadManager downloadManager;
    private Timeline refreshTimeline;

    @FXML
    public void initialize() {
        downloadManager = DownloadManager.getInstance();

        // 初始化默认值
        savePathField.setText(System.getProperty("user.home") + "/Downloads");
        
        // 初始化线程数选项
        threadComboBox.setItems(FXCollections.observableArrayList("1", "2", "4", "8", "16", "32"));
        threadComboBox.setValue("8");

        // 设置表格列
        setupTableColumns();

        // 绑定下载列表
        downloadTable.setItems(downloadManager.getDownloadList());

        // 启动定时刷新
        startRefreshTimer();

        logger.info("主界面初始化完成");
    }

    /**
     * 设置表格列
     */
    private void setupTableColumns() {
        // 文件名列
        nameColumn.setCellValueFactory(cellData ->
                cellData.getValue().fileNameProperty());

        // 大小列
        sizeColumn.setCellValueFactory(cellData ->
                new SimpleStringProperty(FormatUtils.formatFileSize(cellData.getValue().getTotalSize())));

        // 进度列 - 使用进度条
        progressColumn.setCellFactory(column -> new TableCell<>() {
            private final ProgressBar progressBar = new ProgressBar(0);
            private final Label percentLabel = new Label("0%");
            private final VBox vbox = new VBox(2, progressBar, percentLabel);

            {
                progressBar.setPrefWidth(150);
                progressBar.setPrefHeight(16);
                percentLabel.setStyle("-fx-font-size: 11px; -fx-text-fill: #666;");
                vbox.setAlignment(Pos.CENTER);
            }

            @Override
            protected void updateItem(Void item, boolean empty) {
                super.updateItem(item, empty);
                if (empty || getIndex() < 0 || getIndex() >= getTableView().getItems().size()) {
                    setGraphic(null);
                } else {
                    DownloadInfo info = getTableView().getItems().get(getIndex());
                    double progress = info.getProgress();
                    progressBar.setProgress(progress);
                    percentLabel.setText(FormatUtils.formatProgress(progress));
                    setGraphic(vbox);
                }
            }
        });

        // 速度列 - 显示总速度和各线程速度
        speedColumn.setCellFactory(column -> new TableCell<>() {
            private final VBox vbox = new VBox(2);
            private final Label totalSpeedLbl = new Label();
            private final VBox threadSpeedsBox = new VBox(1);
            
            {
                totalSpeedLbl.setStyle("-fx-font-weight: bold; -fx-font-size: 12px;");
                threadSpeedsBox.setStyle("-fx-font-size: 10px;");
                vbox.getChildren().addAll(totalSpeedLbl, threadSpeedsBox);
                vbox.setAlignment(Pos.CENTER_LEFT);
            }
            
            @Override
            protected void updateItem(String item, boolean empty) {
                super.updateItem(item, empty);
                if (empty || getIndex() < 0 || getIndex() >= getTableView().getItems().size()) {
                    setGraphic(null);
                } else {
                    DownloadInfo info = getTableView().getItems().get(getIndex());
                    totalSpeedLbl.setText(FormatUtils.formatSpeed(info.getSpeed()));
                    
                    threadSpeedsBox.getChildren().clear();
                    long[] threadSpeeds = info.getThreadSpeeds();
                    if (threadSpeeds != null && threadSpeeds.length > 0 && 
                        info.getStatus() == TaskStatus.DOWNLOADING) {
                        for (int i = 0; i < threadSpeeds.length; i++) {
                            Label threadLbl = new Label("T" + (i + 1) + ": " + 
                                FormatUtils.formatSpeed(threadSpeeds[i]));
                            threadLbl.setStyle("-fx-text-fill: #666; -fx-font-size: 9px;");
                            threadSpeedsBox.getChildren().add(threadLbl);
                        }
                    }
                    setGraphic(vbox);
                }
            }
        });

        // 剩余时间列
        remainingColumn.setCellValueFactory(cellData -> {
            DownloadInfo info = cellData.getValue();
            long remaining = info.getTotalSize() - info.getDownloadedSize();
            return new SimpleStringProperty(FormatUtils.formatRemainingTime(remaining, info.getSpeed()));
        });

        // 状态列
        statusColumn.setCellValueFactory(cellData ->
                new SimpleStringProperty(cellData.getValue().getStatus().getDescription()));

        // 操作列
        actionColumn.setCellFactory(column -> new TableCell<>() {
            private final Button actionBtn = new Button();
            private final Button cancelBtn = new Button("取消");
            private final HBox hbox = new HBox(5, actionBtn, cancelBtn);

            {
                hbox.setAlignment(Pos.CENTER);
                actionBtn.getStyleClass().add("action-button");
                cancelBtn.getStyleClass().addAll("action-button", "cancel-button");
                cancelBtn.setPrefWidth(50);

                actionBtn.setOnAction(e -> {
                    if (getIndex() >= 0 && getIndex() < getTableView().getItems().size()) {
                        DownloadInfo info = getTableView().getItems().get(getIndex());
                        handleActionButton(info);
                    }
                });

                cancelBtn.setOnAction(e -> {
                    if (getIndex() >= 0 && getIndex() < getTableView().getItems().size()) {
                        DownloadInfo info = getTableView().getItems().get(getIndex());
                        handleCancelButton(info);
                    }
                });
            }

            @Override
            protected void updateItem(Void item, boolean empty) {
                super.updateItem(item, empty);
                if (empty || getIndex() < 0 || getIndex() >= getTableView().getItems().size()) {
                    setGraphic(null);
                } else {
                    DownloadInfo info = getTableView().getItems().get(getIndex());
                    updateButtonState(info);
                    setGraphic(hbox);
                }
            }

            private void updateButtonState(DownloadInfo info) {
                TaskStatus status = info.getStatus();
                actionBtn.getStyleClass().removeAll("pause-button", "resume-button", "restart-button");

                switch (status) {
                    case DOWNLOADING -> {
                        actionBtn.setText("暂停");
                        actionBtn.getStyleClass().add("pause-button");
                        actionBtn.setDisable(false);
                        cancelBtn.setDisable(false);
                    }
                    case PAUSED -> {
                        actionBtn.setText("继续");
                        actionBtn.getStyleClass().add("resume-button");
                        actionBtn.setDisable(false);
                        cancelBtn.setDisable(false);
                    }
                    case COMPLETED -> {
                        actionBtn.setText("完成");
                        actionBtn.setDisable(true);
                        cancelBtn.setDisable(true);
                    }
                    case ERROR -> {
                        actionBtn.setText("重试");
                        actionBtn.getStyleClass().add("restart-button");
                        actionBtn.setDisable(false);
                        cancelBtn.setDisable(false);
                    }
                    case CANCELLED -> {
                        actionBtn.setText("重下");
                        actionBtn.getStyleClass().add("restart-button");
                        actionBtn.setDisable(false);
                        cancelBtn.setDisable(true);
                    }
                    default -> {
                        actionBtn.setText("等待");
                        actionBtn.setDisable(true);
                        cancelBtn.setDisable(false);
                    }
                }
            }
        });
    }

    /**
     * 处理开始下载按钮
     */
    @FXML
    private void handleStartDownload() {
        String url = urlField.getText().trim();
        if (url.isEmpty()) {
            showError("错误", "请输入下载链接");
            return;
        }

        // 验证URL格式
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            showError("错误", "请输入有效的HTTP/HTTPS链接");
            return;
        }

        String savePath = savePathField.getText().trim();
        if (savePath.isEmpty()) {
            savePath = System.getProperty("user.home") + "/Downloads";
        }

        int threadCount;
        try {
            threadCount = Integer.parseInt(threadComboBox.getValue());
        } catch (NumberFormatException e) {
            threadCount = 8;
        }

        // 禁用按钮防止重复点击
        Button startButton = (Button) urlField.getScene().lookup("#startButton");
        if (startButton != null) {
            startButton.setDisable(true);
        }

        final String finalSavePath = savePath;
        final int finalThreadCount = threadCount;

        // 在后台线程中执行
        new Thread(() -> {
            try {
                DownloadInfo info = downloadManager.createDownload(url, finalSavePath, finalThreadCount);
                downloadManager.startDownload(url);

                Platform.runLater(() -> {
                    urlField.clear();
                    statusLabel.setText("开始下载: " + info.getFileName());
                    if (startButton != null) {
                        startButton.setDisable(false);
                    }
                });
            } catch (IOException e) {
                logger.error("创建下载任务失败", e);
                Platform.runLater(() -> {
                    showError("下载失败", e.getMessage());
                    if (startButton != null) {
                        startButton.setDisable(false);
                    }
                });
            }
        }).start();
    }

    /**
     * 处理浏览按钮
     */
    @FXML
    private void handleBrowsePath() {
        DirectoryChooser chooser = new DirectoryChooser();
        chooser.setTitle("选择保存位置");

        String currentPath = savePathField.getText();
        if (!currentPath.isEmpty()) {
            File currentDir = new File(currentPath);
            if (currentDir.exists()) {
                chooser.setInitialDirectory(currentDir);
            }
        }

        File selectedDir = chooser.showDialog(savePathField.getScene().getWindow());
        if (selectedDir != null) {
            savePathField.setText(selectedDir.getAbsolutePath());
            downloadManager.setSavePath(selectedDir.getAbsolutePath());
        }
    }

    /**
     * 处理操作按钮点击
     */
    private void handleActionButton(DownloadInfo info) {
        try {
            switch (info.getStatus()) {
                case DOWNLOADING -> {
                    downloadManager.pauseDownload(info.getUrl());
                    statusLabel.setText("已暂停: " + info.getFileName());
                }
                case PAUSED -> {
                    downloadManager.resumeDownload(info.getUrl());
                    statusLabel.setText("继续下载: " + info.getFileName());
                }
                case ERROR, CANCELLED -> {
                    downloadManager.restartDownload(info.getUrl());
                    statusLabel.setText("重新下载: " + info.getFileName());
                }
                default -> throw new IllegalArgumentException("Unexpected value: " + info.getStatus());
            }
        } catch (IOException e) {
            logger.error("操作失败", e);
            showError("操作失败", e.getMessage());
        }
    }

    /**
     * 处理取消按钮点击
     */
    private void handleCancelButton(DownloadInfo info) {
        Alert alert = new Alert(Alert.AlertType.CONFIRMATION);
        alert.setTitle("确认");
        alert.setHeaderText(null);
        alert.setContentText("确定要取消下载 \"" + info.getFileName() + "\" 吗？");

        alert.showAndWait().ifPresent(response -> {
            if (response == ButtonType.OK) {
                downloadManager.removeDownload(info.getUrl());
                statusLabel.setText("已取消: " + info.getFileName());
            }
        });
    }

    /**
     * 启动定时刷新
     */
    private void startRefreshTimer() {
        refreshTimeline = new Timeline(new KeyFrame(Duration.millis(500), e -> {
            // 刷新表格
            downloadTable.refresh();

            // 更新总速度
            long totalSpeed = downloadManager.getDownloadList().stream()
                    .filter(info -> info.getStatus() == TaskStatus.DOWNLOADING)
                    .mapToLong(DownloadInfo::getSpeed)
                    .sum();
            totalSpeedLabel.setText("总速度: " + FormatUtils.formatSpeed(totalSpeed));
        }));
        refreshTimeline.setCycleCount(Timeline.INDEFINITE);
        refreshTimeline.play();
    }

    /**
     * 停止定时刷新
     */
    public void stopRefreshTimer() {
        if (refreshTimeline != null) {
            refreshTimeline.stop();
        }
    }

    /**
     * 显示错误对话框
     */
    private void showError(String title, String message) {
        Alert alert = new Alert(Alert.AlertType.ERROR);
        alert.setTitle(title);
        alert.setHeaderText(null);
        alert.setContentText(message);
        alert.showAndWait();
    }
}
