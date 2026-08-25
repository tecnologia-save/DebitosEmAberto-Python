"""Caracterizacao da classificacao anti-bot COMO ELA E HOJE.

Esta suite tem um problema que as anteriores nao tinham: a regra NAO E UMA
FUNCAO. Ela esta inline, duas vezes, dentro da navegacao — nao ha o que chamar.

A solucao adotada tem duas partes:

1. `_ORIGINAL` e a transcricao literal da condicao inline. A tabela-verdade e
   afirmada contra ela. Esta funcao NUNCA muda: ela e o registro do ANTES.
2. `test_a_transcricao_confere_com_o_codigo` le main.py e prova, por AST, que a
   transcricao e mesmo o que esta escrito nos dois pontos — senao a tabela-verdade
   estaria caracterizando uma invencao minha.

Depois da extracao, um terceiro teste compara a regra extraida com `_ORIGINAL`
caso a caso. E essa comparacao diferencial que prova equivalencia.

Nenhum teste abre navegador, portal ou sessao. Mensagens ficticias.
"""
import ast
import pathlib
import textwrap

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def _ORIGINAL(texto: str) -> bool:
    """Transcricao literal do que estava inline nos dois pontos de main.py.

    Original, verbatim:

        _err_lower = _err_msg.lower()
        if "automatizado" in _err_lower or "bloqueado" in _err_lower:

    Note o que NAO ha: remocao de acento. A classificacao de recusa aplicada ao
    MESMO texto remove acentos; esta nao.
    """
    baixo = texto.lower()
    return "automatizado" in baixo or "bloqueado" in baixo


# ── Tabela-verdade do comportamento original ─────────────────────────────────

@pytest.mark.parametrize(
    "mensagem",
    [
        "Detectamos acesso automatizado a este portal",
        "Acesso bloqueado",
        "ACESSO BLOQUEADO",
        "Acesso Automatizado",
        "AcEsSo AuToMaTiZaDo",
        "erro: acesso temporariamente bloqueado pelo sistema",
        "sistema automatizado detectado (cod. 0000)",
    ],
)
def test_texto_anti_bot_e_reconhecido(mensagem):
    """Caixa nao importa; a deteccao e por substring em qualquer posicao."""
    assert _ORIGINAL(mensagem) is True


@pytest.mark.parametrize(
    "mensagem",
    [
        "",
        "   ",
        "Erro interno do servidor",
        "Serviço temporariamente indisponível",
        "automatiza",
        "bloque",
        "bloqueio",
        "bloquear",
        "automatico",
        "automático",
        "automatizada",
        "automatizádo",
    ],
)
def test_texto_que_nao_e_anti_bot(mensagem):
    """Palavra parecida nao basta — e substring exata.

    "automatizada" (feminino) e "bloqueio"/"bloquear" NAO casam. "automatizádo"
    tampouco: esta regra nao remove acento, ao contrario da de recusa.
    """
    assert _ORIGINAL(mensagem) is False


def test_a_forma_feminina_escapa():
    """Registro explicito: "representação automatizada" nao seria reconhecida."""
    assert _ORIGINAL("Detectamos representação automatizada") is False
    assert _ORIGINAL("Detectamos acesso automatizado") is True


# ── Anti-bot vs recusa do CNPJ: a precedencia importa ────────────────────────

def test_anti_bot_e_recusa_sao_conjuntos_diferentes():
    """A pergunta de sucesso da fatia, em forma de teste."""
    import main

    anti_bot = "Detectamos acesso automatizado a este portal"
    recusa = "Procuração vencida"

    assert _ORIGINAL(anti_bot) and not main._erro_permanente(anti_bot)
    assert main._erro_permanente(recusa) and not _ORIGINAL(recusa)


def test_anti_bot_tem_precedencia_sobre_recusa():
    """No original, o `if` do anti-bot vem ANTES do de recusa. Um texto que casa
    nos dois e tratado como anti-bot — logo retentado, e nada e gravado na
    coluna D. Ver ANTIBOT_CLASSIFICATION_POSSIBLE_DEFECT."""
    import main

    ambos = "Acesso bloqueado: procuração vencida"

    assert _ORIGINAL(ambos) is True
    assert main._erro_permanente(ambos) is True, "casa nas duas regras"
    # a ordem no codigo decide: anti-bot ganha.


