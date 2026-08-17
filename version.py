__version__ = "1.0.18"
RESOURCE_RELEASE_VERSION = "1.0.8"
APP_NAME = "TikTokAutoUploader"
RELEASE_ASSET_PREFIX = "DONGLAO-TIKTOK-v"

GITHUB_REPO_OWNER = "huynhphamthanhtri"
GITHUB_REPO_NAME = "TikTokAutoUploader"

RESOURCE_ASSETS = {
    "Browser": {
        "asset": "Browser-v{version}.zip",
        "type": "zip_dir",
        "validate": [
            "Browser/chrome-win64/chrome.exe",
        ],
    },
    "ngrok.exe": {
        "asset": "ngrok.exe",
        "type": "file",
    },
    "service_account.json": {
        "asset": "service_account.json",
        "type": "file",
    },
}
