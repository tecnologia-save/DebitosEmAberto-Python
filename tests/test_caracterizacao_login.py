"""Caracterizacao do login COMO ELE E HOJE — so o caminho que DebitosEmAberto usa.

Nenhum teste abre navegador, chama gov.br, usa certificado, chama Gemini, toca o
registro ou pede UAC. O dublê de navegador cobre os PRIMEIROS caminhos de saida;
tudo depois do clique em 'gov.br' e navegacao real e nao e alcancavel sem
browser — isso e um achado, nao uma omissao.

CNs, seriais e CNPJs sao ficticios.
"""
import json

import pytest
from navegador_falso import (
    ErroDeNavegacao,
    LocatorFalso,
    PaginaFalsa,
    PlaywrightFalso,
    SyncPlaywrightFalso,
)

from servicos_rf_login import login as login_rf

CN = "ALFA FICTICIA LTDA:11111111000191"
SERIAL = "0A01FICTICIO"
BOTAO_GOVBR = 'xpath=//*[@id="home-heading"]/div[1]/div/button'


@pytest.fixture
def perfil(tmp_path, monkeypatch):
    """O perfil do Chrome vive em project_dir; nos testes, em tmp_path."""
    monkeypatch.setenv("CERT_SUBJECT_CN", "")
    return tmp_path


def montar(monkeypatch, pagina=None, ao_lancar=None):
    pagina = pagina or PaginaFalsa()
    falso = PlaywrightFalso(pagina, ao_lancar=ao_lancar)
    monkeypatch.setattr(login_rf, "sync_playwright", SyncPlaywrightFalso(falso))
    return falso, pagina


# ── A · a assinatura publica ──────────────────────────────────────────────────

def test_a_a_api_publica_e_uma_funcao_so():
    import servicos_rf_login

    assert servicos_rf_login.__all__ == ["fazer_login"]
    assert servicos_rf_login.fazer_login is login_rf.main


def test_a_os_parametros_e_seus_defaults():
    """Eram oito ate a fatia 7B; `gemini_api_key` entrou em commit proprio para
    que o segredo pare de viajar por os.environ. Todos continuam opcionais."""
    import inspect

    parametros = inspect.signature(login_rf.main).parameters

    assert list(parametros) == [
        "cert_name", "cert_pfx_path", "cert_pfx_passphrase", "project_dir",
        "cnpj", "cert_subject_cn", "cert_serial", "policy_ok", "gemini_api_key",
    ]
    assert parametros["gemini_api_key"].default is None, "omitir = comportamento antigo"
    assert all(p.default is not inspect.Parameter.empty for p in parametros.values())
    assert parametros["policy_ok"].default is True
    assert parametros["cert_subject_cn"].default == ""


def test_b_debitosemaberto_usa_somente_o_modo_windows_store():
    """LEGACY_LIBRARY_CAPABILITY: cert_name, cert_pfx_path e cert_pfx_passphrase
    existem para o modo .pfx, e main.py nunca os informa."""
    import ast
    import pathlib

    arvore = ast.parse(
        (pathlib.Path(__file__).resolve().parents[1] / "automation" / "maquina.py")
        .read_text(encoding="utf-8")
    )
    # Ate a 7B a chamada estava em main.py; na 7B o main passou a INJETAR
    # `fazer_login` na fronteira; na 9B a injecao desceu para a fiacao, porque o
    # app nao conhece o fork. O fato caracterizado — so o modo Windows Store e
    # usado — continua valendo, e o ponto de injecao continua sendo UM.
    injecoes = [
        kw for no in ast.walk(arvore)
        if isinstance(no, ast.Call)
        for kw in no.keywords
        if kw.arg == "fazer_login"
    ]
    assert len(injecoes) == 1, "um unico ponto de login"

    fronteira = ast.parse(
        (pathlib.Path(__file__).resolve().parents[1] / "automation" / "login.py").read_text(
            encoding="utf-8"
        )
    )
    chamada = next(
        no for no in ast.walk(fronteira)
        if isinstance(no, ast.Call)
        and isinstance(no.func, ast.Name)
        and no.func.id == "fazer_login"
    )
    nomeados = {kw.arg for kw in chamada.keywords}
    assert nomeados == {
        "cert_subject_cn", "cert_serial", "policy_ok", "project_dir", "gemini_api_key"
    }
    assert not chamada.args, "tudo por nome"


# ── A flag de auto-selecao ────────────────────────────────────────────────────

def test_a_flag_de_auto_selecao_filtra_pelo_cn():
    flag = login_rf._build_auto_select_cert_flag(CN)

    assert flag.startswith("--auto-select-certificate-for-urls=")
    entradas = json.loads(flag.split("=", 1)[1])
    assert all(e["filter"]["SUBJECT"]["CN"] == CN for e in entradas)
    assert any("receita.fazenda.gov.br" in e["pattern"] for e in entradas)


def test_a_sem_cn_a_flag_fica_sem_filtro(monkeypatch):
    monkeypatch.setenv("CERT_SUBJECT_CN", "")

    entradas = json.loads(login_rf._build_auto_select_cert_flag("").split("=", 1)[1])

    assert all(e["filter"] == {} for e in entradas), "aceita QUALQUER certificado"


