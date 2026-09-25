@echo off

:: 设置控制台编码为UTF-8
chcp 65001 >nul

:: 设置Java运行参数，确保使用UTF-8编码
set JAVA_OPTS=-Dfile.encoding=UTF-8 -Dsun.jnu.encoding=UTF-8

echo 启动下载工具...
echo 编码设置：UTF-8
echo.

:: 检查是否存在编译好的JAR文件
if exist target\downloads-tool-1.0.0-all.jar (
    java %JAVA_OPTS% -jar target\downloads-tool-1.0.0-all.jar
) else (
    echo 错误：未找到编译好的JAR文件
    echo 请先运行 mvn clean package 编译项目
    pause
    exit /b 1
)

pause