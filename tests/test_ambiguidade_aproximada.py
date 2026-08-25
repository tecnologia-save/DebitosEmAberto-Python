"""FATIA 1.1 — a ambiguidade no criterio aproximado (CERTIFICATE_MATCH_BEHAVIOR_CHANGE).

Esta suite descreve a REGRA AUTORIZADA, nao o comportamento historico:

    criterio aproximado (6)
        zero candidatos elegiveis  -> nao resolve
        exatamente um             -> resolve
        dois ou mais              -> AMBIGUIDADE

e, alem disso, um empate ja observado por um criterio MAIS PRECISO nao pode ser
desfeito pelo criterio aproximado.

O motivo e de seguranca, nao de estilo: escolher o certificado errado autentica
na conta de outra empresa. Diante de duvida, a regra prefere NAO autenticar.

Corpus 100% ficticio.
"""
import pytest
from casos_certificado import CERTS

from automation.domain import (
    CRITERIO_APROXIMACAO,
    CRITERIO_EXATO,
    CRITERIO_INICIO,
    CRITERIO_INICIO_COMPACTO,
    CRITERIO_PALAVRAS,
    CRITERIO_PARCIAL,
    buscar_certificado,
)

# Dois certificados ficticios que so o criterio aproximado alcanca: a diferenca
# esta no MEIO do nome, entao nenhum criterio de prefixo, palavra ou substring
# encosta neles.
AURORA_I = "AURORA TEXTIL FICTICIA:00000000000010"
AURORA_E = "AURORA TEXTEL FICTICIA:00000000000011"
DOIS_PLAUSIVEIS = {
    "aurora textil ficticia": AURORA_I,
    "aurora textel ficticia": AURORA_E,
    "beta industria fake": "BETA INDUSTRIA FAKE:00000000000012",
}
UM_SO_PLAUSIVEL = {
    "aurora textil ficticia": AURORA_I,
    "beta industria fake": "BETA INDUSTRIA FAKE:00000000000012",
}

IDENTIDADES = {k: v["subject_cn"] for k, v in CERTS.items()}
DES = "D&S ASSESSORIA:00000000000004"
DSR = "D.S.R. ASSESSORIA:00000000000005"


# ── A. um unico candidato elegivel -> resolve ─────────────────────────────────

def test_a_um_unico_candidato_acima_do_cutoff_resolve():
    resultado = buscar_certificado("aurora textal ficticia", UM_SO_PLAUSIVEL)

    assert resultado.resolvida
    assert resultado.chave == "aurora textil ficticia"
    assert resultado.criterio == CRITERIO_APROXIMACAO


# ── B. dois candidatos plausiveis -> ambiguidade (comportamento NOVO) ─────────

def test_b_dois_candidatos_acima_do_cutoff_viram_ambiguidade():
    """ANTES: `get_close_matches(n=1)` devolvia so o primeiro e a regra resolvia
    para AURORA TEXTIL em silencio. DEPOIS: os dois sao elegiveis, e a regra recusa.

    As duas grafias tem exatamente a mesma proximidade (0.9545) — nao existe
    "mais parecido" para desempatar sequer por score.
    """
    resultado = buscar_certificado("aurora textal ficticia", DOIS_PLAUSIVEIS)

    assert not resultado.resolvida, "resolver aqui seria escolher por chute"
    assert resultado.ambigua
    assert resultado.criterio == CRITERIO_APROXIMACAO
    assert set(resultado.ambiguidade) == {"aurora textil ficticia", "aurora textel ficticia"}


def test_b_o_terceiro_certificado_distante_nao_entra_no_empate():
    """A ambiguidade lista quem e plausivel, nao a lista inteira."""
    resultado = buscar_certificado("aurora textal ficticia", DOIS_PLAUSIVEIS)

    assert "beta industria fake" not in resultado.ambiguidade
    assert len(resultado.ambiguidade) == 2


# ── C. nenhum candidato elegivel -> nao resolve ───────────────────────────────

@pytest.mark.parametrize("nome", ["zzzzzz qqqqqq", "", "   "])
def test_c_nada_acima_do_cutoff_nao_resolve(nome):
    resultado = buscar_certificado(nome, DOIS_PLAUSIVEIS)

    assert not resultado.resolvida
    assert not resultado.ambigua


