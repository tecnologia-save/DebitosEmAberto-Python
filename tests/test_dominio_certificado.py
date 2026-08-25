"""A regra de certificado extraida, com as mesmas expectativas da caracterizacao.

Os valores aqui sao os mesmos capturados do codigo original — ver
`test_caracterizacao_certificado.py`, que continua passando sem alteracao atraves
do adapter em main.py.

O que mudou e o formato do retorno: `ResultadoDaBusca` em vez de `str | None`, para
que as duas mensagens que a regra imprimia possam ser impressas por quem chama.
"""
import pytest
from casos_certificado import CERTS, CERTS_UM_SO

from automation.domain import (
    CRITERIO_APROXIMACAO,
    CRITERIO_EXATO,
    CRITERIO_INICIO,
    CRITERIO_INICIO_COMPACTO,
    CRITERIO_PALAVRAS,
    CRITERIO_PARCIAL,
    ResultadoDaBusca,
    buscar_certificado,
)

ALVORADA = "ALVORADA COMERCIO:00000000000001"
BERNARDO = "BERNARDO TEIXEIRA MONTENEGRO SOUSA:00000000001"
XYZ = "X Y Z CONSULTORIAS:00000000000002"
FICTICIA = "EMPRESARIAL FICTICIA LTDA:00000000000003"
DES = "D&S ASSESSORIA:00000000000004"


def identidades(certs=CERTS) -> dict[str, str]:
    """O mapa que a regra consome: chave normalizada -> identidade."""
    return {k: v["subject_cn"] for k, v in certs.items()}


def resolver(nome, certs=CERTS):
    disponiveis = identidades(certs)
    resultado = buscar_certificado(nome, disponiveis)
    ident = disponiveis[resultado.chave] if resultado.chave else None
    return ident, resultado


# ── Os seis criterios ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("nome", "esperado", "criterio"),
    [
        ("ALVORADA COMERCIO", ALVORADA, CRITERIO_EXATO),
        ("Bernardo", BERNARDO, CRITERIO_INICIO),
        ("Montenegro", BERNARDO, CRITERIO_PALAVRAS),
        ("XYZ", XYZ, CRITERIO_INICIO_COMPACTO),
        ("D&S", DES, CRITERIO_PARCIAL),
        ("ALVORADO COMERCIO", ALVORADA, CRITERIO_APROXIMACAO),
    ],
)
def test_os_seis_criterios(nome, esperado, criterio):
    ident, resultado = resolver(nome)
    assert ident == esperado
    assert resultado.criterio == criterio
    assert resultado.resolvida


# ── Normalizacao ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "nome",
    [
        "ALVORADA COMERCIO", "alvorada comercio", "Alvorada Comercio",
        "alvorada comércio", "  ALVORADA COMERCIO  ",
    ],
)
def test_caixa_acento_e_espaco_nao_importam(nome):
    assert resolver(nome)[0] == ALVORADA


def test_pontuacao_e_ignorada():
    assert resolver("BERNARDO!!!")[0] == BERNARDO
    assert resolver("x-y-z")[0] == XYZ


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [("Z", XYZ), ("CONSULTORIAS", XYZ), ("LTDA", FICTICIA), ("EMPRESARIAL", FICTICIA)],
)
def test_palavra_isolada_resolve_quando_e_unica(nome, esperado):
    assert resolver(nome)[0] == esperado


# ── Sem match ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nome", ["nao existe nada", "", "   ", "ASSESSORIA FICTICIA"])
def test_sem_candidato(nome):
    ident, resultado = resolver(nome)
    assert ident is None
    assert not resultado.resolvida
    assert not resultado.ambigua


def test_lista_vazia():
    assert buscar_certificado("qualquer", {}) == ResultadoDaBusca()


# ── Ambiguidade: a regra de seguranca ─────────────────────────────────────────

def test_ambiguidade_recusa_em_vez_de_escolher():
    ident, resultado = resolver("D")
    assert ident is None
    assert resultado.ambigua
    assert len(resultado.ambiguidade) == 2
    assert resultado.criterio == CRITERIO_INICIO


def test_o_resultado_carrega_o_que_o_chamador_precisa_imprimir():
    """Nenhuma mensagem se perdeu na extracao: criterio e candidatos vem no retorno."""
    _, resolvida = resolver("Montenegro")
    assert resolvida.chave and resolvida.criterio

    _, ambigua = resolver("D")
    assert ambigua.criterio and ambigua.ambiguidade
    assert ambigua.ambiguidade == tuple(sorted(ambigua.ambiguidade)), "ordenados"


def test_o_mesmo_certificado_em_varias_chaves_nao_e_ambiguidade():
    assert len(CERTS_UM_SO) >= 2
    assert resolver("Bernardo", CERTS_UM_SO)[0] == BERNARDO


def test_identidade_ausente_cai_para_a_propria_chave():
    """Preservado do original (`valor.get('subject_cn') or k`)."""
    resultado = buscar_certificado("alfa", {"alfa beta": "", "gama": "GAMA"})
    assert resultado.chave == "alfa beta"


def test_o_resultado_e_imutavel():
    import dataclasses

    _, resultado = resolver("Bernardo")
    with pytest.raises(dataclasses.FrozenInstanceError):
        resultado.chave = "outro"


# ── CERTIFICATE_MATCH_POSSIBLE_DEFECT preservados ─────────────────────────────

def test_defeito_difflib_desempata_silenciosamente():
    """O mais grave: 'ASSESSORIA' e palavra inteira em DOIS certificados, empata
    nos criterios 3 e 5, e o 6 escolhe um — porque `n=1` nunca empata."""
    ident, resultado = resolver("ASSESSORIA")
    assert ident == DES
    assert resultado.criterio == CRITERIO_APROXIMACAO
    assert not resultado.ambigua, "e o silencio que torna isso perigoso"


def test_defeito_nome_com_ponto_e_truncado():
    from automation.domain import normalizar_nome_certificado

    assert normalizar_nome_certificado("D.S.R. ASSESSORIA") == "d.s.r"
    assert normalizar_nome_certificado("ALVORADA COMERCIO LTDA.") == "alvorada comercio ltda"


def test_a_regra_nao_imprime(capsys):
    """MIGRATION_SEMANTIC_CHANGE menor: a regra imprimia; agora quem imprime e
    quem chama. A DECISAO e identica — so o canal do diagnostico mudou."""
    resolver("Bernardo")
    resolver("D")
    resolver("nada")
    assert capsys.readouterr().out == ""
