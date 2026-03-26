#!/usr/bin/env python3
"""
LexifyReview Launcher - Entry point for PyInstaller EXE.
Sets up Playwright browser path before launching main GUI.
"""
import os
import sys

def setup_environment():
    """Set Playwright browser path for bundled EXE."""
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller bundle
        app_dir = os.path.dirname(sys.executable)
        browsers_path = os.path.join(app_dir, '_playwright_browsers')
        if os.path.exists(browsers_path):
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = browsers_path

if __name__ == '__main__':
    setup_environment()
    # Import and run the main GUI
    from shopify_reviews_gui import main
    main()
