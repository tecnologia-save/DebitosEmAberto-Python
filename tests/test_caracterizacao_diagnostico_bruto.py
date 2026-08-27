"""Os tres vazamentos que o inventario focado da 13B.1 encontrou.

Escrito ANTES de qualquer mudanca da fatia 13B.2.

Nenhum teste abre navegador, contata o portal, chama o Gemini ou usa
certificado real. Todas as sentinelas sao ficticias e existem so para serem
procuradas em disco e no console.
"""
from pathlib import Path

import pytest
from test_caracterizacao_diagnostico_login import (
    ContextoFalso,
    PaginaFalsa,
    PlaywrightFalso,
    log_do_dia,
)

RAIZ = Path(__file__).resolve().parent.parent
LOGIN = RAIZ / "servicos_rf_login" / "login.py"

URL_SENTINELA = "https://portal.invalid/SENTINELA-URL"
SELETOR_SENTINELA = "xpath=//*[@id='SELETOR-SENTINELA']"
TOKEN_SENTINELA = "TOKEN-SENTINELA"
CNPJ_SENTINELA = "99999999000199"

# A mensagem que um erro de navegador realmente traz: endereco, seletor e, com
# frequencia, um pedaco do estado da sessao.
MENSAGEM_DO_NAVEGADOR = (
    f"Timeout 30000ms exceeded.\\nCall log:\\n  - navigating to "
    f'"{URL_SENTINELA}?token={TOKEN_SENTINELA}", waiting for '
    f'"{SELETOR_SENTINELA}"'
)


class ErroDoNavegador(Exception):
    """Um `patchright.Error` de mentira: o que importa e a mensagem."""


@pytest.fixture
def login(monkeypatch, tmp_path):
    from servicos_rf_login import login as modulo

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(modulo.time, "sleep", lambda _s: None)
    monkeypatch.setattr(modulo, "_acesso_bloqueado", lambda page: False)
    monkeypatch.setattr(modulo, "_try_solve_captcha",
                        lambda *a, **k: True, raising=False)
    monkeypatch.setattr(modulo, "_clicar_certificado", lambda page: True)
    monkeypatch.setattr(modulo, "fechar_tutorial_pos_login", lambda page: None)
    return modulo


def rodar(modulo, monkeypatch, pagina, tmp_path, **extra):
    monkeypatch.setattr(modulo, "sync_playwright", lambda: PlaywrightFalso(pagina))
    return modulo.main(
        project_dir=tmp_path,
        cert_subject_cn="ALFA FICTICIA LTDA:11111111000191",
        policy_ok=True,
        gemini_api_key="chave-ficticia",
        **extra,
    )


def sentinelas_em(texto):
    return {
        marca for marca in (URL_SENTINELA, "SELETOR-SENTINELA", TOKEN_SENTINELA)
        if marca in texto
    }


# ── §2 · a excecao bruta do `goto` ───────────────────────────────────────────

class PaginaQueFalhaNoGoto(PaginaFalsa):
    def goto(self, *a, **k):
        raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)


def test_2_o_erro_de_goto_persiste_a_mensagem_do_navegador(login, monkeypatch,
                                                           tmp_path):
    """A mensagem do Playwright entra INTEIRA no log diario — e ela traz o
    endereco, o seletor e o que mais estiver na call log."""
    rodar(login, monkeypatch, PaginaQueFalhaNoGoto(), tmp_path)

    conteudo = log_do_dia(tmp_path)

    assert sentinelas_em(conteudo) == {URL_SENTINELA, "SELETOR-SENTINELA",
                                       TOKEN_SENTINELA}


def test_2_e_tambem_para_o_console(login, monkeypatch, tmp_path, capsys):
    """`registrar_erro` imprime o que grava, entao o mesmo conteudo sai duas
    vezes: uma no arquivo, outra no console."""
    rodar(login, monkeypatch, PaginaQueFalhaNoGoto(), tmp_path)

    assert sentinelas_em(capsys.readouterr().out)


def test_2_o_retorno_da_falha_e_None(login, monkeypatch, tmp_path):
    """§6: a semantica que a correcao nao pode mexer."""
    assert rodar(login, monkeypatch, PaginaQueFalhaNoGoto(), tmp_path) is None


# ── §3 · a excecao bruta do locator ──────────────────────────────────────────

class PaginaQueFalhaNoLocator(PaginaFalsa):
    def wait_for(self, *a, **k):
        raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)


def test_3_o_timeout_do_locator_persiste_a_mensagem_do_navegador(
    login, monkeypatch, tmp_path
):
    monkeypatch.setattr(login, "_ja_logado", lambda page: False)

    rodar(login, monkeypatch, PaginaQueFalhaNoLocator(), tmp_path)

    conteudo = log_do_dia(tmp_path)

    assert sentinelas_em(conteudo) == {URL_SENTINELA, "SELETOR-SENTINELA",
                                       TOKEN_SENTINELA}


