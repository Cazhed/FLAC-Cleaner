"""Launcher used by PyInstaller and for double-click start on Windows."""
import sys

from flac_cleaner.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
