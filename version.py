__version__ = "1.0.7"
APP_NAME = "TikTokAutoUploader"
RELEASE_ASSET_PREFIX = "TikTokAutoUploader-v"

GITHUB_REPO_OWNER = "huynhphamthanhtri"
GITHUB_REPO_NAME = "TikTokAutoUploader"

RESOURCE_ASSETS = {
    "Browser": {
        "asset": "Browser-v{version}.zip",
        "type": "zip_dir",
        "validate": [
            "Browser/chromedriver.exe",
            "Browser/orbita-browser-123/chrome.exe",
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