def test_3_e_tambem_para_o_console(login, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(login, "_ja_logado", lambda page: False)

    rodar(login, monkeypatch, PaginaQueFalhaNoLocator(), tmp_path)

    assert sentinelas_em(capsys.readouterr().out)


def test_3_o_retorno_da_falha_e_None(login, monkeypatch, tmp_path):
    monkeypatch.setattr(login, "_ja_logado", lambda page: False)

    assert rodar(login, monkeypatch, PaginaQueFalhaNoLocator(), tmp_path) is None


# ── §4 · o CNPJ do cliente ───────────────────────────────────────────────────

@pytest.fixture
def representacao_falha(login, monkeypatch):
    """Login que chega ao fim e falha SO na representacao do CNPJ."""
    monkeypatch.setattr(login, "_ja_logado", lambda page: True)
    monkeypatch.setattr(login, "_representar_cnpj_procurador",
                        lambda page, cnpj: False)
    return login


def test_4_o_cnpj_do_cliente_vai_para_o_log_persistente(representacao_falha,
                                                        monkeypatch, tmp_path):
    """O achado mais grave do inventario: documento do cliente num arquivo que
    acumula e nunca e removido."""
    rodar(representacao_falha, monkeypatch, PaginaFalsa(), tmp_path,
          cnpj=CNPJ_SENTINELA)

    assert CNPJ_SENTINELA in log_do_dia(tmp_path)


def test_4_e_para_o_console_duas_vezes(representacao_falha, monkeypatch,
                                       tmp_path, capsys):
    """Uma no anuncio da etapa, outra na linha da falha — alem do eco do
    proprio `registrar_erro`."""
    rodar(representacao_falha, monkeypatch, PaginaFalsa(), tmp_path,
          cnpj=CNPJ_SENTINELA)

    saida = capsys.readouterr().out

    assert saida.count(CNPJ_SENTINELA) >= 2


def test_4_o_retorno_NAO_muda_com_a_falha_de_representacao(representacao_falha,
                                                           monkeypatch,
                                                           tmp_path):
    """§6: falhar a representacao nao aborta o login — devolve a pagina sem
    representacao. Isso nao pode mudar."""
    resultado = rodar(representacao_falha, monkeypatch, PaginaFalsa(), tmp_path,
                      cnpj=CNPJ_SENTINELA)

    assert resultado is not None
    assert len(resultado) == 3, "(playwright, context, page)"


# ── §7 · o inventario focado, como ele esta hoje ─────────────────────────────

def test_7_os_tres_call_sites_carregam_dado_dinamico():
    fonte = LOGIN.read_text(encoding="utf-8")

    assert 'registrar_erro(f"Login: erro ao abrir URL (1ª navegação). ' \
           '{type(e).__name__}: {e}")' in fonte
    assert "registrar_erro(f\"Login: botão 'Entrar com gov.br' não encontrado. " \
           '{type(e).__name__}: {e}")' in fonte
    assert 'registrar_erro(f"Login: falha ao representar CNPJ {cnpj}.")' in fonte


def test_7_e_nenhum_outro_registrar_erro_carrega(login):
    """As outras cinco chamadas sao constantes."""
    fonte = LOGIN.read_text(encoding="utf-8")

    dinamicas = [linha.strip() for linha in fonte.splitlines()
                 if 'registrar_erro(f"' in linha]

    assert len(dinamicas) == 3


# ── §9 · §10 · o que a 13B e a 13B.1 fecharam nao volta ──────────────────────

def test_9_os_tres_screenshots_continuam_removidos(login, monkeypatch, tmp_path):
    monkeypatch.setattr(login, "_ja_logado", lambda page: False)

    rodar(login, monkeypatch, PaginaQueFalhaNoLocator(), tmp_path)

    for nome in ("_debug_govbr_btn.png", "_debug_cert_button.png",
                 "_debug_pos_cert.png"):
        assert not (tmp_path / nome).exists()
    assert "screenshot" not in LOGIN.read_text(encoding="utf-8")


def test_10_os_diagnosticos_do_windows_continuam_removidos():
    import inspect

    import cert_windows

    fonte = inspect.getsource(cert_windows.guardiao)
    assert "_glog" not in fonte and "open(" not in fonte

    arquivo = Path(cert_windows.__file__).read_text(encoding="utf-8")
    assert "write_text" not in arquivo[arquivo.index('sys.argv[1] == "--guard"'):]


def test_contexto_falso_e_usado(login):
    """Guarda de sanidade do proprio duble: ele fecha o contexto."""
    assert hasattr(ContextoFalso(PaginaFalsa()), "close")
