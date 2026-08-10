# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from pathlib import Path
import patchright as _pw

# SPECPATH é injetado pelo PyInstaller no namespace do .spec (já é o diretório).
# __file__ não existe nesse namespace a partir do PyInstaller 6.x.
DEBITOS_DIR      = Path(SPECPATH)
PATCHRIGHT_HOOKS = str(Path(_pw.__file__).parent / '_impl' / '__pyinstaller')

# ── Chave Gemini embutida no binário ─────────────────────────────────────────
# O .env do projeto é gitignored, então a chave nunca chega ao repositório.
# Aqui ela é copiada para dentro do exe no momento do build, para que o
# executável funcione em qualquer máquina sem depender de um .env ao lado dele.
# Só a GEMINI_API_KEY entra — a senha do certificado fica de fora de propósito.
# Consequência: quem tiver o .exe consegue extrair a chave. Distribua o binário
# apenas internamente.
_chave = ''
_env_origem = DEBITOS_DIR / '.env'
if _env_origem.exists():
    for _linha in _env_origem.read_text(encoding='utf-8').splitlines():
        if _linha.strip().startswith('GEMINI_API_KEY='):
            _chave = _linha.split('=', 1)[1].strip()

_chave_arquivo = DEBITOS_DIR / 'build' / 'chave_gemini.env'
_chave_arquivo.parent.mkdir(parents=True, exist_ok=True)
_chave_arquivo.write_text(f'GEMINI_API_KEY={_chave}\n', encoding='utf-8')

if _chave:
    print(f'[spec] Chave Gemini embutida ({len(_chave)} chars, '
          f'{_chave[:6]}...{_chave[-4:]}).')
else:
    print('[spec] AVISO: GEMINI_API_KEY vazia ou ausente no .env — '
          'o exe sairá sem chave e o captcha nao sera resolvido.')

a = Analysis(
    [str(DEBITOS_DIR / 'main.py')],
    pathex=[str(DEBITOS_DIR)],
    binaries=[],
    datas=[
        (str(DEBITOS_DIR / 'logo_save.png'), '.'),
        (str(DEBITOS_DIR / 'PLANILHA MODELO.xlsx'), '.'),
        (str(_chave_arquivo), '.'),
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
