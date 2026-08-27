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


def test_2_o_erro_de_goto_NAO_persiste_mais_a_mensagem(login, monkeypatch,
                                                       tmp_path):
    """ANTES a mensagem do Playwright entrava INTEIRA no log diario, com o
    endereco, o seletor e o que mais estivesse na call log.

    AGORA fica so a classe da excecao: ela diz O QUE aconteceu sem dizer onde
    nem com quem.
    """
    rodar(login, monkeypatch, PaginaQueFalhaNoGoto(), tmp_path)

    conteudo = log_do_dia(tmp_path)

    assert sentinelas_em(conteudo) == set()
    assert "ErroDoNavegador" in conteudo, "a classe fica"
    assert "Login: erro ao abrir URL" in conteudo


def test_2_e_nem_para_o_console(login, monkeypatch, tmp_path, capsys):
    """`registrar_erro` imprime o que grava, entao o mesmo conteudo saia duas
    vezes. Agora nenhuma das duas carrega a mensagem."""
    rodar(login, monkeypatch, PaginaQueFalhaNoGoto(), tmp_path)

    saida = capsys.readouterr().out

    assert sentinelas_em(saida) == set()
    assert "erro no goto: ErroDoNavegador" in saida


def test_2_o_retorno_da_falha_e_None(login, monkeypatch, tmp_path):
    """§6: a semantica que a correcao nao pode mexer."""
    assert rodar(login, monkeypatch, PaginaQueFalhaNoGoto(), tmp_path) is None


# ── §3 · a excecao bruta do locator ──────────────────────────────────────────

class PaginaQueFalhaNoLocator(PaginaFalsa):
    def wait_for(self, *a, **k):
        raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)


def test_3_o_timeout_do_locator_NAO_persiste_mais_a_mensagem(
    login, monkeypatch, tmp_path
):
    """Mesma regra do goto, e pela mesma razao: a mensagem de timeout do
    Playwright traz o seletor e costuma trazer a URL da pagina."""
    monkeypatch.setattr(login, "_ja_logado", lambda page: False)

    rodar(login, monkeypatch, PaginaQueFalhaNoLocator(), tmp_path)

    conteudo = log_do_dia(tmp_path)

    assert sentinelas_em(conteudo) == set()
    assert "ErroDoNavegador" in conteudo


