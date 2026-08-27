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

def test_a_o_CN_do_certificado_sai_no_stdout(login, monkeypatch, tmp_path,
                                             capsys):
    """A primeira linha do modo Windows Store. O CN carrega razao social e
    CNPJ: e o identificador do cliente, e ele sai a cada execucao — mesmo
    quando nada da errado."""
    rodar(login, monkeypatch, PaginaFalsa(), tmp_path)

    saida = capsys.readouterr().out

    assert CN_SENTINELA in saida
    assert saida.count(CN_SENTINELA) == 1


def test_a_e_ele_NAO_esta_no_log_persistente(login, monkeypatch, tmp_path):
    """Regressao da 13B.1: e console, e nao disco. O finding e outro, e este
    aqui nao pode reabrir aquele."""
    rodar(login, monkeypatch, PaginaFalsa(), tmp_path)

    assert CN_SENTINELA not in log_do_dia(tmp_path)


# ── §5 B · a mensagem bruta do navegador nos popups ──────────────────────────

def test_b_a_excecao_dos_popups_sai_INTEIRA_no_stdout(login, monkeypatch,
                                                      tmp_path, capsys):
    """O `except` que embrulha `_fechar_popups_iniciais` imprime
    `{type(e).__name__}: {e}` — e o `{e}` de um erro de navegador traz endereco,
    seletor e o que mais estiver na call log."""
    def explodir(page):
        raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)

    monkeypatch.setattr(login, "_fechar_popups_iniciais", explodir)

    rodar(login, monkeypatch, PaginaFalsa(), tmp_path)

    saida = capsys.readouterr().out

    assert sentinelas_do_navegador_em(saida) == {
        URL_SENTINELA, SELETOR_SENTINELA, TOKEN_SENTINELA
    }
    assert "ErroDoNavegador" in saida


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


def test_c_o_CNPJ_sai_DUAS_vezes_no_stdout(login, monkeypatch, capsys):
    """Uma ao anunciar a representacao e outra ao preencher o campo. Nenhuma das
    duas passa por `registrar_erro`: e stdout puro."""
    pagina = PaginaDaRepresentacao()

    assert login._representar_cnpj_procurador(pagina, CNPJ_SENTINELA) is True

    saida = capsys.readouterr().out

    assert saida.count(CNPJ_SENTINELA) == 2
    assert pagina.preenchidos == [CNPJ_SENTINELA], "e o valor CHEGA ao portal"


def test_c_e_o_CNPJ_sai_pelo_caminho_vivo_de_main(login, monkeypatch, tmp_path,
                                                  capsys):
    """Nao e so o auxiliar: `main(cnpj=...)` o alcanca."""
    rodar(login, monkeypatch, PaginaDaRepresentacao(), tmp_path,
          cnpj=CNPJ_SENTINELA)

    assert CNPJ_SENTINELA in capsys.readouterr().out


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


def test_10_ha_SETE_prints_com_a_mensagem_crua_do_navegador():
    """O inventario da 13B.2 procurou dentro de `main()`, e por isso enxergou um
    so. Estes outros vivem nos auxiliares — e os auxiliares sao chamados por
    `main()` no caminho vivo.

    Um deles e do modo `.pfx` (senhas.json) e esta fora do caminho promovido.
    """
    linhas = _linhas_com_excecao_crua()

    assert len(linhas) == 7
    assert len([linha for linha in linhas if "senhas.json" in linha]) == 1
    assert len([linha for linha in linhas if "popups iniciais" in linha]) == 1


@pytest.mark.parametrize("marca", [
    "[popup] Falha ao clicar em",
    "[bloqueado] go_back falhou",
    "[bloqueado] Botão 'Entrar com gov.br' não encontrado",
    "[cnpj] Erro na tentativa",
])
def test_10_e_os_dos_AUXILIARES_estao_fora_da_autorizacao(marca):
    """REPORTADOS, e nao alterados. A autorizacao da 13B.3 nomeia tres call
    sites: o CN, os popups de `main()` e os CNPJs da representacao. Nenhum
    destes e um deles."""
    assert any(marca in linha for linha in _linhas_com_excecao_crua())


def test_10_o_do_captcha_tambem(monkeypatch, capsys):
    """`_try_solve_captcha` imprime a excecao do solver inteira. Fora da
    autorizacao, e com um agravante proprio: quem levanta ali e o resolvedor,
    que fala com um servico externo autenticado.

    Sem a fixture: ela substitui justamente esta funcao.
    """
    from servicos_rf_login import login as modulo

    def solver_que_falha(page, api_key=None):
        raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)

    monkeypatch.setattr(modulo, "solve_hcaptcha", solver_que_falha)

    assert modulo._try_solve_captcha(PaginaFalsa(), "etapa-ficticia",
                                     max_attempts=1) is False

    assert sentinelas_do_navegador_em(capsys.readouterr().out) != set()


def test_10_o_do_go_back_tambem(monkeypatch, capsys):
    """Behavioral, e nao so de texto: o `except` do `go_back` realmente
    imprime a mensagem do navegador."""
    from servicos_rf_login import login as modulo

    class PaginaQueNaoVolta(PaginaFalsa):
        def go_back(self, *a, **k):
            raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)

    monkeypatch.setattr(modulo, "_try_solve_captcha", lambda *a, **k: True)

    modulo._recuperar_acesso_bloqueado(PaginaQueNaoVolta())

    assert sentinelas_do_navegador_em(capsys.readouterr().out) != set()


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
