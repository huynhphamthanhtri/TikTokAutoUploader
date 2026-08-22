__version__ = "1.2.1"
RESOURCE_RELEASE_VERSION = "1.1.0"
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
        "sha256": "1fad89e24cbe126b18c6bf941438af5a7d729d9e4e65e8a9e19b104f59014a96",
    },
}
