"""O dominio e puro — e isso e testado, nao combinado.

A pergunta que esta fatia precisa responder: "consigo decidir qual certificado
corresponde a empresa sem Windows, PowerShell, navegador ou portal?"

Os testes abaixo respondem por comportamento, e nao so por leitura de imports:
disco, ambiente e subprocesso sao substituidos por armadilhas que falham o teste
se forem tocados.
"""
import ast
import builtins
import os
import pathlib
import subprocess
import sys

import pytest
from casos_certificado import CERTS

from automation.domain import buscar_certificado

RAIZ = pathlib.Path(__file__).resolve().parents[1]
NUCLEO = sorted((RAIZ / "automation").glob("*.py"))

# Nada disso pode aparecer no nucleo.
PROIBIDOS = {
    "winreg", "ctypes", "subprocess", "os", "shutil",   # Windows e sistema
    "patchright", "playwright", "selenium",             # navegador
    "pandas", "openpyxl",                               # planilha
    "google", "requests", "urllib", "http", "socket",   # rede
    "tkinter",                                          # interface desktop
    "servicos_rf_login", "resolvedor_captcha", "ecac_login", "cert_windows",
    "main", "ui_upload",
}

DISPONIVEIS = {k: v["subject_cn"] for k, v in CERTS.items()}
NOMES = ["Bernardo", "D", "ALVORADO COMERCIO", "nao existe", "XYZ", ""]


def _exercitar():
    """Passa por todos os caminhos: resolvido, ambiguo e sem match."""
    return [buscar_certificado(n, DISPONIVEIS) for n in NOMES]


# ── Prova estatica ────────────────────────────────────────────────────────────

def test_o_nucleo_foi_encontrado():
    """Guarda contra varredura vazia."""
    assert len(NUCLEO) >= 2


@pytest.mark.parametrize("modulo", NUCLEO, ids=lambda p: p.name)
def test_o_nucleo_nao_importa_efeito_externo(modulo):
    arvore = ast.parse(modulo.read_text(encoding="utf-8"))
    importados: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.level == 0 and no.module:
            importados.add(no.module.split(".")[0])

    usados = importados & PROIBIDOS
    assert not usados, f"{modulo.name} importa {sorted(usados)}."


def test_o_nucleo_usa_purepath_e_nao_path():
    """`PurePath` nao tem `open` nem `exists`: a escolha e o que garante que a
    normalizacao seja manipulacao de string, e nao acesso a disco."""
    fonte = (RAIZ / "automation" / "domain.py").read_text(encoding="utf-8")
    assert "PurePath" in fonte
    assert "from pathlib import Path" not in fonte


# ── Prova comportamental ──────────────────────────────────────────────────────

def test_nao_abre_arquivo(monkeypatch):
    def proibido(*args, **kwargs):
        raise AssertionError("o dominio abriu um arquivo")

    monkeypatch.setattr(builtins, "open", proibido)
    assert _exercitar()


def test_nao_le_o_ambiente(monkeypatch):
    class AmbienteProibido(dict):
        def __getitem__(self, chave):
            raise AssertionError("o dominio leu o ambiente")

        def get(self, *args, **kwargs):
            raise AssertionError("o dominio leu o ambiente")

    monkeypatch.setattr(os, "environ", AmbienteProibido())
    assert _exercitar()


def test_nao_executa_subprocesso(monkeypatch):
    def proibido(*args, **kwargs):
        raise AssertionError("o dominio executou um subprocesso")

    for alvo in ("run", "Popen", "check_output", "call"):
        monkeypatch.setattr(subprocess, alvo, proibido)
    assert _exercitar()


def test_nao_toca_o_disco_por_pathlib(monkeypatch):
    for alvo in ("exists", "open", "is_file", "stat"):
        monkeypatch.setattr(
            pathlib.Path, alvo,
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("tocou o disco")),
        )
    assert _exercitar()


def test_importar_o_dominio_nao_arrasta_nada_pesado():
    """Num interpretador limpo, importar o dominio nao carrega Windows nem navegador."""
    # `winreg` fica de fora de proposito: no Windows ele ja esta em sys.modules
    # antes de qualquer import do projeto — e do interpretador, nao nosso.
    codigo = (
        "import sys; import automation.domain; "
        "pesados=[m for m in ('patchright','playwright','pandas','openpyxl',"
        "'tkinter','subprocess','ctypes') if m in sys.modules]; "
        "print(','.join(pesados))"
    )
    saida = subprocess.run(  # noqa: S603 — comando fixo, montado aqui mesmo
        [sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True, check=True
    )
    assert saida.stdout.strip() == "", f"o dominio carregou {saida.stdout.strip()}"


def test_a_regra_nao_depende_de_plataforma():
    """A decisao vem so de string e dos dados recebidos — nao ha ramo por SO."""
    fonte = (RAIZ / "automation" / "domain.py").read_text(encoding="utf-8")
    for marca in ("sys.platform", "os.name", "platform.system"):
        assert marca not in fonte, f"o dominio ramifica por plataforma ({marca})."
    assert buscar_certificado("Bernardo", DISPONIVEIS).resolvida
