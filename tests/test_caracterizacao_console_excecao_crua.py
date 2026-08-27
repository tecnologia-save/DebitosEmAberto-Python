"""Os cinco `print` da mensagem CRUA que sobraram no caminho vivo.

Escrito ANTES de qualquer mudanca da fatia 13B.4 e commitado antes dela.

A 13B.3 fechou os identificadores de cliente — CN e CNPJ — e a mensagem do
navegador no tratamento de popups de `main()`. O inventario daquela fatia
olhava dentro de `main()`; estes cinco vivem nos AUXILIARES, e `main()` chama
todos eles.

    1. _clicar_popup                          o clique por JS que falhou
    2. _try_solve_captcha                     a excecao do SERVICO EXTERNO
    3. _recuperar_acesso_bloqueado  go_back   a volta que nao aconteceu
    4. _recuperar_acesso_bloqueado  gov.br    o botao que nao apareceu
    5. _representar_cnpj_procurador           o erro de cada tentativa

Um erro de navegador nao traz "detalhe tecnico": traz endereco, seletor e o que
mais estiver na call log. E o do captcha pode trazer a resposta do fornecedor.

Nenhum teste abre navegador, contata o portal, chama o Gemini, pede UAC ou usa
certificado real. Todas as sentinelas sao ficticias.
"""
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
LOGIN = RAIZ / "servicos_rf_login" / "login.py"

URL_SENTINELA = "https://portal.invalid/SENTINELA-URL"
SELETOR_SENTINELA = "SELETOR-SENTINELA"
TOKEN_SENTINELA = "TOKEN-SENTINELA"
RESPOSTA_SENTINELA = "RESPOSTA-EXTERNA-SENTINELA"
CAPTCHA_SENTINELA = "CAPTCHA-SERVICE-SENTINELA"

# O que um erro de navegador realmente carrega.
MENSAGEM_DO_NAVEGADOR = (
    f"Timeout 30000ms exceeded.\nCall log:\n  - navigating to "
    f'"{URL_SENTINELA}?token={TOKEN_SENTINELA}", waiting for '
    f"\"xpath=//*[@id='{SELETOR_SENTINELA}']\"\n"
    f"  - server said: {RESPOSTA_SENTINELA}"
)

# O que um erro de servico externo carrega: a resposta dele, inteira.
MENSAGEM_DO_SERVICO = (
    f"{CAPTCHA_SENTINELA}: POST {URL_SENTINELA}/solve -> 402 "
    f'{{"error": "{RESPOSTA_SENTINELA}", "key": "{TOKEN_SENTINELA}"}}'
)

TODAS = (URL_SENTINELA, SELETOR_SENTINELA, TOKEN_SENTINELA, RESPOSTA_SENTINELA,
         CAPTCHA_SENTINELA)


class ErroDoNavegador(Exception):
    """Um `patchright.Error` de mentira: o que importa e a mensagem."""


class ErroDoServicoExterno(Exception):
    """O que o resolvedor de captcha levanta quando o fornecedor recusa."""


def sentinelas_em(texto):
    return {marca for marca in TODAS if marca in texto}


@pytest.fixture
def login(monkeypatch):
    from servicos_rf_login import login as modulo

    monkeypatch.setattr(modulo.time, "sleep", lambda _s: None)
    return modulo


# ── 1 · _clicar_popup ────────────────────────────────────────────────────────

class PopupQueSoFalhaNoJS:
    """Visivel, o clique normal falha, e o clique por JS estoura."""

    def locator(self, _seletor):
        return self

    @property
    def first(self):
        return self

    def is_visible(self):
        return True

    def click(self, **_k):
        raise ErroDoNavegador("clique interceptado")

    def evaluate(self, _js):
        raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)


def test_1_clicar_popup_imprime_a_mensagem_do_navegador(login, capsys):
    """O fallback por JS falhou, e a mensagem sai inteira."""
    assert login._clicar_popup(PopupQueSoFalhaNoJS(), "aviso", "#qualquer") is False

    saida = capsys.readouterr().out

    assert sentinelas_em(saida) == {URL_SENTINELA, SELETOR_SENTINELA,
                                    TOKEN_SENTINELA, RESPOSTA_SENTINELA}
    assert "ErroDoNavegador" in saida


# ── 2 · _try_solve_captcha ───────────────────────────────────────────────────

