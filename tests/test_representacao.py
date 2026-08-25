"""A fronteira da representacao, exercitada pela sua propria API.

Nenhum teste abre navegador, portal ou captcha: a navegacao legada inteira entra
por parametro. CNPJs e mensagens ficticios.
"""
import dataclasses

import pytest

from automation.representacao import (
    ANTI_BOT_ESGOTADO,
    NAO_CONFIRMADO,
    RECUSA_DO_CNPJ,
    REPRESENTADO,
    AntiBotEsgotado,
    RepresentacaoNaoConfirmada,
    ResultadoDaRepresentacao,
    representar,
)

CNPJ = "11111111000191"


class RecusaDoPortal(Exception):
    """Dublê de FalhaPermanente — a fronteira a recebe por parametro."""

    def __init__(self, mensagem, status_coluna_d=None):
        super().__init__(mensagem)
        self.status_coluna_d = status_coluna_d


class SessaoFalsa:
    def __init__(self):
        self.pagina = object()
        self.encerrada = False

    def encerrar(self):
        self.encerrada = True


def executar_que(erro=None):
    def executar(pagina, cnpj):
        executar.recebido = (pagina, cnpj)
        if erro is not None:
            raise erro
    executar.recebido = None
    return executar


def pedir(sessao, executar):
    return representar(sessao, CNPJ, executar, RecusaDoPortal)


# ── H · os quatro desfechos ───────────────────────────────────────────────────

def test_h_representado():
    sessao = SessaoFalsa()
    executar = executar_que()

    resultado = pedir(sessao, executar)

    assert resultado.situacao == REPRESENTADO
    assert resultado.representado is True
    assert resultado.encerra_a_linha is False
    assert executar.recebido == (sessao.pagina, CNPJ)


def test_h_recusa_do_cnpj_carrega_o_status_da_coluna_d():
    resultado = pedir(
        SessaoFalsa(),
        executar_que(RecusaDoPortal("recusou", status_coluna_d="Procuração sem autorização")),
    )

    assert resultado.situacao == RECUSA_DO_CNPJ
    assert resultado.status_coluna_d == "Procuração sem autorização"
    assert resultado.encerra_a_linha is True


def test_h_recusa_sem_status_tambem_encerra_a_linha():
    resultado = pedir(SessaoFalsa(), executar_que(RecusaDoPortal("recusou")))

    assert resultado.situacao == RECUSA_DO_CNPJ
    assert resultado.status_coluna_d is None
    assert resultado.encerra_a_linha is True


def test_h_anti_bot_esgotado():
    resultado = pedir(SessaoFalsa(), executar_que(AntiBotEsgotado("acabaram as tentativas")))

    assert resultado.situacao == ANTI_BOT_ESGOTADO
    assert resultado.representado is False
    assert resultado.encerra_a_linha is False, "é da sessão, não do CNPJ"


def test_h_nao_confirmado():
    resultado = pedir(SessaoFalsa(), executar_que(RepresentacaoNaoConfirmada("portal exibe outro")))

    assert resultado.situacao == NAO_CONFIRMADO
    assert resultado.encerra_a_linha is False


def test_h_os_desfechos_sao_um_conjunto_fechado_de_quatro():
    situacoes = {
        pedir(SessaoFalsa(), executar_que(erro)).situacao
        for erro in (None, RecusaDoPortal("x"), AntiBotEsgotado("x"),
                     RepresentacaoNaoConfirmada("x"))
    }
    assert situacoes == {REPRESENTADO, RECUSA_DO_CNPJ, ANTI_BOT_ESGOTADO, NAO_CONFIRMADO}


# ── A distincao que importa para a orquestracao ───────────────────────────────

def test_encerra_a_linha_nao_e_o_mesmo_que_nao_representado():
    """Tres desfechos nao representam; so UM encerra a linha. Anti-bot e
    nao-confirmado sao problemas da sessao ou do momento — a linha volta."""
    nao_representados = [
        ResultadoDaRepresentacao(RECUSA_DO_CNPJ),
        ResultadoDaRepresentacao(ANTI_BOT_ESGOTADO),
        ResultadoDaRepresentacao(NAO_CONFIRMADO),
    ]

    assert not any(r.representado for r in nao_representados)
    assert [r.encerra_a_linha for r in nao_representados] == [True, False, False]


# ── S · nada e classificado por texto de mensagem ─────────────────────────────

def test_s_a_distincao_e_por_tipo_e_nunca_por_mensagem():
    """Duas exceptions com a MESMA frase e tipos diferentes dao desfechos
    diferentes — prova de que ninguem esta lendo texto."""
    frase = "aconteceu alguma coisa"

    assert pedir(SessaoFalsa(), executar_que(AntiBotEsgotado(frase))).situacao == ANTI_BOT_ESGOTADO
    assert pedir(
        SessaoFalsa(), executar_que(RepresentacaoNaoConfirmada(frase))
    ).situacao == NAO_CONFIRMADO


def test_s_a_fronteira_nao_le_str_da_exception():
    import ast
    import pathlib

    from automation import representacao

    codigo = pathlib.Path(representacao.__file__).read_text(encoding="utf-8")
    arvore = ast.parse(codigo)
    chamadas = {
        no.func.id for no in ast.walk(arvore)
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
    }
    assert "str" not in chamadas
    assert ".args" not in codigo


# ── S · bug nosso sobe ────────────────────────────────────────────────────────

@pytest.mark.parametrize("erro", [TypeError("bug"), AttributeError("bug"),
                                  ValueError("bug"), RuntimeError("bug")])
def test_s_bug_nosso_nao_vira_desfecho(erro):
    with pytest.raises(type(erro)):
        pedir(SessaoFalsa(), executar_que(erro))


def test_s_excecao_desconhecida_do_navegador_tambem_sobe():
    """Um timeout de seletor nao e desfecho de negocio — ele sobe para quem
    decide sobre a sessao."""
    from navegador_falso import ErroDeNavegacao

    with pytest.raises(ErroDeNavegacao):
        pedir(SessaoFalsa(), executar_que(ErroDeNavegacao("avatar não apareceu")))


# ── Q · ownership ─────────────────────────────────────────────────────────────

def test_q_a_representacao_nao_encerra_a_sessao():
    sessao = SessaoFalsa()

    for erro in (None, RecusaDoPortal("x"), AntiBotEsgotado("x"),
                 RepresentacaoNaoConfirmada("x")):
        pedir(sessao, executar_que(erro))

    assert sessao.encerrada is False, "a fronteira nao e dona da sessao"


def test_q_so_a_pagina_da_sessao_atravessa_para_o_legado():
    sessao = SessaoFalsa()
    executar = executar_que()

    pedir(sessao, executar)

    pagina, _ = executar.recebido
    assert pagina is sessao.pagina
    assert pagina is not sessao, "a sessao inteira nao desce para a navegacao"


# ── Mensagens seguras ─────────────────────────────────────────────────────────

def test_nenhum_desfecho_carrega_cnpj_ou_texto_do_portal():
    sentinela = "Portal recusou CNPJ 11111111000191: 'ACME PARTICIPACOES vencida'"
    resultado = pedir(
        SessaoFalsa(), executar_que(RecusaDoPortal(sentinela, status_coluna_d=None))
    )

    for campo in (resultado.situacao, str(resultado.status_coluna_d)):
        for proibido in ("11111111000191", "ACME", "PARTICIPACOES"):
            assert proibido not in campo


def test_o_resultado_e_imutavel():
    resultado = pedir(SessaoFalsa(), executar_que())
    with pytest.raises(dataclasses.FrozenInstanceError):
        resultado.situacao = "outra"
