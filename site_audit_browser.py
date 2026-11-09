"""Locate a local Chrome installation for the 2GIS worker."""
import os
from pathlib import Path

def chrome_path():
    for base in (os.environ.get('PROGRAMFILES', 'C:/Program Files'), os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)'), os.environ.get('LOCALAPPDATA', '')):
        candidate = Path(base) / 'Google/Chrome/Application/chrome.exe'
        if candidate.is_file():
            return candidate
    return None