def test_a_a_flag_cai_no_ambiente_quando_o_cn_nao_e_passado(monkeypatch):
    """LOGIN_POSSIBLE_DEFECT: `CERT_SUBJECT_CN` no ambiente é um segundo canal
    para o mesmo dado, e ele é global ao processo."""
    monkeypatch.setenv("CERT_SUBJECT_CN", CN)

    entradas = json.loads(login_rf._build_auto_select_cert_flag().split("=", 1)[1])

    assert entradas[0]["filter"]["SUBJECT"]["CN"] == CN


# ── C · os caminhos que devolvem None ─────────────────────────────────────────

def test_c_falha_na_primeira_navegacao_devolve_none_e_para_o_playwright(
    perfil, monkeypatch, capsys
):
    pagina = PaginaFalsa(ao_navegar=ErroDeNavegacao("timeout"))
    falso, _ = montar(monkeypatch, pagina)
    monkeypatch.setattr(login_rf, "registrar_erro", lambda m: None)

    assert login_rf.main(project_dir=perfil, cert_subject_cn=CN) is None
    assert falso.parado is True, "limpa o que criou antes de devolver None"


def test_c_botao_govbr_ausente_devolve_none_e_para_o_playwright(
    perfil, monkeypatch
):
    pagina = PaginaFalsa(
        locators={BOTAO_GOVBR: LocatorFalso(ao_esperar=ErroDeNavegacao("nao apareceu"))}
    )
    falso, _ = montar(monkeypatch, pagina)
    monkeypatch.setattr(login_rf, "registrar_erro", lambda m: None)

    assert login_rf.main(project_dir=perfil, cert_subject_cn=CN) is None
    assert falso.parado is True


def test_c_o_contexto_nao_e_fechado_nos_caminhos_de_none(perfil, monkeypatch):
    """LOGIN_POSSIBLE_DEFECT: os caminhos de falha chamam `p.stop()` mas NUNCA
    `context.close()`. O Playwright para, e o perfil do Chrome fica como ficou."""
    pagina = PaginaFalsa(ao_navegar=ErroDeNavegacao("timeout"))
    falso, _ = montar(monkeypatch, pagina)
    monkeypatch.setattr(login_rf, "registrar_erro", lambda m: None)

    login_rf.main(project_dir=perfil, cert_subject_cn=CN)

    assert falso.contexto.fechado is False


def test_d_todas_as_causas_terminam_no_mesmo_none():
    """LOGIN_OUTCOME_INFORMATION_LOSS: sete pontos devolvem `None`, e nenhum
    carrega o motivo. Quem chama não distingue navegador que não subiu de
    certificado recusado, de gov.br bloqueado, de captcha esgotado."""
    import ast
    import pathlib

    fonte = (
        pathlib.Path(login_rf.__file__).read_text(encoding="utf-8")
    )
    arvore = ast.parse(fonte)
    funcao = next(
        no for no in arvore.body
        if isinstance(no, ast.FunctionDef) and no.name == "main"
    )

    retornos = [no for no in ast.walk(funcao) if isinstance(no, ast.Return)]
    nulos = [
        r for r in retornos
        if isinstance(r.value, ast.Constant) and r.value.value is None
    ]

    assert len(nulos) == 7, "sete saidas, um unico valor"
    assert len(retornos) - len(nulos) == 1, "e uma saida de sucesso"


def test_d_o_sucesso_devolve_uma_tupla_posicional_de_tres():
    """O contrato de sucesso é `(p, context, page)` — sem nomes, sem tipo."""
    import pathlib

    fonte = pathlib.Path(login_rf.__file__).read_text(encoding="utf-8")
    assert fonte.rstrip().endswith("return p, context, page")


# ── O perfil do Chrome ────────────────────────────────────────────────────────

def test_s_o_perfil_do_chrome_e_um_diretorio_fixo_dentro_do_projeto(
    perfil, monkeypatch
):
    """BROWSER_PROFILE_CONCURRENCY_RISK: `chrome_debug_profile` fica no diretório
    do projeto, é o MESMO para toda execução, e a porta de depuração é fixa."""
    pagina = PaginaFalsa(ao_navegar=ErroDeNavegacao("para aqui"))
    falso, _ = montar(monkeypatch, pagina)
    monkeypatch.setattr(login_rf, "registrar_erro", lambda m: None)

    login_rf.main(project_dir=perfil, cert_subject_cn=CN)

    kwargs = falso.kwargs_de_lancamento
    assert kwargs["user_data_dir"] == str(perfil / "chrome_debug_profile")
    assert "--remote-debugging-port=9222" in kwargs["args"]
    assert kwargs["headless"] is False, "o navegador é visível, sempre"