def test_3_e_nem_para_o_console(login, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(login, "_ja_logado", lambda page: False)

    rodar(login, monkeypatch, PaginaQueFalhaNoLocator(), tmp_path)

    assert sentinelas_em(capsys.readouterr().out) == set()


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


def test_4_o_cnpj_do_cliente_NAO_vai_mais_para_o_log(representacao_falha,
                                                     monkeypatch, tmp_path):
    """ANTES: documento do cliente num arquivo que acumula e nunca e removido.

    Nao foi mascarado, nem truncado, nem transformado em hash: saiu. O projeto
    ja decidiu que identificador de cliente nao pertence a diagnostico
    persistente automatico.
    """
    rodar(representacao_falha, monkeypatch, PaginaFalsa(), tmp_path,
          cnpj=CNPJ_SENTINELA)

    conteudo = log_do_dia(tmp_path)

    assert CNPJ_SENTINELA not in conteudo
    assert CNPJ_SENTINELA[:6] not in conteudo, "nem um pedaco"
    assert CNPJ_SENTINELA[-4:] not in conteudo
    assert "Login: falha ao representar CNPJ." in conteudo, "a etapa fica"


def test_4_e_nenhuma_vez_no_console_pelo_bloco_de_main(representacao_falha,
                                                       monkeypatch, tmp_path,
                                                       capsys):
    """ANTES saia duas vezes pelo bloco de `main` — no anuncio da etapa e na
    linha da falha — alem do eco do proprio `registrar_erro`.

    O ESCOPO desta prova: o bloco de `main`. A funcao
    `_representar_cnpj_procurador` esta substituida por duble aqui, e ela tem os
    seus proprios `print` com o CNPJ — reportados, fora da autorizacao, e
    provados em `test_7_o_cnpj_ainda_sai_no_stdout_do_helper`.
    """
    rodar(representacao_falha, monkeypatch, PaginaFalsa(), tmp_path,
          cnpj=CNPJ_SENTINELA)

    saida = capsys.readouterr().out

    assert CNPJ_SENTINELA not in saida
    assert "Representando CNPJ como Procurador" in saida, "a etapa fica visivel"


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

def test_7_nenhum_registrar_erro_carrega_mais_dado_sensivel():
    """ANTES tres das oito chamadas carregavam dado dinamico: duas com a
    mensagem do navegador, uma com o CNPJ.

    AGORA as unicas duas que ainda interpolam algo interpolam a CLASSE da
    excecao — e mais nada.
    """
    fonte = LOGIN.read_text(encoding="utf-8")

    # A busca e sobre o que PERSISTE. Fora dela sobram `print` reportados —
    # ver `test_7_o_inventario_focado_final`.
    persistidas = [linha for linha in fonte.splitlines()
                   if "registrar_erro(" in linha]
    for linha in persistidas:
        assert "{cnpj}" not in linha
        if "{e}" in linha:
            assert "{type(e).__name__}." in linha, linha

    dinamicas = [linha.strip() for linha in fonte.splitlines()
                 if 'registrar_erro(f"' in linha]

    assert len(dinamicas) == 2
    for linha in dinamicas:
        assert "{type(e).__name__}." in linha
        assert "{e}" not in linha.replace("{type(e).__name__}", "")


def test_7_o_inventario_focado_final(login):
    """§7: `print` e `registrar_erro` vivos em `main()`, procurando dado
    dinamico proveniente de URL, titulo, excecao, CNPJ, CN, serial ou caminho.

    Sobram DOIS, e os dois estao FORA da autorizacao desta fatia — reportados,
    e nao alterados:

        print(... CN: {cert_subject_cn})          o CN do certificado, em stdout
        print(... popups ...: {type(e).__name__}: {e})   mensagem do navegador

    Nenhum dos dois e persistido pelo fork: sao stdout. Continuam registrados.
    """
    fonte = LOGIN.read_text(encoding="utf-8")
    corpo = fonte[fonte.index("def main("):]
    corpo = "\n".join(linha for linha in corpo.splitlines()
                      if not linha.lstrip().startswith("#"))

    suspeitos = []
    for linha in corpo.splitlines():
        limpa = linha.strip()
        if not (limpa.startswith("print(") or limpa.startswith("registrar_erro(")):
            continue
        if any(m in limpa for m in ("{e}", "{cnpj}", "page.url", "page.title",
                                    "repr(", ".args", "cert_serial")):
            suspeitos.append(limpa)
        elif "cert_subject_cn" in limpa:
            suspeitos.append(limpa)

    assert len(suspeitos) == 2, suspeitos
    assert any("cert_subject_cn" in s for s in suspeitos)
    assert any("popups" in s for s in suspeitos)
    assert not any("registrar_erro" in s for s in suspeitos), \
        "nenhum dos dois PERSISTE"


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


def test_7_o_cnpj_ainda_sai_no_stdout_do_helper():
    """REPORTADO, fora da autorizacao desta fatia.

    `_representar_cnpj_procurador` imprime o CNPJ duas vezes — ao iniciar e ao
    preencher o campo. E stdout, e nao persistencia: nenhuma das duas passa por
    `registrar_erro`.

    A autorizacao da 13B.2 nomeia tres call sites, e nenhum deles e este.
    """
    fonte = LOGIN.read_text(encoding="utf-8")
    helper = fonte[fonte.index("def _representar_cnpj_procurador"):]
    helper = helper[: helper.index("\ndef ")]

    com_cnpj = [linha.strip() for linha in helper.splitlines()
                if linha.strip().startswith("print(") and "{cnpj}" in linha]

    assert len(com_cnpj) == 2
    assert "registrar_erro" not in helper, "nada dali e persistido"


def test_7_e_o_CN_do_certificado_tambem(login):
    """REPORTADO, fora da autorizacao. O CN identifica a empresa, e sai no
    stdout logo no comeco de `main`. Tambem nao e persistido pelo fork."""
    fonte = LOGIN.read_text(encoding="utf-8")

    assert 'print(f"[cert] Certificado do Windows Store. CN: {cert_subject_cn}")' \
        in fonte
