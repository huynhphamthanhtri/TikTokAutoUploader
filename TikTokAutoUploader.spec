# -*- mode: python ; coding: utf-8 -*-

import os
import site
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

def _collect_watchdog_files():
    """Recursively collect all files from the watchdog package directory."""
    result = []
    for p in site.getsitepackages():
        src = Path(p) / "watchdog"
        if src.is_dir():
            for f in src.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(src.parent)
                    result.append((str(f), str(rel.parent)))
            break
    return result

project_datas = [
    ('icon.ico', '.'),
]

datas = project_datas
datas += collect_data_files('customtkinter')
datas += _collect_watchdog_files()
datas += collect_data_files('charset_normalizer')
datas += collect_data_files('requests')

hidden_imports = [
    'selenium.webdriver',
    'selenium.webdriver.chrome.webdriver',
    'selenium.webdriver.chrome.options',
    'selenium.webdriver.chrome.service',
    'selenium.webdriver.common.driver_finder',
    'selenium.webdriver.remote.webdriver',
    'selenium.webdriver.remote.webelement',
    'selenium.webdriver.remote.command',
    'selenium.webdriver.chromium.options',
    'selenium.webdriver.chromium.service',
    'selenium.webdriver.firefox.options',
    'selenium.webdriver.firefox.service',
    'selenium.webdriver.firefox.webdriver',
    'selenium.webdriver.ie.options',
    'selenium.webdriver.ie.service',
    'selenium.webdriver.ie.webdriver',
    'selenium.webdriver.edge.options',
    'selenium.webdriver.edge.service',
    'selenium.webdriver.edge.webdriver',
    'selenium.webdriver.safari.options',
    'selenium.webdriver.safari.service',
    'selenium.webdriver.safari.webdriver',
    'watchdog',
    'watchdog.observers.winapi',
    'watchdog.observers.read_directory_changes',
    'watchdog.observers.polling',
    'customtkinter',
    'gspread',
    'gspread.utils',
    'google.oauth2.service_account',
    'google.oauth2.credentials',
    'google.auth.transport.requests',
    'webdriver_manager.chrome',
    'psutil',
    'flask',
    'yt_dlp',
    'pyngrok',
    'googleapiclient',
    'googleapiclient.discovery',
    'googleapiclient.http',
    'google_auth_httplib2',
    'googleapiclient.model',
    'googleapiclient.errors',
    'gspread.utils',
    'packaging',
    'packaging.version',
]

hidden_imports += [
    'watchdog',
    'watchdog.events',
    'watchdog.observers',
    'watchdog.observers.api',
    'watchdog.observers.fsevents',
    'watchdog.observers.fsevents2',
    'watchdog.observers.inotify',
    'watchdog.observers.inotify_buffer',
    'watchdog.observers.inotify_c',
    'watchdog.observers.kqueue',
    'watchdog.observers.polling',
    'watchdog.observers.read_directory_changes',
    'watchdog.observers.winapi',
    'watchdog.tricks',
    'watchdog.utils',
    'watchdog.utils.bricks',
    'watchdog.utils.delayed_queue',
    'watchdog.utils.dirsnapshot',
    'watchdog.utils.echo',
    'watchdog.utils.event_debouncer',
    'watchdog.utils.patterns',
    'watchdog.utils.platform',
    'watchdog.utils.process_watcher',
    'watchdog.version',
    'watchdog.watchmedo',
]
hidden_imports += collect_submodules('customtkinter')
hidden_imports += collect_submodules('yt_dlp')
hidden_imports += collect_submodules('pyngrok')
hidden_imports += collect_submodules('googleapiclient')
hidden_imports += collect_submodules('charset_normalizer')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['seleniumwire', 'pydivert', 'undetected_chromedriver'],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TikTokAutoUploader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='TikTokAutoUploader',
)
