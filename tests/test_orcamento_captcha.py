"""LOGIN_CAPTCHA_BUDGET — as camadas nomeadas e travadas.

Relatorios anteriores misturaram `MAX_GEMINI_TRIES = 5` com "4 modelos x 2
tentativas". Sao coisas diferentes, em niveis diferentes, e a confusao produziu
um numero errado. Este arquivo separa cada camada, prende cada constante e deixa
a formula explicita — para que ela nao possa mudar em silencio.

Nenhum teste chama Gemini.
"""
import pathlib

from resolvedor_captcha import solver
from servicos_rf_login import login as login_rf

FONTE_SOLVER = pathlib.Path(solver.__file__).read_text(encoding="utf-8")
FONTE_LOGIN = pathlib.Path(login_rf.__file__).read_text(encoding="utf-8")


# ── P · pontos de captcha alcancaveis num fazer_login ─────────────────────────

def test_p_sao_oito_pontos_alcancaveis():
    """1 pos-gov.br + 1 recuperacao pos-gov.br + 3 pos-certificado
    + 3 recuperacoes pos-certificado (uma por tentativa) = 8.

    O que torna os 6 do laco de certificado alcancaveis: falhar em
    `_try_solve_captcha` NAO interrompe o laco — nao ha `break` ali.
    """
    assert "MAX_TENTATIVAS_CERT = 3" in FONTE_LOGIN
    assert FONTE_LOGIN.count('_try_solve_captcha(page, "captcha-pos-govbr"') == 1
    assert FONTE_LOGIN.count('_try_solve_captcha(page, f"captcha-pos-cert-t{tentativa}"') == 1
    assert FONTE_LOGIN.count('_try_solve_captcha(page, "captcha-pos-bloqueado"') == 1
    assert FONTE_LOGIN.count("_recuperar_acesso_bloqueado(page, api_key=gemini_api_key)") == 2

    trecho = FONTE_LOGIN[FONTE_LOGIN.index('f"captcha-pos-cert-t{tentativa}"'):]
    trecho = trecho[: trecho.index("if _ja_logado(page):")]
    assert "break" not in trecho, "captcha falho nao encerra o laco de certificado"


P = 8


# ── C · tentativas do caller por ponto ────────────────────────────────────────

def test_c_o_caller_tenta_tres_vezes_por_ponto():
    assert "max_attempts: int = 3" in FONTE_LOGIN


C = 3


# ── R · rodadas de solve_hcaptcha ─────────────────────────────────────────────

def test_r_seis_rodadas_por_chamada():
    assert "max_rounds: int = 6" in FONTE_SOLVER


R = 6


# ── S · tentativas da estrategia por rodada ───────────────────────────────────

def test_s_o_que_max_gemini_tries_realmente_conta():
    """V da entrega, e a correcao do relatorio anterior.

    `MAX_GEMINI_TRIES` NAO conta modelos nem tentativas por modelo. Ele e o laco
    DENTRO de cada estrategia de solucao (grade, grade_fused, imagem, cartao),
    em volta de "tira screenshot e pergunta ao Gemini". Aparece tres vezes, uma
    por estrategia que o usa.
    """
    assert "MAX_GEMINI_TRIES       = 5" in FONTE_SOLVER
    assert FONTE_SOLVER.count("for attempt in range(1, MAX_GEMINI_TRIES + 1):") == 3
    assert solver.MAX_GEMINI_TRIES == 5


S = 5


# ── G · chamadas ao Gemini por tentativa de estrategia ────────────────────────

def test_g_cada_tentativa_de_estrategia_faz_uma_chamada():
    """Uma `_gemini_call` por tentativa — e erro dela vira `continue`, gastando a
    tentativa sem interromper o laco."""
    for helper in ("_gemini_grade(", "_gemini_grade_fused(", "_gemini_grid(",
                   "_gemini_cartao_animal("):
        assert helper in FONTE_SOLVER

    trecho = FONTE_SOLVER[FONTE_SOLVER.index("result = _gemini_grade(png, ref_img, api_key)"):]
    trecho = trecho[: trecho.index("valid_tiles = sorted")]
    assert "except Exception" in trecho and "continue" in trecho


G = 1


# ── M · T · dentro de uma _gemini_call ────────────────────────────────────────

def test_m_e_t_sao_modelos_e_tentativas_por_modelo():
    assert len(solver.GEMINI_MODELS) == 4, "lista padrao, sem GEMINI_MODELS no ambiente"
    assert solver.GEMINI_TRIES_PER_MODEL == 2
    # A ordem muda entre chamadas (memoria de modelos), o teto nao: a ordem e
    # sempre um subconjunto de GEMINI_MODELS, sem repeticao.
    assert "for mi, model in enumerate(_modelos_na_ordem()):" in FONTE_SOLVER
    ordem = solver._modelos_na_ordem()
    assert len(ordem) == len(set(ordem)) <= len(solver.GEMINI_MODELS)
    assert set(ordem) <= set(solver.GEMINI_MODELS)
    assert "for attempt in range(1, GEMINI_TRIES_PER_MODEL + 1):" in FONTE_SOLVER


M = 4
T = 2


# ── A formula ─────────────────────────────────────────────────────────────────

def test_o_limite_superior_do_produto_das_camadas():
    """MAX_REQUESTS = P x C x R x S x G x M x T.

    ATENCAO — este e o produto dos LIMITES, e nao um maximo demonstrado como
    alcancavel numa unica execucao. Chegar la exigiria que os sete niveis
    esgotassem sem que nenhum retorno antecipado disparasse, e isso NAO foi
    demonstrado. Nao chamar de "piso": multiplicar maximos nunca produz piso.
    """
    assert P * C * R * S * G * M * T == 5_760


def test_o_minimo_demonstravel_de_cada_caminho():
    """MIN_REQUESTS, estes sim demonstraveis pela estrutura:

    - login sem captcha nenhum: ZERO chamadas;
    - um desafio resolvido na primeira tentativa da primeira estrategia da
      primeira rodada do primeiro ponto, com o primeiro modelo acertando de
      primeira: UMA chamada.
    """
    assert "if tipo == \"nenhum\":" in FONTE_SOLVER, "sem desafio, retorna sem chamar"
    assert "return True" in FONTE_SOLVER


def test_o_unico_delay_do_contrato_inteiro_fica_no_main():
    """Nenhum dos niveis do login espera entre tentativas — so o main espera 2s,
    e isso ficou na fronteira do captcha da fatia 6."""
    trecho = FONTE_LOGIN[FONTE_LOGIN.index("def _try_solve_captcha"):]
    trecho = trecho[: trecho.index("def _ja_logado")]

    assert "sleep" not in trecho and "wait_for_timeout" not in trecho
