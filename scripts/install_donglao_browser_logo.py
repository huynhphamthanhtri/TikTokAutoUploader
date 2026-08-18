"""
scripts/install_donglao_browser_logo.py - Process and deploy the new DONGLAO Browser Engine Logo.
"""

import sys
from pathlib import Path
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_IMAGE = Path(r"C:\Users\huynh\.gemini\antigravity-ide\brain\5e0d9fde-97a9-41e5-970d-549dcf2f66b8\donglao_browser_icon_1787043504738.jpg")

ASSETS_DIR = PROJECT_ROOT / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

PNG_DEST = ASSETS_DIR / "donglao_browser_logo.png"
ICO_DEST = ASSETS_DIR / "donglao_browser_icon.ico"
BROWSER_ICO = PROJECT_ROOT / "Browser" / "orbita-browser-144" / "app.ico"

def process_logo():
    if not SOURCE_IMAGE.exists():
        print(f"Source image not found: {SOURCE_IMAGE}")
        return False
    
    img = Image.open(SOURCE_IMAGE).convert("RGBA")
    
    # Save high-res PNG
    img.save(PNG_DEST, format="PNG")
    print(f"Saved PNG to: {PNG_DEST}")

    # Crop the central app icon (excluding outer padding if needed or saving square)
    # The generated image is 1024x1024 square with centered icon
    # Generate multi-resolution ICO
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ICO_DEST, format="ICO", sizes=sizes)
    print(f"Saved multi-res ICO to: {ICO_DEST}")

    if BROWSER_ICO.parent.exists():
        img.save(BROWSER_ICO, format="ICO", sizes=sizes)
        print(f"Saved browser app.ico to: {BROWSER_ICO}")
    
    return True

if __name__ == "__main__":
    process_logo()
