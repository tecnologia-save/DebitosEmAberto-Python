"""O chao por execucao: copia de trabalho isolada, e a origem intacta.

A pergunta desta fatia: a aplicacao pode continuar gravando na planilha in-place
sem que isso alcance o arquivo que a plataforma entregou?

Nada aqui abre navegador, le o Certificate Store, chama PowerShell ou toca o
portal — e o ultimo teste do arquivo prova isso pelos imports do modulo, e nao
por promessa.
"""
import ast
import pathlib
import shutil
import subprocess
import zipfile

import openpyxl
import pytest
from planilhas_sinteticas import criar_planilha

from automation import espaco_de_trabalho
from automation.planilha import PlanilhaIndisponivel

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def _valida(caminho):
    criar_planilha(str(caminho))
    return caminho


# ── o caminho feliz ──────────────────────────────────────────────────────────

def test_planilha_valida_vira_copia_de_trabalho(tmp_path):
    origem = _valida(tmp_path / "entrada.xlsx")

    with espaco_de_trabalho.abrir(origem) as espaco:
        assert espaco.planilha.exists()
        assert espaco.planilha.parent == espaco.raiz
        openpyxl.load_workbook(espaco.planilha).close()


def test_o_caminho_de_ORIGEM_pode_nao_ter_extensao(tmp_path):
    """Anexo materializado pela plataforma tem nome interno. A extensao do
    caminho recebido nao e evidencia de nada — quem responde sao os bytes."""
    origem = _valida(tmp_path / "temporario.xlsx")
    sem_extensao = tmp_path / "0f3a9c11-anexo"
    shutil.move(str(origem), str(sem_extensao))

    with espaco_de_trabalho.abrir(sem_extensao) as espaco:
        assert espaco.planilha.name == "planilha.xlsx"
        openpyxl.load_workbook(espaco.planilha).close()


def test_a_copia_de_trabalho_SEMPRE_se_chama_planilha_xlsx(tmp_path):
    """O `openpyxl` recusa de saida uma extensao que nao reconhece: quem sabe o
    formato e quem nomeia."""
    origem = _valida(tmp_path / "entrada.xlsx")

    with espaco_de_trabalho.abrir(origem) as espaco:
        assert espaco.planilha.name == espaco_de_trabalho.NOME_DA_PLANILHA
        assert espaco.planilha.suffix == ".xlsx"


# ── a origem nao e nossa ─────────────────────────────────────────────────────

def test_gravar_na_copia_NAO_alcanca_o_anexo_recebido(tmp_path):
    """O contrato central desta fatia. A aplicacao grava na planilha o tempo
    todo — e assim que ela retoma —, e o anexo da plataforma nao e dela."""
    origem = _valida(tmp_path / "entrada.xlsx")
    antes = origem.read_bytes()

    with espaco_de_trabalho.abrir(origem) as espaco:
        wb = openpyxl.load_workbook(espaco.planilha)
        wb["Empresas"].cell(row=2, column=4, value="Concluído")
        wb.save(espaco.planilha)
        wb.close()
        assert espaco.planilha.read_bytes() != antes, "a copia mudou mesmo"

    assert origem.read_bytes() == antes, "SOURCE_ATTACHMENT_MUTATED"


# ── falha fechada ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nome,conteudo", [
    ("vazio", b""),
    ("texto", b"linha um\nlinha dois\n"),
    ("csv", b"CNPJ,EMPRESA,CERTIFICADO\n1,2,3\n"),
    ("html", b"<!DOCTYPE html><html><body>erro</body></html>"),
])
def test_o_que_nao_e_planilha_e_RECUSADO(tmp_path, nome, conteudo):
    origem = tmp_path / f"{nome}.xlsx"
    origem.write_bytes(conteudo)

    with pytest.raises(PlanilhaIndisponivel):
        with espaco_de_trabalho.abrir(origem):
            pass


def test_zip_que_nao_e_xlsx_e_RECUSADO(tmp_path):
    """Assinatura de ZIP nao e prova: falta o OOXML dentro."""
    origem = tmp_path / "pacote.xlsx"
    with zipfile.ZipFile(origem, "w") as zf:
        zf.writestr("leiame.txt", "isto nao e uma planilha")

    with pytest.raises(PlanilhaIndisponivel):
        with espaco_de_trabalho.abrir(origem):
            pass


