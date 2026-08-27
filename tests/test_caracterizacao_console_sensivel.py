"""O que o fork de login IMPRIME — SENSITIVE_CONSOLE_OUTPUT.

Escrito ANTES de qualquer mudanca da fatia 13B.3 e commitado antes dela.

A 13B.1 e a 13B.2 fecharam o que o fork PERSISTE: sairam os screenshots, a URL
autenticada, a mensagem bruta do navegador e o CNPJ do log diario. O console
nunca foi tocado — e e por ali que continuam saindo, a cada execucao:

    A. o CN do certificado, que identifica a empresa;
    B. a mensagem bruta do navegador no tratamento de popups;
    C. o CNPJ do cliente, duas vezes, na representacao.

Nenhum teste abre navegador, contata o portal, chama o Gemini ou usa
certificado real. Todas as sentinelas sao ficticias e existem so para serem
procuradas no console.
"""
from pathlib import Path

import pytest
from test_caracterizacao_diagnostico_login import (
    PaginaFalsa,
    PlaywrightFalso,
    log_do_dia,
)

RAIZ = Path(__file__).resolve().parent.parent
LOGIN = RAIZ / "servicos_rf_login" / "login.py"

CN_SENTINELA = "EMPRESA-SENTINELA-CN"
CNPJ_SENTINELA = "99999999000199"
URL_SENTINELA = "https://portal.invalid/SENTINELA-URL"
SELETOR_SENTINELA = "SELETOR-SENTINELA"
TOKEN_SENTINELA = "TOKEN-SENTINELA"

# A mensagem que um erro de navegador realmente traz: endereco, seletor e, com
# frequencia, um pedaco do estado da sessao.
MENSAGEM_DO_NAVEGADOR = (
    f"Timeout 30000ms exceeded.\nCall log:\n  - navigating to "
    f'"{URL_SENTINELA}?token={TOKEN_SENTINELA}", waiting for '
    f"\"xpath=//*[@id='{SELETOR_SENTINELA}']\""
)


class ErroDoNavegador(Exception):
    """Um `patchright.Error` de mentira: o que importa e a mensagem."""


def sentinelas_do_navegador_em(texto):
    return {marca for marca in (URL_SENTINELA, SELETOR_SENTINELA, TOKEN_SENTINELA)
            if marca in texto}


@pytest.fixture
def login(monkeypatch, tmp_path):
    from servicos_rf_login import login as modulo

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(modulo.time, "sleep", lambda _s: None)
    monkeypatch.setattr(modulo, "_acesso_bloqueado", lambda page: False)
    # O duble nao tem `.count()`, e sem isto o fork ficaria no laco de espera
    # ate desistir. O que estes testes observam e o console, e nao a deteccao.
    monkeypatch.setattr(modulo, "_ja_logado", lambda page: True)
    monkeypatch.setattr(modulo, "_try_solve_captcha",
                        lambda *a, **k: True, raising=False)
    monkeypatch.setattr(modulo, "_clicar_certificado", lambda page: True)
    monkeypatch.setattr(modulo, "fechar_tutorial_pos_login",
                        lambda page, **k: None)
    return modulo


def rodar(modulo, monkeypatch, pagina, tmp_path, **extra):
    """`main()` no modo Windows Store — o caminho VIVO da promocao."""
    monkeypatch.setattr(modulo, "sync_playwright", lambda: PlaywrightFalso(pagina))
    return modulo.main(
        project_dir=tmp_path,
        cert_subject_cn=CN_SENTINELA,
        policy_ok=True,
        gemini_api_key="chave-ficticia",
        **extra,
    )


# ── §3 A · o CN do certificado ───────────────────────────────────────────────

def test_a_o_CN_do_certificado_NAO_sai_mais_no_stdout(login, monkeypatch,
                                                      tmp_path, capsys):
    """ANTES a primeira linha do modo Windows Store era `... CN: {cn}`, e o CN
    carrega razao social e CNPJ: saia a cada execucao, mesmo quando nada dava
    errado.

    §3: mensagem constante. Nem parcial, nem hash, nem iniciais, nem serial.
    """
    rodar(login, monkeypatch, PaginaFalsa(), tmp_path)

    saida = capsys.readouterr().out

    assert CN_SENTINELA not in saida
    assert "Certificado do Windows Store" in saida, "a etapa fica visivel"


