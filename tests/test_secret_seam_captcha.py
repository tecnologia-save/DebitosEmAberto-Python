"""O seam do segredo: a chave do Gemini deixa de precisar do ambiente.

A alteração no fork tem 11 linhas e uma regra:

    api_key omitido (None)  -> comportamento antigo, busca no ambiente;
    api_key informado       -> usa exatamente o que veio, sem consultar ambiente.

A distinção entre "não informei" e "informei vazio" é o ponto. Um `or` teria
misturado as duas, e uma chave explicitamente vazia cairia num fallback
silencioso — exatamente o que este seam existe para impedir.

A chave dos testes é uma sentinela fictícia.
"""
import inspect

import pytest

import resolvedor_captcha
from resolvedor_captcha import solver

CHAVE_EXPLICITA = "AIzaSy-SENTINELA-EXPLICITA-0000"
CHAVE_DO_AMBIENTE = "AIzaSy-SENTINELA-DO-AMBIENTE-9999"


class PaginaSentinela:
    """Marca que o solver passou da validação da chave — nada além disso."""

    def __init__(self):
        self.tocada = False

    def __getattr__(self, nome):
        object.__setattr__(self, "tocada", True)
        raise _PassouDaValidacao(nome)


class _PassouDaValidacao(Exception):
    pass


def chave_usada(monkeypatch, **kwargs):
    """Roda solve_hcaptcha e devolve a chave que ele decidiu usar."""
    capturada = {}

    def espiao(api_key):
        capturada["chave"] = api_key
        raise _PassouDaValidacao("cheguei ao cliente")

    monkeypatch.setattr(solver, "_click_checkbox_widget", lambda *a, **k: None)
    monkeypatch.setattr(solver, "_detect_challenge_type", lambda *a, **k: "grade")
    monkeypatch.setattr(solver, "_solve_grade", lambda page, api_key, **k: espiao(api_key))

    with pytest.raises(_PassouDaValidacao):
        solver.solve_hcaptcha(PaginaSentinela(), **kwargs)
    return capturada["chave"]


# ── J · o caller legado continua funcionando ──────────────────────────────────

def test_j_caller_legado_sem_api_key_usa_o_ambiente(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE_DO_AMBIENTE)

    assert chave_usada(monkeypatch) == CHAVE_DO_AMBIENTE


def test_j_caller_legado_sem_chave_no_ambiente_continua_levantando(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        solver.solve_hcaptcha(PaginaSentinela())


def test_j_o_placeholder_continua_recusado_no_caminho_legado(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "cole-sua-chave-aqui")

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        solver.solve_hcaptcha(PaginaSentinela())


def test_j_a_assinatura_continua_aceitando_as_chamadas_antigas():
    parametros = inspect.signature(resolvedor_captcha.solve_hcaptcha).parameters

    assert list(parametros) == ["page", "max_rounds", "api_key"]
    assert parametros["max_rounds"].default == 6
    assert parametros["api_key"].default is None, "omitir = comportamento antigo"


# ── G · K · o caminho novo não passa pelo ambiente ────────────────────────────

def test_g_chave_explicita_e_usada_tal_qual(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE_DO_AMBIENTE)

    assert chave_usada(monkeypatch, api_key=CHAVE_EXPLICITA) == CHAVE_EXPLICITA


def test_g_chave_explicita_funciona_com_o_ambiente_vazio(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert chave_usada(monkeypatch, api_key=CHAVE_EXPLICITA) == CHAVE_EXPLICITA


def test_k_chave_explicitamente_vazia_nao_cai_no_ambiente(monkeypatch):
    """O ponto do seam. Com `or`, isto pegaria a chave do ambiente em silêncio."""
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE_DO_AMBIENTE)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        solver.solve_hcaptcha(PaginaSentinela(), api_key="")


def test_k_a_recusa_acontece_antes_de_tocar_a_pagina(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE_DO_AMBIENTE)
    pagina = PaginaSentinela()

    with pytest.raises(RuntimeError):
        solver.solve_hcaptcha(pagina, api_key="")

    assert pagina.tocada is False


def test_k_a_mensagem_de_recusa_nao_ecoa_chave_nenhuma(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE_DO_AMBIENTE)

    with pytest.raises(RuntimeError) as erro:
        solver.solve_hcaptcha(PaginaSentinela(), api_key="cole-" + CHAVE_EXPLICITA)

    assert "SENTINELA" not in str(erro.value)


# ── H · nada mais do solver mudou ─────────────────────────────────────────────

def test_h_a_alteracao_no_fork_e_so_o_parametro():
    """Nenhuma outra lógica do solver foi tocada — o resto continua idêntico."""
    import pathlib

    fonte = pathlib.Path(solver.__file__).read_text(encoding="utf-8")

    assert "def solve_hcaptcha(page, max_rounds: int = 6, api_key: str | None = None)" in fonte
    assert "if api_key is None:" in fonte
    assert "api_key = api_key or" not in fonte, "`or` misturaria omitido com vazio"
    assert fonte.count('os.environ.get("GEMINI_API_KEY", "")') == 1, "um fallback só"


# ── I · o mesmo seam no servicos_rf_login ─────────────────────────────────────

def test_i_o_login_repassa_a_chave_recebida(monkeypatch):
    """`fazer_login(gemini_api_key=...)` chega ao solver sem passar pelo ambiente."""
    from servicos_rf_login import login as login_rf

    recebidas = []
    monkeypatch.setattr(
        login_rf, "solve_hcaptcha",
        lambda page, api_key=None: recebidas.append(api_key) or True,
    )

    assert login_rf._try_solve_captcha("pagina", "etapa", api_key=CHAVE_EXPLICITA) is True
    assert recebidas == [CHAVE_EXPLICITA]


def test_i_o_login_sem_chave_mantem_o_comportamento_antigo(monkeypatch):
    from servicos_rf_login import login as login_rf

    recebidas = []
    monkeypatch.setattr(
        login_rf, "solve_hcaptcha",
        lambda page, api_key=None: recebidas.append(api_key) or True,
    )

    login_rf._try_solve_captcha("pagina", "etapa")

    assert recebidas == [None], "None = 'nao informei', e o solver busca no ambiente"


def test_i_os_tres_pontos_de_captcha_repassam_a_chave():
    """Nenhum dos três pontos ficou para trás — senão um deles cairia no ambiente."""
    import pathlib

    from servicos_rf_login import login as login_rf

    fonte = pathlib.Path(login_rf.__file__).read_text(encoding="utf-8")

    # Dois `_try_solve_captcha` diretos e dois `_recuperar_acesso_bloqueado`,
    # que por sua vez repassa ao terceiro ponto de captcha.
    assert fonte.count("api_key=gemini_api_key") == 4
    assert fonte.count('_try_solve_captcha(page, "captcha-pos-bloqueado", api_key=api_key)') == 1
    assert "solve_hcaptcha(page, api_key=api_key)" in fonte
    assert "_recuperar_acesso_bloqueado(page)" not in fonte, "nenhuma chamada ficou sem chave"


def test_i_a_alteracao_no_login_e_pequena():
    """18 inserções e 8 remoções em 869 linhas: um parâmetro e seu repasse."""
    import pathlib

    from servicos_rf_login import login as login_rf

    fonte = pathlib.Path(login_rf.__file__).read_text(encoding="utf-8")

    assert "gemini_api_key: str | None = None," in fonte
    assert "api_key: str | None = None" in fonte
    assert "os.environ.get(\"GEMINI_API_KEY\"" not in fonte, "o login nunca leu o ambiente"
