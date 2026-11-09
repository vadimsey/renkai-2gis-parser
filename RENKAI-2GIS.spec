from PyInstaller.utils.hooks import collect_all, copy_metadata
datas = [('standalone_2gis/ui', 'standalone_2gis/ui'), ('parser_2gis/data', 'parser_2gis/data'), ('third_party', 'third_party')]
binaries = []
hiddenimports = []
for package in ('webview', 'pythonnet', 'clr_loader'):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
datas += copy_metadata('pywebview')
a = Analysis(['standalone_2gis/main.py'], pathex=['.'], binaries=binaries, datas=datas, hiddenimports=hiddenimports, excludes=['pytest', 'IPython', 'notebook', 'matplotlib', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'cefpython3', 'gi'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='RENKAI-2GIS', debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False, icon='web/renkai-icon.ico')