class PaginaInerte:
    def locator(self, *_a, **_k):
        return self

    @property
    def first(self):
        return self

    def is_visible(self):
        return False

    def wait_for(self, *_a, **_k):
        return None

    def click(self, *_a, **_k):
        return None

    def goto(self, *_a, **_k):
        return None

    def wait_for_load_state(self, *_a, **_k):
        return None


def test_2_try_solve_captcha_imprime_a_RESPOSTA_DO_FORNECEDOR(login, monkeypatch,
                                                              capsys):
    """§4: a excecao aqui vem de integracao com servico externo. Ela pode
    carregar endereco, corpo da resposta e identificadores de autenticacao — e
    hoje sai tudo."""
    def recusar(page, api_key=None):
        raise ErroDoServicoExterno(MENSAGEM_DO_SERVICO)

    monkeypatch.setattr(login, "solve_hcaptcha", recusar)

    assert login._try_solve_captcha(PaginaInerte(), "etapa-ficticia",
                                    max_attempts=1) is False

    saida = capsys.readouterr().out

    assert sentinelas_em(saida) == {CAPTCHA_SENTINELA, URL_SENTINELA,
                                    RESPOSTA_SENTINELA, TOKEN_SENTINELA}


def test_2_e_o_numero_de_TENTATIVAS_e_o_que_e(login, monkeypatch, capsys):
    """§4: o que a correcao nao pode mexer."""
    chamadas = []

    def recusar(page, api_key=None):
        chamadas.append(api_key)
        raise ErroDoServicoExterno(MENSAGEM_DO_SERVICO)

    monkeypatch.setattr(login, "solve_hcaptcha", recusar)

    login._try_solve_captcha(PaginaInerte(), "etapa", max_attempts=3,
                             api_key="chave-ficticia")

    assert chamadas == ["chave-ficticia"] * 3


# ── 3 · 4 · _recuperar_acesso_bloqueado ──────────────────────────────────────

class PaginaQueNaoVolta(PaginaInerte):
    def go_back(self, **_k):
        raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)


class PaginaSemBotaoGovBr(PaginaInerte):
    def go_back(self, **_k):
        return None

    def wait_for(self, *_a, **_k):
        raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)


def test_3_o_go_back_que_falha_imprime_a_mensagem(login, monkeypatch, capsys):
    monkeypatch.setattr(login, "_try_solve_captcha", lambda *a, **k: True)

    login._recuperar_acesso_bloqueado(PaginaQueNaoVolta())

    saida = capsys.readouterr().out

    assert sentinelas_em(saida) == {URL_SENTINELA, SELETOR_SENTINELA,
                                    TOKEN_SENTINELA, RESPOSTA_SENTINELA}
    assert "go_back falhou" in saida


def test_4_o_botao_govbr_ausente_tambem(login, monkeypatch, capsys):
    monkeypatch.setattr(login, "_try_solve_captcha", lambda *a, **k: True)

    assert login._recuperar_acesso_bloqueado(PaginaSemBotaoGovBr()) is False

    saida = capsys.readouterr().out

    assert sentinelas_em(saida) == {URL_SENTINELA, SELETOR_SENTINELA,
                                    TOKEN_SENTINELA, RESPOSTA_SENTINELA}
    assert "não encontrado após go_back" in saida


# ── 5 · _representar_cnpj_procurador ─────────────────────────────────────────

CNPJ_SENTINELA = "99999999000199"


class PaginaQueNaoRepresenta(PaginaInerte):
    def __init__(self):
        self.keyboard = self

    def press(self, *_a, **_k):
        return None

    def wait_for(self, *_a, **_k):
        raise ErroDoNavegador(MENSAGEM_DO_NAVEGADOR)


def test_5_o_erro_de_cada_tentativa_imprime_a_mensagem(login, monkeypatch,
                                                       capsys):
    """Tres tentativas, tres mensagens inteiras."""
    monkeypatch.setattr(login, "fechar_tutorial_pos_login", lambda page, **k: None)

    assert login._representar_cnpj_procurador(PaginaQueNaoRepresenta(),
                                              CNPJ_SENTINELA) is False

    saida = capsys.readouterr().out

    assert sentinelas_em(saida) == {URL_SENTINELA, SELETOR_SENTINELA,
                                    TOKEN_SENTINELA, RESPOSTA_SENTINELA}
    assert saida.count("Erro na tentativa") == 3


