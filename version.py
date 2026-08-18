__version__ = "1.1.0"
RESOURCE_RELEASE_VERSION = "1.0.9"
APP_NAME = "TikTokAutoUploader"
RELEASE_ASSET_PREFIX = "DONGLAO-TIKTOK-v"

GITHUB_REPO_OWNER = "huynhphamthanhtri"
GITHUB_REPO_NAME = "TikTokAutoUploader"

RESOURCE_BROWSER_ENGINE_DIR = "donglao-browser-144"

RESOURCE_ASSETS = {
    "Browser": {
        "asset": "Browser-v{version}.zip",
        "type": "zip_dir",
        "validate": [
            "Browser/donglao-browser-144/chrome.exe",
        ],
        "sha256": "ce6df90fad6ea4a8fc6de2502737194cdeea026a653d1467ed2591d783805e3a",
    },
}
