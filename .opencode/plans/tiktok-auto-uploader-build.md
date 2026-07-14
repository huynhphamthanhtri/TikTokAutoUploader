# TikTokAutoUploader — Build Reference

## Prerequisites
- Python 3.13+
- Virtual environment with all dependencies installed

## Step 1: Prepare directories
```powershell
cd E:\BK_TOOL_VIBE_AUTO_UPLOAD\VIBE_AUTO_UPLOAD1
New-Item -ItemType Directory -Path "Auto_Data" -Force
```

## Step 2: Build spec (`TikTokAutoUploader.spec`)
```python
# TikTokAutoUploader.spec
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

project_datas = [
    ('icon.ico', '.'),
]

datas = project_datas
datas += collect_data_files('customtkinter')
datas += collect_data_files('seleniumwire', include_py_files=True)

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
    'seleniumwire',
    'seleniumwire.webdriver',
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
    'seleniumwire.thirdparty',
    'seleniumwire.thirdparty.mitmproxy',
]
hidden_imports += collect_submodules('seleniumwire')
hidden_imports += collect_submodules('watchdog')
hidden_imports += collect_submodules('customtkinter')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='TikTokAutoUploader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False, upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name='TikTokAutoUploader',
)
```

## Step 3: Build
```powershell
python -m PyInstaller TikTokAutoUploader.spec --noconfirm --clean
```

## Step 4: Deploy
```powershell
Copy-Item -Path "service_account.json" -Destination "dist/TikTokAutoUploader/service_account.json" -Force
```

## Step 5: Verify
- Double-click `dist/TikTokAutoUploader/TikTokAutoUploader.exe`
- Open terminal in a **different folder**, run full path to exe → files must be created next to exe, not in cwd

## Key path design (`main.py`)
```python
def app_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

CONFIGS_FILE = app_base_dir() / "configs.json"
REQUEST_TRACE_DIR = app_base_dir() / "request_traces"
FAILED_UPLOADS_LOG = app_base_dir() / "failed_uploads.log"
SERVICE_ACCOUNT_FILE = app_base_dir() / "service_account.json"
OFFLINE_CACHE_FILE = app_base_dir() / "license_cache.json"
LOCAL_DRIVER_CACHE_DIR = app_base_dir() / "temp_dl" / "driver_cache"
BASE_DATA_DIR = app_base_dir() / "Auto_Data"
```

## Troubleshooting
- Missing module at runtime → add to `hidden_imports` list, rebuild with `--clean`
- `service_account.json` missing → copy file next to exe
- `undetected_chromedriver` warning → **safe to ignore** (optional dep)
- `pydivert` .sys warnings → **safe to ignore** (Windows kernel drivers)
