# PyInstaller spec file for Pucon Kayak Retreat
# Build with:  python3.9 -m PyInstaller pucon_kayak.spec --noconfirm --clean
# (or just run:  bash build.sh)

import os

# Include client_secrets.json only if it exists (optional — OAuth won't work without it)
_secrets = [("client_secrets.json", ".")] if os.path.exists("client_secrets.json") else []

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("templates",   "templates"),
        ("static",      "static"),       # includes css/, js/, img/
    ] + _secrets,
    hiddenimports=[
        # Flask / Jinja / Werkzeug
        "flask",
        "jinja2",
        "jinja2.ext",
        "markupsafe",
        "werkzeug",
        "werkzeug.routing",
        "werkzeug.middleware",
        "werkzeug.middleware.proxy_fix",
        "click",
        # dotenv
        "dotenv",
        "python_dotenv",
        # Google Sheets
        "gspread",
        "gspread.utils",
        "gspread.exceptions",
        # Google auth — core
        "google.auth",
        "google.auth.transport",
        "google.auth.transport.requests",
        "google.oauth2",
        "google.oauth2.credentials",
        "google.oauth2.service_account",
        "google.auth.crypt",
        "google.auth.crypt._python_rsa",
        # Google auth — OAuth2 flow
        "google_auth_oauthlib",
        "google_auth_oauthlib.flow",
        # Supporting libs
        "cachetools",
        "pyasn1",
        "pyasn1_modules",
        "rsa",
        "requests",
        "requests.adapters",
        "requests.packages",
        "urllib3",
        "urllib3.util",
        "charset_normalizer",
        "certifi",
        "idna",
        "oauthlib",
        "oauthlib.oauth2",
        # PDF invoice export
        "reportlab",
        "reportlab.pdfgen",
        "reportlab.pdfgen.canvas",
        "reportlab.lib",
        "reportlab.lib.pagesizes",
        "reportlab.lib.units",
        "reportlab.lib.colors",
        # pywebview / macOS
        "webview",
        "webview.platforms.cocoa",
        "objc",
        "AppKit",
        "Foundation",
        "WebKit",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PuconKayakRetreat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PuconKayakRetreat",
)

app = BUNDLE(
    coll,
    name="PuconKayakRetreat.app",
    icon="icon.icns",
    bundle_identifier="com.puconkayakretreat.app",
    version="1.2.1",
    info_plist={
        "CFBundleName":              "Pucon Kayak Retreat",
        "CFBundleDisplayName":       "Pucon Kayak Retreat",
        "CFBundleVersion":           "1.2.1",
        "CFBundleShortVersionString":"1.2.1",
        "NSPrincipalClass":          "NSApplication",
        "NSHighResolutionCapable":   True,
        "NSAppleScriptEnabled":      False,
        "LSMinimumSystemVersion":    "10.15.0",
        "LSUIElement":               False,
        # Allow outbound connections (exchange rate, Google Sheets)
        "NSAppTransportSecurity": {
            "NSAllowsArbitraryLoads": True,
        },
    },
)
