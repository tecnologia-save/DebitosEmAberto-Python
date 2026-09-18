"""`_gemini_call` lembra qual modelo respondeu e quais estao fora.

O ESTADO ANTES, observado numa execucao real sob pico de demanda: cada desafio
recomecava a lista — 503, 503, 503, 503, 429, 429 — antes de chegar ao modelo
que respondia. Um a tres minutos por desafio; o hCaptcha trocava o desafio no
meio e o login entrava em laco.

Nenhum teste chama Gemini: o cliente e falso, os erros sao as classes reais do
SDK construidas localmente.
"""
import pytest
from google.genai import errors

from resolvedor_captcha import solver

LITE = "gemini-3.1-flash-lite"


def _erro(codigo):
    status = {503: "UNAVAILABLE", 429: "RESOURCE_EXHAUSTED"}[codigo]
    classe = errors.ServerError if codigo >= 500 else errors.ClientError
    return classe(codigo, {"error": {"code": codigo, "message": "x", "status": status}})


class _Resposta:
    text = '{"ok": true}'


class _ClienteFalso:
    """`comportamento[modelo]` e uma lista consumida a cada chamada: um codigo
    HTTP levanta o erro do SDK; "ok" responde."""

    def __init__(self, comportamento):
        self.comportamento = {m: list(v) for m, v in comportamento.items()}
        self.chamadas = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.chamadas.append(model)
        fila = self.comportamento.get(model) or ["ok"]
        passo = fila.pop(0) if len(fila) > 1 else fila[0]
        if passo == "ok":
            return _Resposta()
        raise _erro(passo)


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(solver, "GEMINI_MODELS", list(solver.GEMINI_MODELS[:4]))
    monkeypatch.setattr(solver, "_modelo_que_respondeu", None)
    monkeypatch.setattr(solver, "_fora_ate", {})
    monkeypatch.setattr(solver, "_make_config", lambda schema, model: None)
    monkeypatch.setattr(solver.time, "sleep", lambda _s: None)

    def instalar(comportamento):
        falso = _ClienteFalso(comportamento)
        monkeypatch.setattr(solver, "_get_client", lambda _k: falso)
        return falso
    return instalar


def _chamar():
    return solver._gemini_call([], {}, "chave-ficticia", "teste")


def _sobrecarga_menos_o_lite():
    flash, flash36, pro = solver.GEMINI_MODELS[:3]
    return {flash: [503], flash36: [503], pro: [429], LITE: ["ok"]}


def test_503_e_429_nao_repetem_o_mesmo_modelo(cliente):
    falso = cliente(_sobrecarga_menos_o_lite())

    assert _chamar() == {"ok": True}

    assert falso.chamadas == [*solver.GEMINI_MODELS[:3], LITE], "uma tentativa por modelo"


def test_a_segunda_chamada_vai_direto_ao_que_respondeu(cliente):
    falso = cliente(_sobrecarga_menos_o_lite())
    _chamar()
    falso.chamadas.clear()

    assert _chamar() == {"ok": True}

    assert falso.chamadas == [LITE]


def test_quem_respondeu_e_caiu_perde_a_frente_e_entra_em_pausa(cliente):
    falso = cliente(_sobrecarga_menos_o_lite())
    _chamar()
    falso.comportamento[LITE] = [503]
    with pytest.raises(RuntimeError):
        _chamar()

    assert solver._modelo_que_respondeu is None
    assert LITE in solver._fora_ate


def test_todos_em_pausa_tenta_todos_em_vez_de_desistir(cliente):
    falso = cliente({m: [503] for m in solver.GEMINI_MODELS})
    with pytest.raises(RuntimeError):
        _chamar()
    falso.chamadas.clear()
    falso.comportamento = {m: ["ok"] for m in solver.GEMINI_MODELS}

    assert _chamar() == {"ok": True}
    assert falso.chamadas == [solver.GEMINI_MODELS[0]]


def test_a_pausa_expira(cliente, monkeypatch):
    falso = cliente(_sobrecarga_menos_o_lite())
    _chamar()
    agora = solver.time.time()
    monkeypatch.setattr(solver.time, "time", lambda: agora + solver.PAUSA_COTA_S + 1)
    monkeypatch.setattr(solver, "_modelo_que_respondeu", None)
    falso.comportamento = {m: ["ok"] for m in solver.GEMINI_MODELS}
    falso.chamadas.clear()

    _chamar()

    assert falso.chamadas == [solver.GEMINI_MODELS[0]], "o primario volta depois da pausa"


def test_erro_sem_codigo_continua_com_duas_tentativas(cliente):
    """Rede/transitorio sem `code`: o comportamento de antes, tentar de novo."""
    primeiro = solver.GEMINI_MODELS[0]
    falso = cliente({})

    def instavel(model, contents, config):
        falso.chamadas.append(model)
        if len(falso.chamadas) == 1:
            raise ConnectionError("rede")
        return _Resposta()
    falso.generate_content = instavel

    assert _chamar() == {"ok": True}
    assert falso.chamadas == [primeiro, primeiro]
    assert solver._fora_ate == {}
