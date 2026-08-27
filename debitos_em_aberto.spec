# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from pathlib import Path
import patchright as _pw

# SPECPATH é injetado pelo PyInstaller no namespace do .spec (já é o diretório).
# __file__ não existe nesse namespace a partir do PyInstaller 6.x.
DEBITOS_DIR      = Path(SPECPATH)
PATCHRIGHT_HOOKS = str(Path(_pw.__file__).parent / '_impl' / '__pyinstaller')

# ── A chave do Gemini NAO entra no binário (fatia 13B) ───────────────────────
# Até aqui o build lia a chave do serviço de captcha do `.env` do operador,
# gravava uma cópia em `build/` e a injetava no bundle, para que o .exe
# funcionasse em qualquer máquina sem configuração. O próprio comentário antigo
# reconhecia a consequência: quem tivesse o .exe extraía a chave.
#
# LEGACY_EMBEDDED_SECRET_REMOVAL_BEHAVIOR_CHANGE. O executável passa a exigir
# configuração externa — variável de ambiente ou `.env` ao lado dele — e falha
# de forma segura sem ela. Um binário distribuído deixa de ser um portador de
# credencial.
#
# Nenhum literal jamais esteve neste arquivo: a chave vinha do `.env`, que é
# gitignored. O que sai daqui é o MECANISMO.

a = Analysis(
    [str(DEBITOS_DIR / 'main.py')],
    pathex=[str(DEBITOS_DIR)],
    binaries=[],
    datas=[
        (str(DEBITOS_DIR / 'logo_save.png'), '.'),
        (str(DEBITOS_DIR / 'PLANILHA MODELO.xlsx'), '.'),
        *collect_data_files('patchright'),
        *collect_data_files('servicos_rf_login'),
        *collect_data_files('resolvedor_captcha'),
        *collect_data_files('google.genai'),
        *collect_data_files('google.generativeai'),
    ],
    hiddenimports=[
        'cert_windows',
        'pywinauto',
        'servicos_rf_login.cert_dialog',
        'winreg',
        'servicos_rf_login',
        'servicos_rf_login.login',
        'servicos_rf_login.log_manager',
        *collect_submodules('servicos_rf_login'),
        'resolvedor_captcha',
        'resolvedor_captcha.solver',
        *collect_submodules('resolvedor_captcha'),
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
    icon=str(DEBITOS_DIR / 'icone.ico'),
)