def test_a_e_o_CN_continua_chegando_a_QUEM_PRECISA(login, monkeypatch,
                                                   tmp_path):
    """§8. O que saiu foi a APRESENTACAO. A variavel de ambiente e a flag de
    auto-selecao do Chrome continuam recebendo o CN — sem isso o certificado
    nao seria escolhido."""
    import os

    rodar(login, monkeypatch, PaginaFalsa(), tmp_path)

    assert os.environ["CERT_SUBJECT_CN"] == CN_SENTINELA


def test_a_e_ele_NAO_esta_no_log_persistente(login, monkeypatch, tmp_path):
    """Regressao da 13B.1: e console, e nao disco. O finding e outro, e este
    aqui nao pode reabrir aquele."""
    rodar(login, monkeypatch, PaginaFalsa(), tmp_path)

    assert CN_SENTINELA not in log_do_dia(tmp_path)


# ── §5 B · a mensagem bruta do navegador nos popups ──────────────────────────

def test_b_a_excecao_dos_popups_NAO_sai_mais_INTEIRA(login, monkeypatch,
                                                     tmp_path, capsys):
    """ANTES o `except` que embrulha `_fechar_popups_iniciais` imprimia
    `{type(e).__name__}: {e}`, e o `{e}` de um erro de navegador traz endereco,
    seletor e o que mais estiver na call log.

    §5: a classe fica, a mensagem sai. §6: nao foi trocada por `repr`, por
    `e.args` nem por traceback — a sanitizacao acontece ANTES da apresentacao.
    """
    def explodir(page):
        raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)

    monkeypatch.setattr(login, "_fechar_popups_iniciais", explodir)

    rodar(login, monkeypatch, PaginaFalsa(), tmp_path)

    saida = capsys.readouterr().out

    assert sentinelas_do_navegador_em(saida) == set()
    assert "ErroDoNavegador" in saida, "a classe fica: ela diz O QUE aconteceu"
    assert "Timeout 30000ms" not in saida, "e nada da mensagem"


def test_b_e_o_login_continua_apesar_da_falha(login, monkeypatch, tmp_path):
    """§8: a falha de popup e ignorada de proposito. Isso nao pode mudar."""
    def explodir(page):
        raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)

    monkeypatch.setattr(login, "_fechar_popups_iniciais", explodir)

    resultado = rodar(login, monkeypatch, PaginaFalsa(), tmp_path)

    assert resultado is not None
    assert len(resultado) == 3, "(playwright, context, page)"


def test_b_e_nada_disso_chega_ao_disco(login, monkeypatch, tmp_path):
    """Regressao: o ramo dos popups nunca chamou `registrar_erro`."""
    def explodir(page):
        raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)

    monkeypatch.setattr(login, "_fechar_popups_iniciais", explodir)

    rodar(login, monkeypatch, PaginaFalsa(), tmp_path)

    assert sentinelas_do_navegador_em(log_do_dia(tmp_path)) == set()


# ── §4 C · o CNPJ do cliente ─────────────────────────────────────────────────

class PaginaDaRepresentacao(PaginaFalsa):
    """O que `_representar_cnpj_procurador` toca, e nada alem."""

    def __init__(self, falhar=False):
        super().__init__()
        self.preenchidos = []
        self.falhar = falhar
        self.keyboard = self

    def press(self, *a, **k):
        return None

    def fill(self, valor):
        self.preenchidos.append(valor)

    def wait_for(self, *a, **k):
        if self.falhar:
            raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)

    def select_option(self, *a, **k):
        return None

    def get_by_role(self, *a, **k):
        return self


def test_c_o_CNPJ_NAO_sai_mais_no_stdout(login, monkeypatch, capsys):
    """ANTES saia DUAS vezes: ao anunciar a representacao e ao preencher o
    campo. Nenhuma das duas passava por `registrar_erro` — era stdout puro.

    §4: a etapa permanece; o valor, nao. Nem mascarado, nem parcial, nem hash.
    E §8: o valor continua CHEGANDO ao portal.
    """
    pagina = PaginaDaRepresentacao()

    assert login._representar_cnpj_procurador(pagina, CNPJ_SENTINELA) is True

    saida = capsys.readouterr().out

    assert CNPJ_SENTINELA not in saida
    assert "Iniciando representação do CNPJ" in saida
    assert "Preenchendo CNPJ" in saida
    assert pagina.preenchidos == [CNPJ_SENTINELA], "e o valor CHEGA ao portal"


