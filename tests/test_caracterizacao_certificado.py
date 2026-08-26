"""Caracterizacao da regra de match de certificado COMO ELA E HOJE.

Escrito ANTES de extrair. Nenhum teste toca o Windows, o registro, o PowerShell
ou o Certificate Store: a lista de certificados chega pronta, em memoria.

Corpus 100% ficticio — nenhum nome, empresa, CNPJ ou CN real.

As assercoes sao sobre a IDENTIDADE do certificado escolhido (`subject_cn`), e nao
sobre a chave devolvida. O motivo esta em `test_a_chave_devolvida_depende_da_ordem`:
o mesmo certificado e indexado sob varias chaves, e qual delas volta depende da
ordem de iteracao de um `set`. A identidade e estavel; a chave nao.
"""
import io
from contextlib import redirect_stdout

import pytest
from casos_certificado import CERTS, CERTS_UM_SO

import main
from automation import certificados_windows, eventos
from automation.domain import buscar_certificado


def resolver(nome, certs=CERTS):
    """Devolve (identidade_do_certificado, resultado da regra).

    CHARACTERIZATION_TARGET_CHANGE (fatia 9B): `main._buscar_certificado` era um
    adapter que chamava a regra e IMPRIMIA o criterio e os candidatos ambiguos.
    Ele saiu com a orquestracao. As mesmas afirmacoes passam a ser feitas sobre o
    RESULTADO — que ja carregava tudo o que o print carregava, e de forma
    estruturada.
    """
    resultado = buscar_certificado(nome, certificados_windows.identidades(certs))
    identidade = certs[resultado.chave]["subject_cn"] if resultado.resolvida else None
    return identidade, resultado


ALVORADA = "ALVORADA COMERCIO:00000000000001"
BERNARDO = "BERNARDO TEIXEIRA MONTENEGRO SOUSA:00000000001"
XYZ = "X Y Z CONSULTORIAS:00000000000002"
FICTICIA = "EMPRESARIAL FICTICIA LTDA:00000000000003"
DES = "D&S ASSESSORIA:00000000000004"


# ── Os seis criterios, um caso explicito para cada ────────────────────────────

@pytest.mark.parametrize(
    ("criterio", "nome", "esperado"),
    [
        ("1 igualdade exata", "ALVORADA COMERCIO", ALVORADA),
        ("2 inicio do nome", "Bernardo", BERNARDO),
        ("3 palavras inteiras", "Montenegro", BERNARDO),
        ("4 inicio sem separadores", "XYZ", XYZ),
        ("5 correspondencia parcial", "D&S", DES),
        ("6 aproximacao (difflib)", "ALVORADO COMERCIO", ALVORADA),
    ],
)
def test_os_seis_criterios(criterio, nome, esperado):
    assert resolver(nome)[0] == esperado


def test_o_criterio_usado_e_reportado():
    """A mensagem diz QUAL criterio resolveu — util quando o match surpreende."""
    _, r = resolver("Montenegro")
    assert "palavras inteiras" in r.criterio


# ── Normalizacao ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "nome",
    [
        "ALVORADA COMERCIO",
        "alvorada comercio",
        "Alvorada Comercio",
        "alvorada comércio",
        "  ALVORADA COMERCIO  ",
    ],
)
def test_caixa_acento_e_espaco_nao_importam(nome):
    assert resolver(nome)[0] == ALVORADA


def test_pontuacao_e_ignorada_na_tokenizacao():
    assert resolver("BERNARDO!!!")[0] == BERNARDO
    assert resolver("x-y-z")[0] == XYZ


def test_nome_completo_do_certificado_resolve():
    assert resolver("bernardo teixeira montenegro sousa")[0] == BERNARDO


# ── Palavra isolada em qualquer posicao ───────────────────────────────────────

@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("Z", XYZ),
        ("CONSULTORIAS", XYZ),
        ("LTDA", FICTICIA),
        ("FICTICIA", FICTICIA),
        ("EMPRESARIAL", FICTICIA),
    ],
)
def test_palavra_isolada_resolve_quando_e_unica(nome, esperado):
    assert resolver(nome)[0] == esperado


# ── Sem match ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nome", ["nao existe nada", "", "   ", "ASSESSORIA FICTICIA"])
def test_sem_candidato_devolve_none(nome):
    assert resolver(nome)[0] is None


def test_lista_de_certificados_vazia():
    assert resolver("qualquer", {})[0] is None


# ── Ambiguidade: a regra de seguranca ─────────────────────────────────────────