def test_5_e_o_CNPJ_ja_NAO_sai_junto(login, monkeypatch, capsys):
    """Regressao da 13B.3: os dois vazamentos convivem no mesmo auxiliar, e o
    primeiro ja foi fechado. Nenhum dos dois pode voltar."""
    monkeypatch.setattr(login, "fechar_tutorial_pos_login", lambda page, **k: None)

    login._representar_cnpj_procurador(PaginaQueNaoRepresenta(), CNPJ_SENTINELA)

    assert CNPJ_SENTINELA not in capsys.readouterr().out


# ── §11 · o modo PFX: so REACHABILITY, sem tocar em nada ─────────────────────

def test_11_o_unico_chamador_de_fazer_login_sempre_manda_o_CN():
    """A fronteira e uma so, e ela nao tem parametro de `.pfx`. Nem
    `cert_pfx_path`, nem `cert_name`, nem passphrase."""
    fonte = (RAIZ / "automation" / "login.py").read_text(encoding="utf-8")
    chamada = fonte[fonte.index("recursos = fazer_login("):]
    chamada = chamada[: chamada.index(")")]

    assert "cert_subject_cn=certificado.subject_cn" in chamada
    for parametro in ("cert_pfx_path", "cert_pfx_passphrase", "cert_name"):
        assert parametro not in chamada


def test_11_e_nenhuma_entrada_chama_fazer_login_por_fora():
    """`runner.py`, `local.py` e `main.py` chegam ao login por
    `automation.app.executar`. Nenhum deles chama o fork diretamente."""
    for nome in ("runner.py", "local.py", "main.py"):
        fonte = (RAIZ / nome).read_text(encoding="utf-8")
        chamadas = [linha.strip() for linha in fonte.splitlines()
                    if "fazer_login(" in linha and not linha.lstrip().startswith("#")]
        assert chamadas == [], (nome, chamadas)


def test_11_e_o_CN_nunca_chega_vazio_porque_o_indice_e_FILTRADO():
    """O elo que sustenta a resposta.

    `usar_windows_store = bool(cert_subject_cn and cert_subject_cn.strip())` — um
    CN vazio cairia no ramo `.pfx`. Quem impede isso e `indexar`, que so admite
    certificado com CN em forma ICP-Brasil: `NOME:CPF` ou `NOME:CNPJ`.

    Nao ha guarda em `ConfigLogin.validar` nem em `automation/login.py`. E o
    filtro, e so ele.
    """
    from automation import certificados_windows

    mapa, ignorados = certificados_windows.indexar([
        {"subject_cn": "", "display": "SEM CN"},
        {"subject_cn": "SO NOME SEM DOCUMENTO", "display": "SEM DOCUMENTO"},
        {"subject_cn": "ALFA FICTICIA LTDA:11111111000191", "display": "ALFA"},
    ])

    assert ignorados == 2
    assert all(dado["subject_cn"] for dado in mapa.values())


def test_11_e_o_senhas_json_continua_INTOCADO():
    """§11 e §12: nesta fatia o modo `.pfx` so e caracterizado. O `{e}` dele
    continua onde estava."""
    fonte = LOGIN.read_text(encoding="utf-8")

    assert 'print(f"[cert] Erro ao ler senhas.json: {e}")' in fonte
    assert "_resolver_certificado(" in fonte


# ── §14 · as seams de elevacao nos testes ────────────────────────────────────

def test_14_nenhum_teste_depende_da_elevacao_REAL():
    """A auditoria do §14, e a guarda que a torna irrelevante.

    Qualquer teste que chegue ao `ShellExecuteExW` de verdade falha — a guarda
    autouse de `conftest.py` o substitui em TODOS. O inventario abaixo existe
    para que a lista de quem mexe com elevacao continue visivel.
    """
    testes = sorted(
        caminho.name for caminho in (RAIZ / "tests").glob("test_*.py")
        if any(marca in caminho.read_text(encoding="utf-8")
               for marca in ("_runas", "_elevar", "ShellExecuteExW"))
    )

    assert testes == ["test_caracterizacao_console_excecao_crua.py",
                      "test_caracterizacao_guardiao.py",
                      "test_caracterizacao_guardiao_liveness.py",
                      "test_caracterizacao_host_release.py",
                      "test_caracterizacao_policy.py",
                      "test_guarda_de_elevacao.py"]


def test_14_e_a_guarda_esta_ATIVA_neste_teste_tambem():
    """Abaixo da fronteira Win32, e sem provocar UAC: o que o modulo tem no
    lugar do `ShellExecuteExW` durante a suite nao e a funcao do sistema."""
    import cert_windows

    assert cert_windows._shell32.ShellExecuteExW.__name__ == "recusar"
