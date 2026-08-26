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
FISCAL = "consulta_fiscal.py"
NAVEGADOR = "navegador.py"
INTEGRACOES = {PLANILHA, CERTIFICADOS, CAPTCHA, FISCAL, NAVEGADOR}
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


def test_so_as_integracoes_de_browser_conhecem_o_navegador():
    """Page e Locator atravessam as duas fronteiras que falam com o navegador —
    captcha e consulta fiscal. Nao passam dali."""
    culpados = {m.name for m in AUTOMATION if _importa(m, {"patchright", "playwright"})}
    assert culpados == {CAPTCHA, FISCAL, NAVEGADOR}, f"encontrado {culpados}"


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


# ── Fatia 7A: o protocolo da policy ──────────────────────────────────────────

POLICY = "policy_certificado.py"


def _codigo_sem_docstrings(modulo) -> str:
    """A fonte sem docstring nenhuma.

    Quatro vezes nesta migracao uma checagem por texto falhou porque o modulo
    DOCUMENTAVA o que nao faz. O que interessa e o codigo.
    """
    arvore = ast.parse(modulo.read_text(encoding="utf-8-sig"))
    for no in ast.walk(arvore):
        corpo = getattr(no, "body", None)
        if not corpo or not isinstance(
            no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        if (
            isinstance(corpo[0], ast.Expr)
            and isinstance(corpo[0].value, ast.Constant)
            and isinstance(corpo[0].value.value, str)
        ):
            corpo[0].value.value = ""
    return ast.unparse(arvore)


def test_o_protocolo_da_policy_e_nucleo_e_nao_integracao():
    """Ele decide, nao executa: winreg, ctypes e o processo elevado ficam em
    cert_windows.py, e entram por parametro."""
    modulo = RAIZ / "automation" / POLICY
    assert modulo.exists()
    assert modulo in NUCLEO, "esta no nucleo, entao ja passa pela varredura geral"

    codigo = _codigo_sem_docstrings(modulo)
    for chamada in ("SetValueEx", "DeleteKey", "CreateKeyEx", "ShellExecute",
                    "OpenProcess", "sys.executable", "time.sleep", "winreg", "ctypes"):
        assert chamada not in codigo, f"o protocolo executa {chamada}."


def test_o_protocolo_da_policy_nao_imprime():
    assert "print(" not in _codigo_sem_docstrings(RAIZ / "automation" / POLICY)


def test_o_protocolo_da_policy_roda_sem_windows():
    """Num interpretador limpo, importa-lo nao carrega nada de Windows."""
    codigo = (
        "import sys; import automation.policy_certificado; "
        "pesados=[m for m in ('ctypes','subprocess','patchright','openpyxl','pandas') "
        "if m in sys.modules]; print(','.join(pesados))"
    )
    saida = subprocess.run(  # noqa: S603 — comando fixo, montado aqui mesmo
        [sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True, check=True
    )
    assert saida.stdout.strip() == "", f"o protocolo carregou {saida.stdout.strip()}"


def test_o_guardiao_continua_entrypoint_interno():
    """`--guard <pid> <b64>` é modo interno de processo elevado, NÃO input da
    execução. A boundary da fatia 3 segue com um campo só."""
    from automation.boundary import CAMPOS_CONHECIDOS

    assert CAMPOS_CONHECIDOS == {"planilha"}

    principal = (RAIZ / "main.py").read_text(encoding="utf-8-sig")
    assert "--guard" in principal, "o modo existe"
    assert "add_argument(\"--guard" not in principal, "mas não é argumento da CLI"


# ── Fatia 7B: a fronteira do login ───────────────────────────────────────────

LOGIN = "login.py"


def test_a_fronteira_do_login_nao_conhece_a_policy():
    """A 7A provou lifecycles independentes. O login recebe UMA informação —
    posso confiar no auto-select? — e nada sobre registro, guardião ou limpeza."""
    modulo = RAIZ / "automation" / LOGIN
    assert not _importa(modulo, {"winreg", "ctypes", "subprocess", "cert_windows"})

    codigo = _codigo_sem_docstrings(modulo)
    for proibido in ("policy_certificado", "garantir_policy", "ResultadoDaPolicy",
                     "limpar_autoselect", "iniciar_guarda", "definir_autoselect"):
        assert proibido not in codigo, f"o login conhece {proibido}."


def test_a_fronteira_do_login_nao_le_o_ambiente_nem_importa_o_fork():
    modulo = RAIZ / "automation" / LOGIN
    assert not _importa(modulo, {"os", "servicos_rf_login", "resolvedor_captcha",
                                 "patchright", "playwright", "dotenv"})

    codigo = _codigo_sem_docstrings(modulo)
    assert "os.environ" not in codigo
    assert "print(" not in codigo


def test_a_sessao_nao_vaza_para_o_nucleo():
    """SessaoReceita e os objetos de navegador nao atravessam para domain,
    status_portal, boundary nem planilha."""
    for modulo in ("domain.py", "status_portal.py", "boundary.py", "planilha.py"):
        codigo = _codigo_sem_docstrings(RAIZ / "automation" / modulo)
        for proibido in ("SessaoReceita", "playwright", "Page", "context"):
            assert proibido not in codigo, f"{modulo} conhece {proibido}."


def test_main_nao_manipula_mais_a_tupla_do_navegador():
    fonte = (RAIZ / "main.py").read_text(encoding="utf-8-sig")

    assert "browser_aberto" not in fonte
    assert "p, context, page = " not in fonte
    assert "sessao.pagina" in fonte, "os recursos passaram a ter nome"


# ── Fatia 8A: a fronteira da representacao ───────────────────────────────────

REPRESENTACAO = "representacao.py"


def test_a_representacao_nao_conhece_login_nem_policy():
    """§16: recebe sessao pronta. Nao autentica, nao garante policy, nao toca
    registro nem UAC."""
    modulo = RAIZ / "automation" / REPRESENTACAO
    assert not _importa(modulo, {"winreg", "ctypes", "subprocess", "cert_windows",
                                 "servicos_rf_login", "resolvedor_captcha",
                                 "patchright", "playwright", "os"})

    codigo = _codigo_sem_docstrings(modulo)
    for proibido in ("autenticar", "fazer_login", "garantir_policy",
                     "ResultadoDaPolicy", "policy_certificado", "SessaoReceita",
                     "os.environ", "print("):
        assert proibido not in codigo, f"a representacao conhece {proibido}."


def test_a_representacao_nao_e_dona_da_sessao():
    codigo = _codigo_sem_docstrings(RAIZ / "automation" / REPRESENTACAO)

    for proibido in ("close", "stop", "encerrar", "logout"):
        assert proibido not in codigo, f"a representacao chama {proibido}."


def test_main_nao_passa_mais_page_para_o_fluxo_por_cnpj():
    """`processar_cnpj` recebe a sessao; quem desce para a navegacao legada e
    `sessao.pagina`, dentro dela."""
    fonte = (RAIZ / "main.py").read_text(encoding="utf-8-sig")

    assert "def processar_cnpj(sessao, cnpj" in fonte
    assert "processar_cnpj(sessao, cnpj, row, caminho_planilha)" in fonte


def test_o_legado_sinaliza_desfecho_por_tipo_e_nao_por_exception_nua():
    """Depois da 8A, nenhum `raise Exception(` sobrou em trocar_perfil_procurador."""
    fonte = (RAIZ / "main.py").read_text(encoding="utf-8-sig")
    inicio = fonte.index("def trocar_perfil_procurador")
    trecho = fonte[inicio:fonte.index("def verificar_pendencias")]

    assert "raise Exception(" not in trecho
    assert trecho.count("representacao.AntiBotEsgotado") == 2
    assert trecho.count("representacao.RepresentacaoNaoConfirmada") == 2


# ── Fatia 8B1 e 8B2: a consulta fiscal ───────────────────────────────────────

def test_a_consulta_fiscal_nao_conhece_planilha():
    """A prova do corte: se ela nao sabe o que e coluna, aba ou Workbook, entao
    extrair e persistir nunca estiveram entrelacados."""
    modulo = RAIZ / "automation" / FISCAL
    assert not _importa(modulo, {"openpyxl", "pandas", "os", "winreg", "ctypes",
                                 "subprocess", "servicos_rf_login", "resolvedor_captcha",
                                 "main"})

    codigo = _codigo_sem_docstrings(modulo)
    for proibido in ("planilha", "SessaoPlanilha", "escrever_coluna", "escrever_aba",
                     "salvar", "Worksheet", "DataFrame", "COL_", "Empresas",
                     "Débitos", "Processos Fiscais"):
        assert proibido not in codigo, f"a consulta fiscal conhece {proibido}."


def test_a_consulta_fiscal_nao_autentica_nem_representa():
    codigo = _codigo_sem_docstrings(RAIZ / "automation" / FISCAL)

    # Por CHAMADA: `closest('div')` do JavaScript contem "close", e citar nao e usar.
    for proibido in ("autenticar(", "fazer_login(", "garantir_policy(", "representar(",
                     "trocar_perfil(", ".encerrar(", ".close(", ".stop(", "print("):
        assert proibido not in codigo, f"a consulta fiscal chama {proibido}."


def test_os_seletores_de_navegacao_fiscal_sairam_do_main():
    """Os seletores de NAVEGACAO — status, botoes de acao, paginacao das listas,
    cards — vivem na integracao.

    O que ainda NAO saiu, e esta declarado: `_extrair_dados_pagina`,
    `_extrair_dados_pagina_processo` e `_processar_card_processo` continuam em
    main, injetados como leitores de conteudo. Sao o primeiro item da 8B2.
    """
    fonte = (RAIZ / "main.py").read_text(encoding="utf-8-sig")

    for seletor in ('XPATH_STATUS', 'aria-label*="DCTFWeb"',
                    'aria-label*="processo fiscal"',
                    'Expandir informações complementares'):
        assert seletor not in fonte, f"main.py ainda tem o seletor {seletor}."


def test_nao_sobrou_parsing_fiscal_em_main():
    """O criterio de conclusao da 8B2: nenhum leitor, nenhum JavaScript de
    parsing e nenhuma paginacao fiscal restaram em main."""
    fonte = (RAIZ / "main.py").read_text(encoding="utf-8-sig")

    for nome in ("_extrair_dados_pagina", "_extrair_dados_pagina_processo",
                 "_processar_card_processo", "_selecionar_n_por_pagina",
                 "_expandir_todas_as_linhas"):
        assert nome not in fonte, f"main.py ainda tem {nome}."

    for marca in ('aria-label="Página seguinte"', "chevron-down", "resultado.push",
                  "querySelectorAll", "text-nowrap", "processo-credito",
                  "ng-option-label", "URL_ANALISE_PENDENCIAS ="):
        assert marca not in fonte, f"main.py ainda tem {marca}."


def test_a_consulta_fiscal_nao_recebe_callback_de_parsing():
    """§18: o unico parametro externo das consultas e espera/navegacao generica."""
    import inspect

    from automation import consulta_fiscal

    dctfweb = set(inspect.signature(consulta_fiscal.consultar_dctfweb).parameters)
    processos = set(inspect.signature(consulta_fiscal.consultar_processos).parameters)

    # A 8A2 fechou TRANSITIONAL_NAVIGATION_CALLBACK: nao ha mais parametro externo.
    assert dctfweb == {"sessao", "cnpj"}
    assert processos == {"sessao", "cnpj"}


def test_a_consulta_fiscal_expoe_tres_operacoes_e_nao_uma():
    """RESUMABILITY_CONTRACT: a coluna D precisa poder ser gravada assim que o
    DCTFWeb termina. Uma operacao monolitica destruiria a retomada parcial."""
    from automation import consulta_fiscal

    for operacao in ("ler_situacao", "consultar_dctfweb", "consultar_processos"):
        assert callable(getattr(consulta_fiscal, operacao))


# ── Fatia 8A2: navegacao compartilhada e avisos ──────────────────────────────

def test_o_navegador_e_o_unico_lugar_com_politica_de_espera():
    """`_goto_seguro` e `_aguardar_networkidle` sairam de main: as integracoes de
    browser importam, o app nao fornece nada."""
    fonte = (RAIZ / "main.py").read_text(encoding="utf-8-sig")

    assert "aguardar_rede=" not in fonte
    assert "navegar=" not in fonte


def test_o_navegador_nao_imprime_e_nao_carrega_url_em_mensagem():
    """SENSITIVE_OUTPUT: a URL de uma sessao autenticada carrega identificadores.
    A falha de navegacao sobe como veio, sem mensagem nossa."""
    codigo = _codigo_sem_docstrings(RAIZ / "automation" / NAVEGADOR)

    assert "print(" not in codigo
    assert "page.url" not in codigo
    assert "page.title" not in codigo


def test_os_avisos_sao_constantes_de_um_conjunto_fechado():
    """Nenhum aviso carrega URL, CNPJ, empresa, valor ou texto do portal."""
    from automation import navegador

    avisos = [
        v for k, v in vars(navegador).items()
        if k.isupper() and isinstance(v, str) and not k.startswith("TIMEOUT")
    ]
    assert len(avisos) == 2
    for aviso in avisos:
        assert "{" not in aviso, "sem interpolação"
        assert "http" not in aviso


def test_so_a_extracao_fiscal_transporta_aviso():
    """§9: aviso so onde ha informacao operacional comprovadamente perdida.
    ResultadoDaRepresentacao nao tem — entao nao ganhou o campo."""
    import dataclasses

    from automation.consulta_fiscal import ExtracaoFiscal, SituacaoFiscal
    from automation.representacao import ResultadoDaRepresentacao

    assert "avisos" in {c.name for c in dataclasses.fields(ExtracaoFiscal)}
    for sem_aviso in (SituacaoFiscal, ResultadoDaRepresentacao):
        assert "avisos" not in {c.name for c in dataclasses.fields(sem_aviso)}
