import os
import sys
from pathlib import Path

def validate():
    print("--- Validating Project Assets ---")
    base_dir = Path(__file__).resolve().parent.parent
    fonts_dir = base_dir / "assets" / "fonts"
    
    required_fonts = ["Montserrat-Bold.ttf", "Roboto-Black.ttf"]
    missing = []

    if not fonts_dir.exists():
        print(f"ERROR: Fonts directory not found at {fonts_dir}")
        sys.exit(1)

    for font in required_fonts:
        font_path = fonts_dir / font
        if not font_path.exists():
            missing.append(font)
            print(f"MISSING: {font}")
        else:
            print(f"OK: {font}")

    if missing:
        print(f"Validation failed. {len(missing)} assets missing.")
        sys.exit(1)
    
    print("All assets validated successfully.")

if __name__ == "__main__":
    validate()
