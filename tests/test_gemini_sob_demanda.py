"""A chave do Gemini e pedida quando alguem precisa dela — e so entao.

O ESTADO ANTES DA D8.3-B, reproduzido antes da correcao: a borda da plataforma
pedia `gemini_api_key` ao cofre como PRIMEIRA acao, antes ate de pedir a
planilha. Uma execucao sem nada a processar dependia de uma credencial que
nunca usaria — e, no QA, onde o vinculo tem outro alias, morria por isso.

O desenho: `ConfigCaptcha` pode receber, em vez da chave, uma funcao sem
argumentos que a obtem. Ela e chamada no primeiro `chave()` — na pratica, no
primeiro login, porque o login ja exige a chave ao abrir a sessao — e o
resultado fica guardado. Ninguem em `automation/` sabe de onde a chave vem.

Nada aqui fala com cofre, Gemini, navegador ou portal. Chaves sao sentinelas.
"""
import ast
import dataclasses
import inspect
import pathlib

import pytest

from automation import app, planilha
from automation.captcha import ConfigCaptcha, ConfiguracaoInvalida, resolver
from automation.domain import ResultadoDaBusca
from automation.login import AUTENTICADO, Certificado, ResultadoDoLogin

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CHAVE = "AIzaSy-SENTINELA-SOB-DEMANDA-0000"


class FonteDaChave:
    """Conta quantas vezes a chave foi pedida. Pode tambem recusar."""

    def __init__(self, valor=CHAVE, falha=None):
        self.valor = valor
        self.falha = falha
        self.pedidos = 0

    def __call__(self):
        self.pedidos += 1
        if self.falha is not None:
            raise self.falha
        return self.valor


# ── a config sob demanda ─────────────────────────────────────────────────────

def test_a_config_de_sempre_nao_mudou():
    """O desktop continua construindo com a chave em maos."""
    config = ConfigCaptcha(api_key=CHAVE)

    assert config.chave() == CHAVE
    assert config.obter_chave is None
    assert config == ConfigCaptcha(api_key=CHAVE)


def test_construir_sob_demanda_NAO_pede_a_chave():
    fonte = FonteDaChave()

    ConfigCaptcha.sob_demanda(fonte)

    assert fonte.pedidos == 0


def test_a_chave_e_pedida_no_primeiro_uso_e_UMA_vez_so():
    fonte = FonteDaChave()
    config = ConfigCaptcha.sob_demanda(fonte)

    assert config.chave() == CHAVE
    assert config.chave() == CHAVE
    config.validar()

    assert fonte.pedidos == 1


def test_chave_obtida_invalida_e_recusada_como_sempre_foi():
    for valor, trecho in (("", "configurada"), ("   ", "configurada"),
                          ("cole-" + CHAVE, "exemplo")):
        config = ConfigCaptcha.sob_demanda(FonteDaChave(valor))
        with pytest.raises(ConfiguracaoInvalida, match=trecho) as erro:
            config.chave()
        assert "SENTINELA" not in str(erro.value)


def test_falha_ao_obter_a_chave_SOBE_e_nada_fica_guardado():
    """Credencial necessaria e ausente nao vira sucesso nem chave vazia."""
    fonte = FonteDaChave(falha=RuntimeError("credencial nao vinculada"))
    config = ConfigCaptcha.sob_demanda(fonte)

    for _ in range(2):
        with pytest.raises(RuntimeError):
            config.chave()

    assert fonte.pedidos == 2, "nada guardado: a proxima tentativa pede de novo"


def test_a_chave_obtida_nao_aparece_em_repr_nem_em_asdict():
    config = ConfigCaptcha.sob_demanda(FonteDaChave())
    config.chave()

    assert CHAVE not in repr(config)
    assert CHAVE not in str(dataclasses.asdict(config)), (
        "a chave guardada nao e campo: nao entra em asdict")


