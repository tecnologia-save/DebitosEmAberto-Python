"""A integracao de descoberta, exercitada pela sua propria API.

A caracterizacao ja prova a equivalencia atraves de `main`. O que esta aqui e o
criterio de sucesso da fatia: o contrato e testavel SEM Windows, porque a unica
coisa que precisa de Windows real e um callable.
"""
import json

import pytest

from automation.certificados_windows import (
    COMANDO_POWERSHELL,
    FalhaAoLerCertificados,
    chaves_do_certificado,
    descobrir,
    e_certificado_icp,
    identidades,
    indexar,
    interpretar_saida,
)
from automation.domain import buscar_certificado

ALFA = {"display": "ALFA FICTICIA LTDA", "subject_cn": "ALFA FICTICIA LTDA:11111111000191"}
BETA = {"display": "BETA FICTICIA SA", "subject_cn": "BETA FICTICIA SA:22222222000172"}
SISTEMA = {"display": "Sistema", "subject_cn": "00000000-0000-0000-0000-000000000000"}


def executor(certificados):
    """A fronteira externa inteira cabe num callable — sem service, sem factory."""
    def executar(comando):
        assert comando is COMANDO_POWERSHELL
        return json.dumps(certificados)
    return executar


# ── O executor substituivel ───────────────────────────────────────────────────

def test_descobrir_sem_powershell_nenhum():
    mapa, ignorados = descobrir(executor([ALFA, BETA]))

    assert ignorados == 0
    assert "alfa ficticia ltda" in mapa and "beta ficticia sa" in mapa


def test_repositorio_vazio_e_diferente_de_falha():
    """A distincao que a fatia 5A introduz de proposito."""
    vazio, _ = descobrir(lambda _: "")
    assert vazio == {}

    with pytest.raises(FalhaAoLerCertificados):
        descobrir(lambda _: "isto nao e json")


def test_falha_externa_conhecida_tem_mensagem_constante():
    with pytest.raises(FalhaAoLerCertificados) as erro:
        interpretar_saida("SENTINELA-CN:11111111000191 nao e json")

    mensagem = str(erro.value)
    assert "SENTINELA-CN" not in mensagem
    assert "11111111000191" not in mensagem


def test_formato_inesperado_tambem_e_falha_conhecida():
    with pytest.raises(FalhaAoLerCertificados, match="formato inesperado"):
        interpretar_saida("42")


def test_bug_nosso_sobe():
    """O executor levanta algo que nao e falha externa — nao viramos tradutor."""
    def executar(_):
        raise TypeError("bug nosso")

    with pytest.raises(TypeError):
        descobrir(executar)


# ── Filtro e indexacao ────────────────────────────────────────────────────────

@pytest.mark.parametrize("cn", [
    "EMPRESA:11111111000191", "PESSOA:11122233344", "COM ESPACO : 11111111000191",
])
def test_cn_da_icp_e_reconhecido(cn):
    assert e_certificado_icp(cn) is True


@pytest.mark.parametrize("cn", [
    "", None, "00000000-0000-0000-0000-000000000000", "EMPRESA", "EMPRESA:123",
    "EMPRESA:111111110001911",
])
def test_cn_fora_do_padrao_nao_e_icp(cn):
    assert e_certificado_icp(cn) is False


def test_certificado_de_sistema_e_contado_e_descartado():
    mapa, ignorados = indexar([ALFA, SISTEMA])

    assert ignorados == 1
    assert not any("sistema" in chave for chave in mapa)


def test_chaves_de_um_certificado():
    assert set(chaves_do_certificado(ALFA)) == {
        "alfa ficticia ltda", "alfa ficticia ltda:11111111000191",
    }


def test_chave_vazia_nunca_entra():
    assert "" not in chaves_do_certificado({"display": "", "subject_cn": ""})


def test_identidades_traduz_para_o_que_a_regra_consome():
    mapa, _ = indexar([ALFA])

    assert set(identidades(mapa).values()) == {ALFA["subject_cn"]}


# ── R · o circuito fechado, sem Windows ───────────────────────────────────────

def test_do_executor_ate_o_resultado_da_busca():
    mapa, _ = descobrir(executor([ALFA, BETA]))
    disponiveis = identidades(mapa)

    resultado = buscar_certificado("ALFA FICTICIA LTDA", disponiveis)

    assert resultado.resolvida
    assert disponiveis[resultado.chave] == ALFA["subject_cn"]


def test_a_ambiguidade_sobrevive_ao_circuito():
    """A propriedade de seguranca da fatia 1.1, com dados vindos da descoberta."""
    outra = {"display": "ALFA COMERCIO SA", "subject_cn": "ALFA COMERCIO SA:33333333000153"}
    mapa, _ = descobrir(executor([ALFA, outra]))

    resultado = buscar_certificado("ALFA", identidades(mapa))

    assert not resultado.resolvida
    assert resultado.ambigua
