# -*- mode: python ; coding: utf-8 -*-
# ManwaDownloader v4.2 — PyInstaller spec
# Build: pyinstaller ManwaDownloader.spec

a = Analysis(
    ['download.py'],
    pathex=['.'],
    binaries=[],
    datas=[('config.json', '.')],
    hiddenimports=[
        # Core package
        'manhua_download',
        'manhua_download.config',
        'manhua_download.utils',
        'manhua_download.api',
        'manhua_download.downloader',
        'manhua_download.cli',
        'manhua_download.crypto_utils',
        # aiohttp + async
        'aiohttp',
        'aiohttp.client',
        'aiohttp.connector',
        'aiohttp.client_reqrep',
        'aiohttp.client_ws',
        'aiohttp.cookiejar',
        'aiohttp.formdata',
        'aiohttp.hdrs',
        'aiohttp.helpers',
        'aiohttp.http',
        'aiohttp.http_parser',
        'aiohttp.http_websocket',
        'aiohttp.multipart',
        'aiohttp.payload',
        'aiohttp.resolver',
        'aiohttp.streams',
        'aiohttp.tcp',
        'aiohttp.web',
        'aiohttp.web_protocol',
        'aiohttp.web_runner',
        'aiohttp.web_server',
        'aiohttp.web_urldispatcher',
        'aiohttp.web_ws',
        'aiohttp.worker',
        'asyncio',
        # Crypto (pycryptodome — AES-256-CBC decryption)
        'Crypto',
        'Crypto.Cipher',
        'Crypto.Cipher.AES',
        'Crypto.Cipher._mode_cbc',
        'Crypto.Util',
        'Crypto.Util.Padding',
        'Crypto.Util._raw_api',
        # requests (sync API calls)
        'requests',
        'urllib3',
        # Rich (optional CLI美化)
        'rich',
        'rich.console',
        'rich.progress',
        'rich.progress_bar',
        'rich.text',
        'rich.style',
        'rich.theme',
        'rich.table',
        'rich.panel',
        'rich.layout',
        'rich.live',
        'rich.spinner',
        'rich.status',
        'rich.traceback',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # GUI — not needed for CLI tool
        'tkinter', '_tkinter', 'tcl', 'tk',
        'matplotlib', 'PIL', 'Pillow',
        # Data science
        'numpy', 'numpy._core', 'pandas', 'scipy',
        'cv2', 'opencv', 'pygame', 'curses',
        # Dev tools
        'unittest', 'pydoc', 'doctest',
        'pdb', 'profile', 'cProfile', 'trace',
        # Package managers & build
        'distutils', 'setuptools', 'pip',
        'lib2to3', 'xmlrpc', 'sqlite3', 'test',
        # Web servers
        'flask', 'django', 'bottle', 'cherrypy',
        'wsgiref', 'http.server', 'socketserver',
        # Unused
        'aiofiles',
        # Jupyter/IPython
        'IPython', 'jupyter', 'ipykernel', 'ipywidgets',
        'zmq', 'tornado',
    ],
    noarchive=False,
    optimize=2,  # Bytecode optimization level 2 (like python -OO)
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ManwaDownloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,       # Strip binary symbols
    upx=True,         # UPX compression (reduces size ~30%)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,     # Keep console window (CLI app)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # v4.2 additions
    icon=None,        # Add .ico path here for custom icon
)