# ── ANTIBOT_CLASSIFICATION_POSSIBLE_DEFECT ───────────────────────────────────

@pytest.mark.parametrize(
    "mensagem",
    ["CNPJ bloqueado na base da Receita", "Certificado bloqueado", "Contribuinte bloqueado"],
)
def test_defeito_bloqueio_do_contribuinte_vira_anti_bot(mensagem):
    """ANTIBOT_CLASSIFICATION_POSSIBLE_DEFECT: "bloqueado" tambem descreve
    bloqueio DO CNPJ, que nao e anti-bot e nao melhora com retentativa.

    Consequencia caracterizada: 3 tentativas de representacao reaproveitando a
    sessao, depois navegador fechado, login refeito, mais uma rodada — e o CNPJ
    pulado sem nada gravado. Trabalho desperdicado, motivo real nunca registrado.
    """
    assert _ORIGINAL(mensagem) is True


def test_defeito_normalizacao_divergente_entre_as_duas_regras():
    """ANTIBOT_CLASSIFICATION_POSSIBLE_DEFECT: o MESMO texto passa por duas
    regras com normalizacoes diferentes — recusa remove acento, anti-bot nao."""
    import main

    assert main._erro_permanente("PROCURAÇÃO VENCIDA") is True, "remove acento"
    assert _ORIGINAL("BLOQUEÁDO") is False, "nao remove acento"


# ── Prova de que a transcricao e fiel ao codigo ──────────────────────────────

# Trecho de main.py congelado ANTES da extracao. E contra ELE que a fidelidade da
# transcricao e provada — assim estes testes continuam valendo depois da extracao,
# em vez de virarem cadaveres que precisam ser editados.
FONTE_ORIGINAL = (pathlib.Path(__file__).parent / "fonte_original_antibot.txt").read_text(
    encoding="utf-8"
)


def _blocos_originais() -> list[str]:
    """Os dois pontos inline, separados — cada um tem indentacao propria."""
    partes = FONTE_ORIGINAL.split("# ── ponto ")[1:]
    return [textwrap.dedent(p.partition(chr(10))[2]) for p in partes]


def _condicoes_antibot_no_fonte() -> list[ast.BoolOp]:
    """Todo `X in Y or Z in Y` do original que menciona as duas palavras."""
    achados = []
    for no in [n for b in _blocos_originais() for n in ast.walk(ast.parse(b))]:
        if not isinstance(no, ast.BoolOp) or not isinstance(no.op, ast.Or):
            continue
        literais = {
            v.value
            for cmp in no.values
            if isinstance(cmp, ast.Compare)
            for v in [cmp.left]
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        }
        if literais == {"automatizado", "bloqueado"}:
            achados.append(no)
    return achados


def test_a_transcricao_confere_com_o_codigo():
    """Sem esta prova, a tabela-verdade acima estaria caracterizando uma invencao.

    Confirma: sao DOIS pontos, ambos com a MESMA condicao, ambos sobre uma
    variavel — nunca sobre o texto cru — e sem nenhuma chamada de normalizacao
    dentro da propria condicao.
    """
    condicoes = _condicoes_antibot_no_fonte()

    assert len(condicoes) == 2, "os dois pontos inline mapeados na fatia"
    for cond in condicoes:
        assert len(cond.values) == 2
        for cmp in cond.values:
            assert isinstance(cmp.ops[0], ast.In)
            assert isinstance(cmp.comparators[0], ast.Name), "compara com variavel ja em minusculas"


def test_os_dois_pontos_usam_texto_ja_em_minusculas():
    """A normalizacao acontece uma linha antes, em `.lower()`, nos dois casos —
    e em nenhum deles ha remocao de acento."""
    assert FONTE_ORIGINAL.count("_err_lower = _err_msg.lower()") == 1
    assert FONTE_ORIGINAL.count("_epc_lower = _err_pos_captcha.lower()") == 1
    assert "remover_acentos" not in FONTE_ORIGINAL
