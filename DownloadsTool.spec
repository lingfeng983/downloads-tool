# -*- mode: python ; coding: utf-8 -*-
# 多线程下载工具 单文件 exe 打包规格（裁剪无用 Qt 组件以减小体积）
import os

# Qt Widgets 应用用不到的二进制（按文件名前缀裁剪）
DROP_PREFIXES = (
    'qt5qml', 'qt5quick', 'qt5quick3d', 'qt5websockets', 'qt5dbus',
    'qt5network', 'qt5svg', 'qt5printsupport', 'qt5pdf', 'qt5opengl',
    'qt5test', 'qt5xml', 'qt5charts', 'qt5datavisualization', 'qt5remoteobjects',
    'opengl32sw', 'd3dcompiler_47', 'libglesv2', 'libegl', 'vlc',
)

# 插件目录只保留 Windows 平台插件与原生样式插件
KEEP_PLUGINS = (
    'platforms/qwindows',
    'styles/qwindowsvistastyle',
)


def keep_binary(item):
    dest = item[0].replace('\\', '/').lower()
    base = os.path.basename(dest)
    if '/plugins/' in dest:
        rel = dest.split('/plugins/', 1)[1]
        return any(rel.startswith(k) for k in KEEP_PLUGINS)
    if base.endswith(('.dll', '.pyd')):
        stem = base[:-4].lower()
        if stem.startswith(DROP_PREFIXES):
            return False
        if stem.startswith('pyqt5.') and stem.split('.', 1)[1] in (
                'qtqml', 'qtquick', 'qtwebsockets', 'qtnetwork', 'qtsvg',
                'qttest', 'qtxml', 'qtxmlpatterns', 'qtremoteobjects'):
            return False
    return True


a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=[('assets/icon.png', 'assets')],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'pydoc', 'xmlrpc',
        'PyQt5.QtNetwork', 'PyQt5.QtQml', 'PyQt5.QtQuick',
        'PyQt5.QtQuickWidgets', 'PyQt5.QtSvg', 'PyQt5.QtTest',
        'PyQt5.QtXml', 'PyQt5.QtXmlPatterns', 'PyQt5.QtRemoteObjects',
        'PyQt5.QtWebEngineWidgets', 'PyQt5.QtMultimedia',
    ],
    optimize=2,
)

a.binaries = [b for b in a.binaries if keep_binary(b)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='多线程下载工具',
    debug=False,
    strip=False,
    upx=False,
    icon='assets/icon.ico',
    version='version.txt',
    console=False,
)
