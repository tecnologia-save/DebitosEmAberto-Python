# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from pathlib import Path
import patchright as _pw

DEBITOS_DIR      = Path(__file__).parent
PATCHRIGHT_HOOKS = str(Path(_pw.__file__).parent / '_impl' / '__pyinstaller')

a = Analysis(
    [str(DEBITOS_DIR / 'main.py')],
    pathex=[str(DEBITOS_DIR)],
    binaries=[],
    datas=[
        (str(DEBITOS_DIR / 'logo_save.png'), '.'),
        (str(DEBITOS_DIR / 'PLANILHA MODELO.xlsx'), '.'),
        *collect_data_files('patchright'),
        *collect_data_files('servicos_rf_login'),
        *collect_data_files('captcha_uipath'),
        *collect_data_files('google.genai'),
        *collect_data_files('google.generativeai'),
    ],
    hiddenimports=[
        'servicos_rf_login',
        'servicos_rf_login.login',
        'servicos_rf_login.log_manager',
        *collect_submodules('servicos_rf_login'),
        'captcha_uipath',
        'captcha_uipath.solver',
        *collect_submodules('captcha_uipath'),
        *collect_submodules('google.genai'),
        *collect_submodules('google.generativeai'),
        'pandas',
        'pandas.io.formats.style',
        'openpyxl',
        'openpyxl.styles',
        'openpyxl.styles.alignment',
        'openpyxl.utils',
        'openpyxl.utils.dataframe',
        'dotenv',
        'tkinter',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        'unicodedata',
        'argparse',
        'json',
        're',
        'time',
        'logging',
    ],
    hookspath=[PATCHRIGHT_HOOKS],
    runtime_hooks=[str(DEBITOS_DIR / 'rthook_patchright.py')],
    excludes=['matplotlib', 'scipy', 'IPython', 'jupyter', 'notebook', 'pytest'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Débitos em Aberto',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    icon=str(DEBITOS_DIR / 'debito.ico'),
)
