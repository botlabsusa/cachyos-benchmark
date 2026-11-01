#!/usr/bin/env python3
"""Trim unused Qt Quick/QML/Designer from AppDir (widgets-only GUI)."""
from __future__ import annotations
import shutil
import sys
from pathlib import Path

def main(appdir: Path) -> None:
    qt = appdir / "usr/lib/python3.13/site-packages/PySide6"
    for rel in ["Qt/qml", "Qt/translations", "Qt/metatypes", "Qt/libexec"]:
        p = qt / rel
        if p.exists():
            shutil.rmtree(p)
    plugins = qt / "Qt/plugins"
    keep = {
        "platforms", "platformthemes", "platforminputcontexts",
        "imageformats", "iconengines", "styles", "xcbglintegrations",
        "wayland-shell-integration", "wayland-decoration-client",
        "wayland-graphics-integration-client", "egldeviceintegrations",
    }
    if plugins.is_dir():
        for d in list(plugins.iterdir()):
            if d.is_dir() and d.name not in keep:
                shutil.rmtree(d)
    lib = qt / "Qt/lib"
    keep_keys = (
        "Qt6Core", "Qt6Gui", "Qt6Widgets", "Qt6DBus", "Qt6Network",
        "Qt6OpenGL", "Qt6Svg", "Qt6XcbQpa", "Qt6WaylandClient",
        "Qt6WaylandEglClientHwIntegration", "Qt6EglFSDeviceIntegration",
        "Qt6WlShellIntegration", "libicu", "Qt6OpenGLWidgets",
    )
    drop_keys = (
        "Qml", "Quick", "Multimedia", "WebEngine", "WebView", "Designer",
        "Charts", "3D", "Sensors", "Sql", "Test", "Pdf", "Location",
        "Positioning", "Bluetooth", "Nfc", "RemoteObjects", "TextToSpeech",
        "HttpServer", "Graphs", "DataVisualization", "ShaderTools",
        "JsonRpc", "Canvas3D", "Lottie", "PrintSupport", "Help", "Xml",
        "Concurrent", "Labs", "UiTools", "WaylandCompositor", "WaylandEglCompositor",
        "EglFsKms",
    )
    if lib.is_dir():
        for f in list(lib.iterdir()):
            name = f.name
            if any(k in name for k in drop_keys) or not any(k in name for k in keep_keys):
                try:
                    f.unlink()
                except IsADirectoryError:
                    shutil.rmtree(f)
    for pat in [
        "Qt3D*", "QtBluetooth*", "QtCharts*", "QtDataVisualization*",
        "QtMultimedia*", "QtPositioning*", "QtQuick*", "QtQml*",
        "QtRemoteObjects*", "QtSensors*", "QtSerial*", "QtSql*", "QtTest*",
        "QtTextToSpeech*", "QtWeb*", "QtPdf*", "QtNfc*", "QtHttpServer*",
        "QtGraphs*", "QtDesigner*", "QtHelp*", "QtUiTools*", "QtXml*",
        "QtConcurrent*", "QtStateMachine*",
    ]:
        for f in qt.glob(pat):
            if f.is_dir():
                shutil.rmtree(f)
            else:
                f.unlink()
    for d in appdir.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    main(Path(sys.argv[1]))
