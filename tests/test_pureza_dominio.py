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
AUTOMATION = sorted((RAIZ / "automation").glob("*.py"))

# As INTEGRACOES podem tocar o mundo — cada uma o seu pedaco, e so ela.
# Todo o resto e nucleo e nao toca efeito externo nenhum.
PLANILHA = "planilha.py"
CERTIFICADOS = "certificados_windows.py"
CAPTCHA = "captcha.py"
INTEGRACOES = {PLANILHA, CERTIFICADOS, CAPTCHA}
NUCLEO = [m for m in AUTOMATION if m.name not in INTEGRACOES]

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
    assert {m.name for m in AUTOMATION} >= INTEGRACOES, "a integracao existe e esta na lista"


def _importa(modulo, pacotes: set[str]) -> bool:
    arvore = ast.parse(modulo.read_text(encoding="utf-8-sig"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            if pacotes & {a.name.split(".")[0] for a in no.names}:
                return True
        elif isinstance(no, ast.ImportFrom) and no.level == 0 and no.module:
            if no.module.split(".")[0] in pacotes:
                return True
    return False


def _importa_no_topo(modulo, pacotes: set[str]) -> bool:
    """So os imports de nivel de modulo — um import preguicoso dentro de funcao
    nao e carregado quando alguem importa o modulo."""
    arvore = ast.parse(modulo.read_text(encoding="utf-8-sig"))
    for no in arvore.body:
        if isinstance(no, ast.Import):
            if pacotes & {a.name.split(".")[0] for a in no.names}:
                return True
        elif isinstance(no, ast.ImportFrom) and no.level == 0 and no.module:
            if no.module.split(".")[0] in pacotes:
                return True
    return False


def test_so_a_integracao_conhece_openpyxl_e_pandas():
    """A fronteira arquitetural da fatia 4: DataFrame e Workbook moram num lugar so.

    Por IMPORT, e nao por texto: os outros modulos citam openpyxl em comentario
    para explicar por que so .xlsx passa — citar e diferente de depender.
    """
    culpados = {m.name for m in AUTOMATION if _importa(m, {"openpyxl", "pandas"})}
    assert culpados == {PLANILHA}, f"esperado {PLANILHA}, encontrado {culpados}"


def test_so_a_integracao_windows_conhece_subprocess():
    """A fronteira arquitetural da fatia 5A: PowerShell mora num lugar so."""
    culpados = {m.name for m in AUTOMATION if _importa(m, {"subprocess", "winreg", "ctypes"})}
    assert culpados == {CERTIFICADOS}, f"esperado {CERTIFICADOS}, encontrado {culpados}"


def test_a_integracao_windows_nao_altera_o_sistema():
    """5A e READ-ONLY. Registro, policy, elevacao e guardiao ficam no legado.

    Por IMPORT para os modulos, e por CHAMADA para as funcoes: o docstring cita
    "guardiao" e "registro" justamente para dizer que nao os toca, e citar e
    diferente de usar.
    """
    modulo = RAIZ / "automation" / CERTIFICADOS
    assert not _importa(modulo, {"winreg", "ctypes", "patchright", "playwright",
                                 "tkinter", "openpyxl", "pandas"})

    fonte = modulo.read_text(encoding="utf-8")
    for chamada in ("ShellExecute", "SetValueEx", "CreateKey", "DeleteKey",
                    "AutoSelectCertificateForUrls", "REG_PATH", "iniciar_guarda("):
        assert chamada not in fonte, f"a descoberta toca {chamada}."


def test_so_a_fronteira_do_captcha_conhece_o_navegador():
    """Page e Locator podem atravessar a fronteira do captcha — e integracao
    stateful com o browser. Mas nao passam dali."""
    culpados = {m.name for m in AUTOMATION if _importa(m, {"patchright", "playwright"})}
    assert culpados == {CAPTCHA}, f"esperado {CAPTCHA}, encontrado {culpados}"


def test_a_fronteira_do_captcha_nao_le_o_ambiente_nem_o_fork():
    """A chave chega por parametro. O fork so e importado sob demanda, dentro da
    funcao, para que a fronteira continue carregavel sem ele."""
    modulo = RAIZ / "automation" / CAPTCHA
    assert not _importa(modulo, {"os", "dotenv", "google"})
    assert not _importa_no_topo(modulo, {"resolvedor_captcha"}), (
        "o fork entra sob demanda, dentro da função"
    )
    assert _importa(modulo, {"resolvedor_captcha"}), "mas o default ainda é ele"

    # Sem `import os` a leitura do ambiente e impossivel — o docstring cita
    # `os.environ` justamente para explicar que quem o le e o adapter, nao aqui.
    assert "print(" not in modulo.read_text(encoding="utf-8")


def test_a_fronteira_do_captcha_nao_tem_chave_padrao():
    """Nenhuma chave embutida, nenhum default, nenhum placeholder utilizavel."""
    fonte = (RAIZ / "automation" / CAPTCHA).read_text(encoding="utf-8")

    assert "AIza" not in fonte, "nada com cara de chave do Google"
    assert "api_key: str = field(repr=False)" in fonte, "obrigatoria, sem default"


def test_a_integracao_windows_nao_imprime():
    fonte = (RAIZ / "automation" / CERTIFICADOS).read_text(encoding="utf-8")
    assert "print(" not in fonte


def test_o_comando_powershell_nao_tem_interpolacao():
    """SECURITY: o comando e literal. Nenhuma f-string, format ou concatenacao
    com dado externo — nao ha superficie de injecao para fechar."""
    fonte = (RAIZ / "automation" / CERTIFICADOS).read_text(encoding="utf-8")
    trecho = fonte[fonte.index("COMANDO_POWERSHELL = ("):fonte.index("_RE_CN_ICP")]

    assert 'f"' not in trecho, "sem f-string no comando"
    assert ".format(" not in trecho
    assert " % " not in trecho
    assert " + " not in trecho, "sem concatenação com dado externo"


def test_a_integracao_nao_conhece_o_resto_do_mundo():
    """openpyxl e pandas sim; Windows, navegador, Gemini e Tkinter nao."""
    fonte = (RAIZ / "automation" / "planilha.py").read_text(encoding="utf-8")
    for proibido in ("winreg", "subprocess", "patchright", "playwright", "tkinter",
                     "google", "requests", "ctypes", "cert_windows",
                     "servicos_rf_login", "resolvedor_captcha"):
        assert proibido not in fonte, f"a integracao de planilha importa {proibido}."


def test_a_integracao_nao_imprime():
    """Diagnostico e do adapter. Sem isto, a integracao decidiria o que vai para
    o log — e ela manipula CNPJ, empresa e conteudo de celula."""
    fonte = (RAIZ / "automation" / "planilha.py").read_text(encoding="utf-8")
    assert "print(" not in fonte


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


def test_a_integracao_so_embrulha_erro_com_mensagem_constante():
    """Onde ela embrulha um erro externo, corta o encadeamento com `from None`.

    A mensagem do openpyxl e a do sistema de arquivos carregam o caminho
    completo; deixa-las penduradas na nossa exception vazaria o nome do cliente.
    Que as mensagens sao mesmo seguras esta provado por comportamento em
    `test_planilha_recurso.py`, com sentinelas plantadas no caminho.
    """
    fonte = (RAIZ / "automation" / "planilha.py").read_text(encoding="utf-8")
    embrulhos = fonte.count("raise PlanilhaIndisponivel")
    assert embrulhos >= 4
    assert fonte.count("from None") >= 2, "os que nascem de erro externo cortam a cadeia"


def test_main_nao_manipula_mais_celula_nem_indice_de_linha():
    """Depois da fatia 4, main orquestra e imprime; quem mexe em célula é a
    integração."""
    principal = RAIZ / "main.py"
    assert not _importa(principal, {"openpyxl"}), "main.py ainda importa openpyxl."

    # Por texto aqui e proposital: nao ha como chamar isto sem manipular planilha.
    fonte = principal.read_text(encoding="utf-8-sig")
    for marca in ("iter_rows", "create_sheet", ".max_row", "ws.cell(", "sheetnames",
                  "Alignment("):
        assert marca not in fonte, f"main.py ainda manipula planilha ({marca})."