def test_o_captcha_pede_a_chave_pela_config_sob_demanda():
    fonte = FonteDaChave()
    config = ConfigCaptcha.sob_demanda(fonte)

    resolver(object(), config, resolver_bruto=lambda _alvo: True)

    assert fonte.pedidos == 1


# ── na aplicacao: o primeiro login e o momento ───────────────────────────────

class _PlanilhaInerte:
    def salvar(self):
        pass

    def descartar(self):
        pass

    def mapa_status(self, caminho):
        return {}


class _Provedor:
    def carregar(self):
        return 1

    def resolver(self, nome):
        return ResultadoDaBusca(chave=nome, criterio="exato")

    def certificado(self, chave):
        return Certificado(subject_cn="", serial="", pfx_path="x.pfx", pfx_senha="s")


def _execucao(config, monkeypatch, abrir_sessao):
    execucao = app._Execucao(_PlanilhaInerte(), "p.xlsx", config, None, _Provedor())
    execucao.certificado_atual = "ALFA"
    execucao.policy_confiavel = True
    monkeypatch.setattr(app, "maquina", type("M", (), {
        "estado_do_guardiao": staticmethod(lambda c: None),
        "abrir_sessao": staticmethod(abrir_sessao),
    }))
    monkeypatch.setattr(app._Execucao, "exigir_responsavel_pela_policy",
                        lambda self: None)
    return execucao


def _item():
    return planilha.ItemPendente(posicao=0, cnpj="11111111000191",
                                 certificado="ALFA", linha=2)


def test_o_login_recebe_a_chave_e_ela_e_pedida_UMA_vez(monkeypatch):
    fonte = FonteDaChave()
    recebidas = []

    def abrir_sessao(cert, auto, chave, chao=None):
        recebidas.append(chave)
        return ResultadoDoLogin(AUTENTICADO, object())

    execucao = _execucao(ConfigCaptcha.sob_demanda(fonte), monkeypatch, abrir_sessao)

    assert execucao.autenticar(_item()) is True
    execucao.sessao = None  # um segundo login, como numa troca de certificado
    assert execucao.autenticar(_item()) is True

    assert recebidas == [CHAVE, CHAVE]
    assert fonte.pedidos == 1


def test_sem_a_chave_o_login_FALHA_antes_de_o_navegador_abrir(monkeypatch):
    fonte = FonteDaChave(falha=RuntimeError("credencial nao vinculada"))

    def navegador_nao_pode_abrir(*_a, **_k):
        raise AssertionError("abriu sessao sem a chave")

    execucao = _execucao(ConfigCaptcha.sob_demanda(fonte), monkeypatch,
                         navegador_nao_pode_abrir)

    with pytest.raises(RuntimeError):
        execucao.autenticar(_item())


def test_a_falha_da_chave_nao_e_engolida_pelo_laco():
    """`autenticar` fica FORA de todo `try` de `_percorrer`: a falha ao obter a
    chave nao vira "login falhou, proxima linha" — ela encerra a execucao."""
    arvore = ast.parse(inspect.getsource(app._percorrer))
    dentro_de_try = {
        n.func.attr
        for tentativa in ast.walk(arvore) if isinstance(tentativa, ast.Try)
        for bloco in tentativa.body
        for n in ast.walk(bloco)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }

    assert "autenticar" not in dentro_de_try


# ── quem le a chave ──────────────────────────────────────────────────────────

def test_em_automation_a_chave_so_e_lida_por_chave():
    """Ler o CAMPO de uma config sob demanda devolveria "" — e o login recusaria
    com "chave nao configurada". O campo so e lido dentro da propria config."""
    leitores = set()
    for caminho in sorted((RAIZ / "automation").glob("*.py")):
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        if any(isinstance(n, ast.Attribute) and n.attr == "api_key"
               for n in ast.walk(arvore)):
            leitores.add(caminho.name)

    assert leitores <= {"captcha.py"}