def test_ambiguidade_recusa_em_vez_de_escolher():
    """'D' e o inicio de dois certificados diferentes. A regra NAO escolhe."""
    identidade, r = resolver("D")
    assert identidade is None
    assert r.ambigua is True


def test_a_mensagem_de_ambiguidade_lista_os_candidatos():
    _, r = resolver("D")
    assert len(r.ambiguidade) == 2
    assert "identifique só um deles" in main._frase(
        eventos.EventoOperacional(eventos.CERTIFICADO_AMBIGUO, posicao=0,
                                  quantidade=len(r.ambiguidade))
    ), "a orientação ao operador sobreviveu, agora no adapter de apresentação"


def test_o_mesmo_certificado_em_varias_chaves_nao_e_ambiguidade():
    """Cada certificado e indexado sob FriendlyName, CN e CN sem documento.

    Sem a deduplicacao por identidade, um nome parcial casaria em tres chaves do
    MESMO certificado e viraria empate sem motivo.
    """
    assert len(CERTS_UM_SO) >= 2, "o corpus precisa ter o mesmo cert sob varias chaves"
    assert resolver("Bernardo", CERTS_UM_SO)[0] == BERNARDO


# ── CERTIFICATE_MATCH_POSSIBLE_DEFECT ─────────────────────────────────────────

def test_ambiguidade_no_criterio_aproximado_corrigida_na_fatia_1_1():
    """UNICO teste desta suite alterado apos a caracterizacao — e por necessidade
    logica, nao por conveniencia: ele afirmava um comportamento que a fatia 1.1
    mudou DE PROPOSITO (CERTIFICATE_MATCH_BEHAVIOR_CHANGE).

    O que ele afirmava ANTES, e que continua legivel no historico (cffa5bb e
    476a4ab): 'ASSESSORIA' e palavra inteira em DOIS certificados distintos, os
    criterios 3 e 5 empatavam, e o criterio 6 escolhia um dos dois em silencio,
    porque `get_close_matches(n=1)` nunca consegue empatar.

    O que ele afirma DEPOIS: a duvida levantada pelo criterio mais preciso vale, e
    a regra recusa em vez de escolher.
    """
    identidade, r = resolver("ASSESSORIA")

    assert identidade is None, "recusar e o comportamento seguro"
    assert r.ambigua is True
    assert len(r.ambiguidade) == 2


def test_defeito_nome_com_ponto_e_truncado():
    """CERTIFICATE_MATCH_POSSIBLE_DEFECT: a normalizacao usa `Path(nome).stem`,
    que corta tudo depois do ultimo ponto.

    'D.S.R. ASSESSORIA' vira 'd.s.r' — a palavra que distinguiria os dois
    certificados de assessoria e descartada antes de qualquer criterio rodar.
    """
    from pathlib import Path

    assert Path("D.S.R. ASSESSORIA").stem == "D.S.R"
    assert Path("ALVORADA COMERCIO LTDA.").stem == "ALVORADA COMERCIO LTDA"


def test_a_chave_devolvida_depende_da_ordem():
    """CERTIFICATE_MATCH_POSSIBLE_DEFECT: `carregar_certificados` monta os nomes
    num `set`, entao qual chave do mesmo certificado entra primeiro varia.

    A IDENTIDADE nunca varia — todas as chaves apontam para o mesmo dicionario —
    mas a chave devolvida, e portanto o nome impresso no log, pode mudar entre
    execucoes. Por isso esta suite afirma identidade, nao chave.
    """
    chaves_do_bernardo = {k for k, v in CERTS.items() if v["subject_cn"] == BERNARDO}
    assert len(chaves_do_bernardo) >= 2

    _, r = resolver("Bernardo")
    assert r.chave in chaves_do_bernardo


def test_o_defeito_do_diagnostico_que_imprimia_a_identidade_sumiu():
    """CERTIFICATE_MATCH_POSSIBLE_DEFECT #4 — RESOLVIDO pela migracao da 9B.

    O adapter antigo imprimia "Certificado 'X' resolvido para 'Y'", e o nome de
    um certificado carrega o nome da empresa ou da pessoa. Ele nao existe mais:
    o app le o RESULTADO, e o evento que chega ao console leva posicao e
    quantidade — nunca identidade.
    """
    saida = io.StringIO()
    with redirect_stdout(saida):
        _, r = resolver("Bernardo")

    assert saida.getvalue() == "", "a regra nao imprime, e ninguem imprime por ela"
    assert r.chave is not None or r.ambigua

    evento = eventos.EventoOperacional(eventos.CERTIFICADO_AMBIGUO, posicao=0, quantidade=2)
    assert "BERNARDO" not in (main._frase(evento) or "").upper()
