"""A regra de status extraida, exercitada pela sua propria API.

A suite de caracterizacao ja prova a equivalencia atraves de `main`. O que esta
aqui e a pergunta de sucesso da fatia: "dado SO o texto do portal, decido se a
linha encerra ou volta para a fila?" — respondida sem navegador, login ou planilha.
"""
import pytest
from test_caracterizacao_antibot import _ORIGINAL

from automation.status_portal import (
    ANTIBOT,
    NAO_RECONHECIDA,
    RECUSA_DO_CNPJ,
    STATUS_D_TERMINAIS,
    STATUS_SEM_AUTORIZACAO,
    FalhaPermanente,
    classificar_mensagem,
    condicao_antibot,
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


# ── Fatia 2.1: anti-bot ──────────────────────────────────────────────────────


CASOS = [
    "Detectamos acesso automatizado a este portal", "Acesso bloqueado", "ACESSO BLOQUEADO",
    "AcEsSo AuToMaTiZaDo", "erro: acesso temporariamente bloqueado pelo sistema",
    "CNPJ bloqueado na base da Receita", "Certificado bloqueado", "BLOQUEÁDO",
    "", "   ", "Erro interno do servidor", "automatiza", "bloque", "bloqueio",
    "bloquear", "automatico", "automático", "automatizada", "automatizádo",
    "Procuração vencida", "não permite acesso a este serviço",
    "Acesso bloqueado: procuração vencida",
]


@pytest.mark.parametrize("texto", CASOS, ids=lambda t: repr(t)[:32])
def test_a_regra_extraida_concorda_com_o_original_caso_a_caso(texto):
    """A prova de equivalencia da fatia 2.1.

    `_ORIGINAL` e a transcricao congelada do que estava inline — provada fiel ao
    codigo por AST na suite de caracterizacao. Se as duas concordam em todo caso,
    a extracao nao mudou nada.
    """
    assert condicao_antibot(texto) == _ORIGINAL(texto)


@pytest.mark.parametrize(
    ("texto", "classe"),
    [
        ("Detectamos acesso automatizado", ANTIBOT),
        ("Acesso bloqueado", ANTIBOT),
        ("Procuração vencida", RECUSA_DO_CNPJ),
        ("não permite acesso a este serviço", RECUSA_DO_CNPJ),
        ("Erro interno do servidor", NAO_RECONHECIDA),
        ("", NAO_RECONHECIDA),
    ],
)
def test_distingo_anti_bot_de_recusa_so_com_o_texto(texto, classe):
    """O criterio de sucesso da fatia 2.1, sem navegador."""
    assert classificar_mensagem(texto) == classe


def test_a_precedencia_do_original_foi_preservada():
    """Um texto que casa nas DUAS regras continua sendo anti-bot — que e o que o
    original fazia, por ordem dos `if`. Nao e detalhe: decide entre retentar e
    gravar a coluna D."""
    ambos = "Acesso bloqueado: procuração vencida"

    assert recusa_permanente(ambos), "casa tambem na regra de recusa"
    assert classificar_mensagem(ambos) == ANTIBOT, "mas o anti-bot tem precedencia"


def test_o_anti_bot_nao_produz_status_para_a_planilha():
    """Nenhum caminho anti-bot grava nada — e por isso que a ma classificacao de
    "CNPJ bloqueado" desperdica trabalho sem corromper resultado."""
    for texto in ["Acesso bloqueado", "CNPJ bloqueado na base da Receita"]:
        assert classificar_mensagem(texto) == ANTIBOT
        assert status_da_recusa(texto) is None


def test_o_inline_saiu_de_main():
    """A regra nao esta mais escrita duas vezes dentro da navegacao."""
    import pathlib

    principal = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8-sig"
    )
    assert '"automatizado" in' not in principal

    # A classificacao acompanhou a representacao para automation/ na 8A2.
    repr_fonte = (
        pathlib.Path(__file__).resolve().parents[1] / "automation" / "representacao.py"
    ).read_text(encoding="utf-8")
    assert "status_portal.classificar_mensagem(" in repr_fonte
