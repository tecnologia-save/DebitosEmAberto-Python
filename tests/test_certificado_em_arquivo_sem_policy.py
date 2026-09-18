"""Certificado do cofre nao pede a policy do Windows.

O ESTADO ANTES, observado numa execucao real na plataforma: o certificado do
cofre chega como ARQUIVO (`subject_cn` vazio, `pfx_path` preenchido) e o fork o
entrega ao navegador como `client_certificates`. Mesmo assim `trocar_certificado`
pedia a policy de auto-selecao — UAC e processo guardiao — para um CN vazio. No
agente, que roda em segundo plano, ninguem aceitou o UAC, o guardiao nao ficou
vivo, e a execucao parou em `exigir_responsavel_pela_policy`, antes do login.

A policy so faz sentido para certificado INSTALADO no Windows Store.

Nada aqui fala com registro, UAC, navegador ou cofre. Certificados sao ficticios.
"""
import pytest

from automation import app, planilha
from automation.captcha import ConfigCaptcha
from automation.domain import ResultadoDaBusca
from automation.login import AUTENTICADO, Certificado, ResultadoDoLogin
from automation.policy_certificado import ATIVADA, ResultadoDaPolicy

CHAVE = "AIzaSy-SENTINELA-ARQUIVO-0000"
DO_COFRE = Certificado(subject_cn="", serial="", pfx_path="0.pfx", pfx_senha="s")
DO_STORE = Certificado(subject_cn="ALFA FICTICIA:11111111000191", serial="0A01")


class _PlanilhaInerte:
    def salvar(self):
        pass

    def descartar(self):
        pass

    def mapa_status(self, caminho):
        return {}


class _Provedor:
    def __init__(self, certificado):
        self._certificado = certificado

    def carregar(self):
        return 1

    def resolver(self, nome):
        return ResultadoDaBusca(chave=nome, criterio="exato")

    def certificado(self, chave):
        return self._certificado


def _item():
    return planilha.ItemPendente(posicao=0, cnpj="11111111000191",
                                 certificado="ALFA", linha=2)


@pytest.fixture
def maquina_espia(monkeypatch):
    chamadas = {"policy": [], "sessao": [], "guardiao": []}

    def garantir(cn, nossa=False):
        chamadas["policy"].append(cn)
        return ResultadoDaPolicy(ATIVADA, tem_guardiao=True, controle=object())

    def abrir_sessao(cert, auto, chave, chao=None):
        chamadas["sessao"].append((cert, auto))
        return ResultadoDoLogin(AUTENTICADO, object())

    def estado(controle):
        chamadas["guardiao"].append(controle)
        from automation.policy_certificado import GUARDIAO_VIVO
        return GUARDIAO_VIVO

    monkeypatch.setattr(app.maquina, "garantir_policy_do_windows", garantir)
    monkeypatch.setattr(app.maquina, "abrir_sessao", abrir_sessao)
    monkeypatch.setattr(app.maquina, "estado_do_guardiao", estado)
    return chamadas


def _execucao(certificado):
    return app._Execucao(_PlanilhaInerte(), "p.xlsx", ConfigCaptcha(api_key=CHAVE),
                         None, _Provedor(certificado))


def test_certificado_do_cofre_NAO_pede_policy_e_chega_ao_login(maquina_espia):
    execucao = _execucao(DO_COFRE)

    assert execucao.trocar_certificado(_item()) is True
    assert execucao.autenticar(_item()) is True

    assert maquina_espia["policy"] == [], "nenhum UAC/guardiao para certificado em arquivo"
    assert maquina_espia["guardiao"] == [], "sem policy, nao ha guardiao a exigir"
    assert execucao.controle_da_policy is None
    assert [c for c, _ in maquina_espia["sessao"]] == [DO_COFRE]


def test_certificado_do_store_continua_pedindo_a_policy(maquina_espia):
    execucao = _execucao(DO_STORE)

    assert execucao.trocar_certificado(_item()) is True

    assert maquina_espia["policy"] == [DO_STORE.subject_cn]
    assert execucao.controle_da_policy is not None


@pytest.mark.parametrize("certificado, esperado", [
    (DO_STORE, True),
    (DO_COFRE, False),
    (Certificado(subject_cn="   ", pfx_path="0.pfx", pfx_senha="s"), False),
])
def test_do_windows_store_segue_a_regra_do_fork(certificado, esperado):
    """O fork decide por `bool(cert_subject_cn and cert_subject_cn.strip())`."""
    assert certificado.do_windows_store is esperado


# ── certificado instalado pelo provedor so para a execucao (D8.4) ─────────────

class _ProvedorQueInstala(_Provedor):
    pede_policy_do_windows = False


def test_certificado_instalado_pelo_provedor_nao_pede_policy_e_usa_a_janela(maquina_espia):
    """Sem UAC no agente: nada de policy, e `auto_select=False` faz o login
    resolver a janela "Selecione um certificado" pelo serial."""
    execucao = app._Execucao(_PlanilhaInerte(), "p.xlsx", ConfigCaptcha(api_key=CHAVE),
                             None, _ProvedorQueInstala(DO_STORE))

    assert execucao.trocar_certificado(_item()) is True
    assert execucao.autenticar(_item()) is True

    assert maquina_espia["policy"] == []
    assert maquina_espia["guardiao"] == []
    assert maquina_espia["sessao"] == [(DO_STORE, False)]