def test_c_e_nenhum_pedaco_do_CNPJ_sobrou(login, monkeypatch, capsys):
    """§4 proibe mascaramento parcial. Nem os primeiros digitos, nem os
    ultimos, nem a forma pontuada."""
    login._representar_cnpj_procurador(PaginaDaRepresentacao(), CNPJ_SENTINELA)

    saida = capsys.readouterr().out

    for pedaco in (CNPJ_SENTINELA, CNPJ_SENTINELA[:6], CNPJ_SENTINELA[-4:],
                   "99.999.999/0001-99"):
        assert pedaco not in saida


def test_c_e_nem_pelo_caminho_vivo_de_main(login, monkeypatch, tmp_path,
                                           capsys):
    """Nao e so o auxiliar: `main(cnpj=...)` o alcanca — e por ali tambem nao
    sai mais."""
    rodar(login, monkeypatch, PaginaDaRepresentacao(), tmp_path,
          cnpj=CNPJ_SENTINELA)

    assert CNPJ_SENTINELA not in capsys.readouterr().out


def test_c_e_ele_NAO_esta_no_log_persistente(login, monkeypatch, tmp_path):
    """Regressao da 13B.2: o CNPJ saiu do log diario e nao pode voltar."""
    rodar(login, monkeypatch, PaginaDaRepresentacao(), tmp_path,
          cnpj=CNPJ_SENTINELA)

    assert CNPJ_SENTINELA not in log_do_dia(tmp_path)


# ── §10 · o resto do inventario focado, como esta hoje ───────────────────────

def _linhas_com_excecao_crua():
    """`print` VIVOS que interpolam `{e}` sem ser so a classe.

    Sem comentario: a prosa deste fork cita `{e}` para explicar o que foi
    retirado, e assertiva de texto batendo em prosa e o tropeco recorrente
    deste projeto.
    """
    fonte = LOGIN.read_text(encoding="utf-8")
    codigo = [linha.strip() for linha in fonte.splitlines()
              if not linha.lstrip().startswith("#")]
    return [linha for linha in codigo
            if linha.startswith("print(") and "{e}" in linha]


def test_10_sobrou_UM_print_com_a_mensagem_crua_do_navegador():
    """Eram SETE quando a 13B.3 comecou. Ela fechou o dos popups de `main()`,
    e sobraram seis — cinco em auxiliares vivos e um no modo `.pfx`.

    A 13B.4 fechou os cinco. O que resta e o do `senhas.json`, caracterizado e
    nao alterado por decisao explicita.
    """
    linhas = _linhas_com_excecao_crua()

    assert len(linhas) == 1
    assert "senhas.json" in linhas[0]


def test_10_os_dos_AUXILIARES_foram_fechados_pela_13B4():
    """ANTES este arquivo prendia CINCO marcadores como REPORTADOS e nao
    alterados — estavam fora da autorizacao da 13B.3:

        [popup] Falha ao clicar em ...
        [{etapa}] tentativa .../...
        [bloqueado] go_back falhou ...
        [bloqueado] Botão 'Entrar com gov.br' não encontrado ...
        [cnpj] Erro na tentativa .../3

    A 13B.4 os fechou. As provas de comportamento vivem em
    `test_caracterizacao_console_excecao_crua.py`; aqui fica so a contagem.
    """
    assert _linhas_com_excecao_crua() == [
        'print(f"[cert] Erro ao ler senhas.json: {e}")'
    ]


# ── §13 · §14 · o que ja estava fechado continua fechado ─────────────────────

def test_13_o_log_persistente_continua_sem_URL_e_sem_screenshot(login,
                                                                monkeypatch,
                                                                tmp_path):
    """SENSITIVE_PERSISTENT_DIAGNOSTIC. Nenhuma das correcoes desta fatia pode
    devolver nada disso ao disco."""
    pagina = PaginaFalsa(falhar_no_locator=True)

    rodar(login, monkeypatch, pagina, tmp_path)

    conteudo = log_do_dia(tmp_path)
    assert "portal.invalid" not in conteudo
    assert pagina.screenshots == [], "nenhum screenshot"
    assert list(tmp_path.glob("*.png")) == []


def test_14_nenhum_diagnostico_do_guardiao_em_disco(login, monkeypatch,
                                                    tmp_path):
    """`_guard_log.txt` e `_wincert_erro.log` sairam na 13B e nao voltam."""
    rodar(login, monkeypatch, PaginaFalsa(), tmp_path)

    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")

    assert "_guard_log" not in fonte
    assert "_wincert_erro" not in fonte
    assert list(tmp_path.rglob("_guard_log.txt")) == []
    assert list(tmp_path.rglob("_wincert_erro.log")) == []


# ── §7 · §10 · §11 · o inventario focado final ───────────────────────────────

