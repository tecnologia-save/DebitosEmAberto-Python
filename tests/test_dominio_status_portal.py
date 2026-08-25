"""A regra de status extraida, exercitada pela sua propria API.

A suite de caracterizacao ja prova a equivalencia atraves de `main`. O que esta
aqui e a pergunta de sucesso da fatia: "dado SO o texto do portal, decido se a
linha encerra ou volta para a fila?" — respondida sem navegador, login ou planilha.
"""
import pytest

from automation.status_portal import (
    STATUS_D_TERMINAIS,
    STATUS_SEM_AUTORIZACAO,
    FalhaPermanente,
    recusa_permanente,
    status_da_recusa,
    status_encerra_linha,
)

ENCERRA_DE_VEZ = "encerra a linha"
PULA_ESTA_EXECUCAO = "pula nesta execução"
TENTA_DE_NOVO = "tenta de novo"


def decidir(texto: str) -> str:
    """O que a automacao faz com este texto — so com a regra pura em maos."""
    if status_da_recusa(texto):
        return ENCERRA_DE_VEZ
    if recusa_permanente(texto):
        return PULA_ESTA_EXECUCAO
    return TENTA_DE_NOVO


@pytest.mark.parametrize(
    ("texto", "decisao"),
    [
        ("Sua autorização como procurador não permite acesso a este serviço", ENCERRA_DE_VEZ),
        ("Procuração vencida", PULA_ESTA_EXECUCAO),
        ("O CNPJ não possui procuração eletrônica", PULA_ESTA_EXECUCAO),
        ("Erro interno do servidor", TENTA_DE_NOVO),
        ("Serviço temporariamente indisponível", TENTA_DE_NOVO),
        ("", TENTA_DE_NOVO),
    ],
)
def test_o_texto_do_portal_basta_para_decidir(texto, decisao):
    assert decidir(texto) == decisao


def test_so_o_desfecho_que_encerra_grava_status():
    """Um dos tres desfechos escreve na planilha; os outros dois nao."""
    assert status_da_recusa("não permite acesso a este serviço") == STATUS_SEM_AUTORIZACAO
    assert status_da_recusa("Procuração vencida") is None
    assert status_da_recusa("Erro interno do servidor") is None


def test_o_ciclo_fecha_entre_execucoes():
    """O status gravado hoje e o que faz a linha ser pulada amanha."""
    status = status_da_recusa("não permite acesso a este serviço")
    assert status in STATUS_D_TERMINAIS
    assert status_encerra_linha(status)


def test_falha_permanente_carrega_um_desfecho_e_nao_um_diagnostico_tecnico():
    """Evidencia para a migracao futura da representacao (ver relatorio).

    O que a excecao transporta e o resultado de uma regra de negocio — um status
    para gravar na planilha — e nao rastro de erro tecnico.
    """
    falha = FalhaPermanente("recusa", status_coluna_d=STATUS_SEM_AUTORIZACAO)

    assert falha.status_coluna_d in STATUS_D_TERMINAIS
    assert falha.__cause__ is None, "nasce da regra, nao de outra exception"


def test_a_regra_nao_imprime(capsys):
    for texto in ["Procuração vencida", "não permite acesso a este serviço", "outro"]:
        decidir(texto)
    assert capsys.readouterr().out == ""
