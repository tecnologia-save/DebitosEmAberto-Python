"""Runtime hook PyInstaller — configura o caminho dos browsers patchright.

Quando o exe é executado em outra máquina, procura a pasta ms-playwright
ao lado do próprio executável antes de usar o padrão do sistema.
"""
import os
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    _exe_dir = Path(sys.executable).parent
    _local_browsers = _exe_dir / "ms-playwright"
    if _local_browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_local_browsers))
