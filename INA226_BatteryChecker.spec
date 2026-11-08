# PyInstaller 設定（フォルダ配布）。build.bat から使う。
# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["run_checker.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("assets/app_icon.ico", "assets"),
        ("assets/app_icon_256.png", "assets"),
        ("assets/app_icon_512.png", "assets"),
        ("battery_checker/style/app.qss", "battery_checker/style"),
        ("battery_checker/style/arrow_down.png", "battery_checker/style"),
        ("battery_checker/style/arrow_down_disabled.png", "battery_checker/style"),
        ("battery_checker/style/check.png", "battery_checker/style"),
    ],
    hiddenimports=["serial.tools.list_ports"],
    hookspath=[],
    runtime_hooks=[],
    # 使わない Qt バインディングや大きなモジュールは入れない
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter", "matplotlib", "scipy", "pytest",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf",
              "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="INA226_BatteryChecker",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    uac_admin=False,
    icon="assets/app_icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="INA226_BatteryChecker",
)
