; Inno Setup 脚本：多线程下载工具 安装包版
;
; 用法（先 PyInstaller 打出 dist/多线程下载工具.exe）:
;   iscc /DAppVersion=3.2.0 installer\DownloadsTool.iss
; 产物: dist/多线程下载工具安装包.exe
;
; AppId 固定不变：同一 AppId 的安装包覆盖安装/升级时自动接替旧版本，
; 卸载信息也能正确关联。改动 AppId 会导致系统里出现两个独立条目。

#define AppName "多线程下载工具"
#define AppExe "多线程下载工具.exe"

#ifndef AppVersion
#define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B7E4A2C1-9F3D-4E58-8C26-D1A0F5B97E43}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher=lingfeng983
; 安装包自身的版本资源（右键属性可见）
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} 安装包
VersionInfoCompany=lingfeng983
VersionInfoCopyright=Copyright © 2026 lingfeng983
DefaultDirName={autopf}\DownloadsTool
DefaultGroupName={#AppName}
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
OutputBaseFilename=多线程下载工具安装包
SetupIconFile=..\assets\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
; 覆盖安装时旧版本可能正在运行：先提示关闭，避免安装后文件被占用
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; \
    GroupDescription: "附加任务:"

[Files]
Source: "..\dist\多线程下载工具.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "运行 {#AppName}"; \
    Flags: nowait postinstall skipifsilent