def test_xlsx_TRUNCADO_e_recusado(tmp_path):
    origem = _valida(tmp_path / "entrada.xlsx")
    inteiro = origem.read_bytes()
    origem.write_bytes(inteiro[: len(inteiro) // 2])

    with pytest.raises(PlanilhaIndisponivel):
        with espaco_de_trabalho.abrir(origem):
            pass


def test_origem_inexistente_e_recusada_com_mensagem_SEM_o_caminho(tmp_path):
    ausente = tmp_path / "nao existe" / "cliente real.xlsx"

    with pytest.raises(PlanilhaIndisponivel) as erro:
        with espaco_de_trabalho.abrir(ausente):
            pass

    assert "cliente real" not in str(erro.value)
    assert str(ausente) not in str(erro.value)


# ── isolamento entre execucoes ───────────────────────────────────────────────

def test_duas_execucoes_NAO_dividem_chao(tmp_path):
    origem = _valida(tmp_path / "entrada.xlsx")

    with espaco_de_trabalho.abrir(origem) as a, espaco_de_trabalho.abrir(origem) as b:
        assert a.raiz != b.raiz
        assert a.planilha != b.planilha

        wb = openpyxl.load_workbook(a.planilha)
        wb["Empresas"].cell(row=2, column=4, value="Concluído")
        wb.save(a.planilha)
        wb.close()

        assert a.planilha.read_bytes() != b.planilha.read_bytes()


def test_a_unicidade_nao_depende_de_relogio_nem_de_id_externo(tmp_path):
    """Sem `sleep` e sem run_id: quem garante o inedito e o proprio sistema."""
    origem = _valida(tmp_path / "entrada.xlsx")
    raizes = []
    for _ in range(5):
        with espaco_de_trabalho.abrir(origem) as espaco:
            raizes.append(espaco.raiz)

    assert len(set(raizes)) == 5


# ── limpeza ──────────────────────────────────────────────────────────────────

def test_o_chao_some_no_fim(tmp_path):
    origem = _valida(tmp_path / "entrada.xlsx")

    with espaco_de_trabalho.abrir(origem) as espaco:
        raiz = espaco.raiz
        assert raiz.exists()

    assert not raiz.exists()


def test_o_chao_some_TAMBEM_quando_a_execucao_morre(tmp_path):
    origem = _valida(tmp_path / "entrada.xlsx")
    raiz = None

    with pytest.raises(ZeroDivisionError):
        with espaco_de_trabalho.abrir(origem) as espaco:
            raiz = espaco.raiz
            raise ZeroDivisionError("a execução morreu no meio")

    assert raiz is not None
    assert not raiz.exists()


def test_planilha_recusada_nao_deixa_chao_para_tras(tmp_path):
    origem = tmp_path / "vazia.xlsx"
    origem.write_bytes(b"")
    antes = set(pathlib.Path(espaco_de_trabalho.tempfile.gettempdir()).glob(
        f"{espaco_de_trabalho._PREFIXO}*"))

    with pytest.raises(PlanilhaIndisponivel):
        with espaco_de_trabalho.abrir(origem):
            pass

    depois = set(pathlib.Path(espaco_de_trabalho.tempfile.gettempdir()).glob(
        f"{espaco_de_trabalho._PREFIXO}*"))
    assert depois <= antes


# ── o que este caminho NAO toca ──────────────────────────────────────────────

def test_a_fundacao_nao_conhece_windows_navegador_nem_portal():
    """Prova por import, e nao por promessa: o caminho novo e offline."""
    arvore = ast.parse((RAIZ / "automation" / "espaco_de_trabalho.py").read_text(
        encoding="utf-8"))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])

    proibidos = {"winreg", "ctypes", "subprocess", "patchright", "playwright",
                 "requests", "urllib", "http", "socket", "tkinter", "os",
                 "servicos_rf_login", "resolvedor_captcha", "ecac_login",
                 "cert_windows", "main", "ui_upload"}
    assert not (importados & proibidos), importados & proibidos


# ── a planilha do cliente nao entra no Git ───────────────────────────────────

def _ignorado(caminho: str) -> bool:
    resultado = subprocess.run(  # noqa: S603 — git local, sem rede
        ["git", "check-ignore", "-q", caminho],  # noqa: S607 — `git` do PATH
        cwd=RAIZ, capture_output=True, check=False)
    return resultado.returncode == 0


def test_planilha_de_execucao_na_raiz_fica_FORA_do_git():
    """Ela tem CNPJ e razão social dentro, e vive na raiz durante o uso."""
    assert _ignorado("Empresas Janeiro.xlsx")


def test_a_planilha_MODELO_continua_rastreavel():
    assert not _ignorado("PLANILHA MODELO.xlsx")


def test_a_regra_nao_alcanca_fixtures_fora_da_raiz():
    assert not _ignorado("tests/fixtures/planilha.xlsx")