def test_7_nenhuma_sentinela_em_stdout_nem_em_stderr(login, monkeypatch,
                                                     tmp_path, capsys):
    """A execucao inteira do caminho vivo, com tudo dando errado ao mesmo tempo:
    o CN, o CNPJ e uma excecao de navegador com endereco, seletor e token.

    Nenhuma das cinco sentinelas pode aparecer — nem no stdout, nem no stderr,
    nem no log diario.
    """
    def explodir(page):
        raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)

    monkeypatch.setattr(login, "_fechar_popups_iniciais", explodir)

    rodar(login, monkeypatch, PaginaDaRepresentacao(), tmp_path,
          cnpj=CNPJ_SENTINELA)

    capturado = capsys.readouterr()
    for lugar in (capturado.out, capturado.err, log_do_dia(tmp_path)):
        assert CN_SENTINELA not in lugar
        assert CNPJ_SENTINELA not in lugar
        assert sentinelas_do_navegador_em(lugar) == set()


def test_11_o_console_continua_contando_a_execucao(login, monkeypatch,
                                                   tmp_path, capsys):
    """§11: nao era preciso silenciar o fork para fechar o finding. As etapas
    constantes ficam — e sao elas que dizem a quem opera onde a execucao esta."""
    rodar(login, monkeypatch, PaginaDaRepresentacao(), tmp_path,
          cnpj=CNPJ_SENTINELA)

    saida = capsys.readouterr().out

    for etapa in ("[cert] Certificado do Windows Store configurado.",
                  "Clicando em 'Entrar com gov.br'",
                  "Representando CNPJ como Procurador",
                  "[cnpj] Preenchendo CNPJ",
                  "[cnpj] Representação enviada."):
        assert etapa in saida


def test_10_nenhum_print_vivo_interpola_identificador_de_cliente():
    """O inventario do §10, sobre o arquivo inteiro e nao so sobre `main()`.

    Procura o que identifica o cliente ou a sessao: CN, CNPJ, URL, titulo,
    serial e subject. A excecao crua tem o seu proprio teste, logo acima, e o
    seu proprio finding.
    """
    fonte = LOGIN.read_text(encoding="utf-8")
    codigo = [linha.strip() for linha in fonte.splitlines()
              if not linha.lstrip().startswith("#")]

    proibidos = ("{cert_subject_cn}", "{cnpj}", "page.url", "page.title",
                 "{serial}", "{subject}", "content()")
    suspeitos = [linha for linha in codigo
                 if linha.startswith(("print(", "registrar_erro("))
                 and any(marca in linha for marca in proibidos)]

    assert suspeitos == []


def test_6_a_mensagem_nao_voltou_por_outra_porta():
    """§6: nao trocamos um vazamento por outro. Nenhum `repr(e)`, `e.args`,
    `traceback` ou `logging.exception` entrou no lugar."""
    fonte = LOGIN.read_text(encoding="utf-8")
    codigo = chr(10).join(linha for linha in fonte.splitlines()
                          if not linha.lstrip().startswith("#"))

    for porta in ("repr(e)", "e.args", "traceback.", "logging.exception",
                  "format_exc"):
        assert porta not in codigo


def test_9_o_modo_PFX_continua_intocado():
    """LEGACY_INACTIVE_CONSOLE_DIAGNOSTIC. O `{e}` do `senhas.json` vive no modo
    `.pfx`, e o caminho promovido e o Windows Store: `main()` so chega la quando
    `cert_subject_cn` vem vazio.

    Nao alterado, e nao misturado com o finding do caminho vivo.
    """
    fonte = LOGIN.read_text(encoding="utf-8")

    assert 'print(f"[cert] Erro ao ler senhas.json: {e}")' in fonte
    assert "usar_windows_store = bool(cert_subject_cn and cert_subject_cn.strip())"         in fonte
    assert "if usar_windows_store:" in fonte


def test_8_a_semantica_dos_tres_call_sites_nao_mudou():
    """§8: so o conteudo sensivel virou mensagem segura. O `except` continua
    sendo o mesmo, o retorno continua sendo o mesmo, e o CNPJ continua sendo
    preenchido."""
    fonte = LOGIN.read_text(encoding="utf-8")

    assert "campo.fill(cnpj)" in fonte, "o valor continua indo ao portal"
    assert 'os.environ["CERT_SUBJECT_CN"] = cert_subject_cn' in fonte
    assert "_build_auto_select_cert_flag(cert_subject_cn)" in fonte
    assert "for tentativa in range(1, 4):" in fonte, "as tres tentativas ficam"
    assert "except Exception as e:" in fonte