def test_c_o_cutoff_nao_foi_afrouxado():
    """A mudanca e sobre QUANTOS candidatos o criterio enxerga, nao sobre quao
    parecido algo precisa ser. Um nome distante continua sem match."""
    from automation.domain import _CUTOFF_APROXIMACAO

    assert _CUTOFF_APROXIMACAO == 0.75
    assert not buscar_certificado("beta industria fake", IDENTIDADES).resolvida


# ── D. criterios 1-5 intactos ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("nome", "esperado", "criterio"),
    [
        ("ALVORADA COMERCIO", "ALVORADA COMERCIO:00000000000001", CRITERIO_EXATO),
        ("Bernardo", "BERNARDO TEIXEIRA MONTENEGRO SOUSA:00000000001", CRITERIO_INICIO),
        ("Montenegro", "BERNARDO TEIXEIRA MONTENEGRO SOUSA:00000000001", CRITERIO_PALAVRAS),
        ("XYZ", "X Y Z CONSULTORIAS:00000000000002", CRITERIO_INICIO_COMPACTO),
        ("D&S", DES, CRITERIO_PARCIAL),
    ],
)
def test_d_os_cinco_primeiros_criterios_nao_mudaram(nome, esperado, criterio):
    resultado = buscar_certificado(nome, IDENTIDADES)

    assert resultado.resolvida
    assert IDENTIDADES[resultado.chave] == esperado
    assert resultado.criterio == criterio


def test_d_a_ambiguidade_dos_criterios_precisos_continua_identica():
    resultado = buscar_certificado("D", IDENTIDADES)

    assert not resultado.resolvida
    assert resultado.criterio == CRITERIO_INICIO
    assert len(resultado.ambiguidade) == 2


def test_d_criterio_mais_frouxo_ainda_separa_empate_entre_1_e_5():
    """A promessa original — "um criterio que empata nao encerra a busca" —
    continua valendo ENTRE os criterios 1-5. So o criterio 6 perdeu esse poder."""
    disponiveis = {
        "delta uno ficticia": "DELTA UNO FICTICIA:1",
        "delta duo ficticia": "DELTA DUO FICTICIA:2",
    }
    # "delta" empata no inicio do nome; "delta uno" isola no criterio seguinte.
    assert buscar_certificado("delta", disponiveis).ambigua
    resolvido = buscar_certificado("delta uno", disponiveis)
    assert resolvido.resolvida
    assert resolvido.chave == "delta uno ficticia"


# ── E. o criterio aproximado nao desfaz empate mais preciso ───────────────────

def test_e_empate_preciso_nao_e_desfeito_pelo_criterio_aproximado():
    """O caso que motivou a fatia 1.1.

    "ASSESSORIA" e palavra inteira em DOIS certificados distintos: os criterios 3
    (palavras inteiras) e 5 (correspondencia parcial) empatam corretamente.

    ANTES: o criterio 6 escolhia D&S e a busca terminava "resolvida", sem uma
    palavra de aviso. DEPOIS: o empate mais preciso e o que vale.

    Note que ampliar o `n` do difflib NAO resolveria este caso sozinho — a outra
    assessoria fica em 0.7407, logo abaixo do cutoff de 0.75. Ver relatorio.
    """
    resultado = buscar_certificado("ASSESSORIA", IDENTIDADES)

    assert not resultado.resolvida, "escolher aqui pode autenticar na empresa errada"
    assert resultado.ambigua
    assert resultado.criterio == CRITERIO_PALAVRAS, "reporta o empate MAIS PRECISO"

    identidades_empatadas = {IDENTIDADES[k] for k in resultado.ambiguidade}
    assert identidades_empatadas == {DES, DSR}


def test_e_a_ambiguidade_relatada_e_a_do_criterio_mais_preciso():
    """Quem le a mensagem precisa saber onde a duvida nasceu, nao onde ela parou."""
    resultado = buscar_certificado("ASSESSORIA", IDENTIDADES)

    assert resultado.criterio != CRITERIO_APROXIMACAO