def test_s_o_perfil_persiste_entre_execucoes(perfil, monkeypatch):
    """É persistent context: cookies e sessão sobrevivem — é o que faz
    `_ja_logado` poder ser verdadeiro logo na abertura."""
    pagina = PaginaFalsa(ao_navegar=ErroDeNavegacao("para aqui"))
    falso, _ = montar(monkeypatch, pagina)
    monkeypatch.setattr(login_rf, "registrar_erro", lambda m: None)

    login_rf.main(project_dir=perfil, cert_subject_cn=CN)

    assert (perfil / "chrome_debug_profile" / "Default" / "Preferences").exists()
    assert falso.kwargs_de_lancamento["channel"] == "chrome"


def test_s_o_cn_vai_para_o_ambiente_do_processo(perfil, monkeypatch):
    """LOGIN_POSSIBLE_DEFECT: `CERT_SUBJECT_CN` é escrito em `os.environ` — mais
    um dado atravessando por estado global, como a chave do Gemini."""
    import os

    pagina = PaginaFalsa(ao_navegar=ErroDeNavegacao("para aqui"))
    montar(monkeypatch, pagina)
    monkeypatch.setattr(login_rf, "registrar_erro", lambda m: None)

    login_rf.main(project_dir=perfil, cert_subject_cn=CN)

    assert os.environ["CERT_SUBJECT_CN"] == CN


def test_s_a_flag_de_auto_selecao_entra_nos_args_do_chrome(perfil, monkeypatch):
    pagina = PaginaFalsa(ao_navegar=ErroDeNavegacao("para aqui"))
    falso, _ = montar(monkeypatch, pagina)
    monkeypatch.setattr(login_rf, "registrar_erro", lambda m: None)

    login_rf.main(project_dir=perfil, cert_subject_cn=CN)

    flags = [a for a in falso.kwargs_de_lancamento["args"] if "auto-select" in a]
    assert len(flags) == 1
    assert CN in flags[0]


def test_s_o_modo_windows_store_nao_entrega_pfx_ao_patchright(perfil, monkeypatch):
    pagina = PaginaFalsa(ao_navegar=ErroDeNavegacao("para aqui"))
    falso, _ = montar(monkeypatch, pagina)
    monkeypatch.setattr(login_rf, "registrar_erro", lambda m: None)

    login_rf.main(project_dir=perfil, cert_subject_cn=CN)

    assert "client_certificates" not in falso.kwargs_de_lancamento


# ── Deteccao de estado na pagina ──────────────────────────────────────────────

def test_ja_logado_procura_o_avatar():
    assert login_rf._ja_logado(
        PaginaFalsa(locators={"#avatar-dropdown-trigger": LocatorFalso(contagem=1)})
    ) is True
    assert login_rf._ja_logado(PaginaFalsa()) is False


def test_ja_logado_engole_erro_de_pagina():
    class PaginaQuebrada:
        def locator(self, s):
            raise ErroDeNavegacao("pagina fechada")

    assert login_rf._ja_logado(PaginaQuebrada()) is False


def test_acesso_bloqueado_procura_a_mensagem():
    seletor = "p:has-text('acesso foi bloqueado')"
    assert login_rf._acesso_bloqueado(
        PaginaFalsa(locators={seletor: LocatorFalso(contagem=1)})
    ) is True
    assert login_rf._acesso_bloqueado(PaginaFalsa()) is False


# ── R · o fallback pywinauto ──────────────────────────────────────────────────

def test_r_o_fallback_so_existe_quando_a_policy_nao_esta_ativa():
    """A condição exata: modo Windows Store, policy inativa e pywinauto presente."""
    import pathlib

    fonte = pathlib.Path(login_rf.__file__).read_text(encoding="utf-8")

    assert "if usar_windows_store and not policy_ok and _CERT_DIALOG_OK:" in fonte
    assert "kwargs={\"timeout\": 90.0}" in fonte, "90s esperando a janela nativa"
    assert "daemon=True" in fonte, "thread daemon: ninguém espera por ela"


def test_r_o_fallback_casa_pelo_serial_e_nao_pelo_cn():
    """Com CNs iguais, o serial é o que distingue — mesma preocupação da fatia 1."""
    import pathlib

    fonte = pathlib.Path(login_rf.__file__).read_text(encoding="utf-8")
    trecho = fonte[fonte.index("_selecionar_cert_dialog,"):]
    trecho = trecho[: trecho.index(").start()")]

    assert "cert_serial" in trecho


def test_r_pywinauto_e_import_opcional():
    """RUNTIME_DEPENDENCY: ausente, o fallback simplesmente não acontece."""
    import pathlib

    fonte = pathlib.Path(login_rf.__file__).read_text(encoding="utf-8")

    assert "_CERT_DIALOG_OK = False" in fonte


# ── Diagnostico persistente ───────────────────────────────────────────────────

def test_v_o_login_grava_screenshot_da_pagina_autenticada_em_falha():
    """SENSITIVE_PERSISTENT_DIAGNOSTIC: `_debug_pos_cert.png` fica no diretório do
    projeto, é uma captura da sessão do cliente, e nunca é removida."""
    import pathlib

    fonte = pathlib.Path(login_rf.__file__).read_text(encoding="utf-8")

    assert '_debug_pos_cert.png' in fonte
    assert "full_page=True" in fonte
    assert "unlink" not in fonte and "os.remove" not in fonte
