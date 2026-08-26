"""O seam da planilha: o que a aplicacao recebe, e o que ela deixa de saber.

Antes da 9B a orquestracao lia `df.iterrows()`, `df.columns[0]` e
`df.columns[2]`. Quem coordenava a automacao precisava conhecer pandas e a
posicao fisica das colunas. Estes testes travam o corte.

Nenhuma planilha de cliente: CNPJs, empresas e certificados ficticios.
"""
import ast
import pathlib

import pandas as pd
import pytest

from automation import planilha
from automation.planilha import ItemPendente, certificados_dos_itens, itens_pendentes

RAIZ = pathlib.Path(__file__).resolve().parents[1]
COLUNAS = ["CNPJ", "EMPRESA", "CERTIFICADO", "D", "E"]


def df_com(*linhas):
    return pd.DataFrame(
        [dict(zip(COLUNAS, linha, strict=False)) for linha in linhas], columns=COLUNAS
    )


# ── O que atravessa ───────────────────────────────────────────────────────────

def test_o_item_carrega_cnpj_certificado_e_posicao():
    itens = itens_pendentes(df_com(
        ("11111111000191", "ALFA FICTICIA", "CERT ALFA"),
        ("22222222000172", "BETA FICTICIA", "CERT BETA"),
    ))

    assert [i.cnpj for i in itens] == ["11111111000191", "22222222000172"]
    assert [i.certificado for i in itens] == ["CERT ALFA", "CERT BETA"]
    assert [i.posicao for i in itens] == [0, 1]


def test_o_item_nao_carrega_a_empresa():
    """A empresa esta na planilha e nao e usada pelo fluxo. Nao viaja."""
    item = itens_pendentes(df_com(("11111111000191", "ALFA FICTICIA", "CERT")))[0]

    assert "ALFA FICTICIA" not in str(item)
    assert not hasattr(item, "empresa")


def test_o_cnpj_chega_normalizado():
    itens = itens_pendentes(df_com(("11.111.111/0001-91", "ALFA", "CERT")))

    assert itens[0].cnpj == "11111111000191"


def test_o_sufixo_de_float_do_pandas_e_removido():
    """Celula numerica lida como texto vira '11111111000191.0'."""
    itens = itens_pendentes(df_com(("11111111000191.0", "ALFA", "CERT")))

    assert itens[0].cnpj == "11111111000191"


def test_o_certificado_chega_sem_espacos_em_volta():
    itens = itens_pendentes(df_com(("11111111000191", "ALFA", "  CERT ALFA  ")))

    assert itens[0].certificado == "CERT ALFA"


@pytest.mark.parametrize("celula", ["", "nan", "None", "00000000000000"])
def test_celula_que_nao_e_cnpj_produz_item_inutilizavel(celula):
    """O item existe — a linha continua contando na posicao — mas nao e usavel."""
    itens = itens_pendentes(df_com((celula, "ALFA", "CERT")))

    assert len(itens) == 1
    assert itens[0].utilizavel is False


def test_cnpj_de_verdade_e_utilizavel():
    assert itens_pendentes(df_com(("11111111000191", "ALFA", "CERT")))[0].utilizavel


def test_planilha_vazia_produz_lista_vazia():
    assert itens_pendentes(df_com()) == []


def test_o_item_e_imutavel():
    import dataclasses

    item = itens_pendentes(df_com(("11111111000191", "ALFA", "CERT")))[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.cnpj = "outro"


# ── O que NAO atravessa ───────────────────────────────────────────────────────

def test_a_posicao_nao_e_identidade():
    """PLANILHA_POSSIBLE_DEFECT preservado: o CNPJ repetido produz DOIS itens
    com posicoes diferentes, e a identidade continua sendo o CNPJ.

    Uma identidade por linha consertaria a duplicata por acidente. Nao e desta
    fatia — e a prova de que nao foi consertada e este teste.
    """
    itens = itens_pendentes(df_com(
        ("11111111000191", "ALFA", "CERT"),
        ("11111111000191", "ALFA", "CERT"),
    ))

    assert [i.posicao for i in itens] == [0, 1]
    assert itens[0].cnpj == itens[1].cnpj


def test_o_item_nao_carrega_o_estado_das_colunas_d_e_e():
    """O motivo e funcional, nao estetico.

    D e E mudam DURANTE a execucao — o DCTFWeb grava D antes de os Processos
    comecarem — e uma retentativa do mesmo CNPJ precisa ler o valor novo. Um
    item com D/E congelados no inicio da lista faria a retentativa refazer o
    DCTFWeb que ja tinha terminado.
    """
    campos = {c.name for c in __import__("dataclasses").fields(ItemPendente)}

    assert campos == {"posicao", "cnpj", "certificado"}


# ── O resumo por certificado ──────────────────────────────────────────────────

def test_o_resumo_conta_itens_por_certificado_na_ordem_de_aparicao():
    itens = itens_pendentes(df_com(
        ("11111111000191", "ALFA", "CERT ALFA"),
        ("22222222000172", "BETA", "CERT BETA"),
        ("33333333000153", "GAMA", "CERT ALFA"),
    ))

    assert certificados_dos_itens(itens) == [("CERT ALFA", 2), ("CERT BETA", 1)]


def test_resumo_de_lista_vazia():
    assert certificados_dos_itens([]) == []


# ── O corte, provado na fonte ─────────────────────────────────────────────────

def _corpo_de(nome: str) -> str:
    """CHARACTERIZATION_TARGET_CHANGE (9B): a orquestracao saiu de `main.py` e
    foi para `automation/app.py`. As afirmacoes seguem o codigo."""
    arvore = ast.parse((RAIZ / "automation" / "app.py").read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.FunctionDef) and no.name == nome:
            return ast.unparse(no)
    raise AssertionError(f"{nome} nao existe em automation/app.py")


@pytest.mark.parametrize("funcao", ["_percorrer", "_processar_item",
                                    "_consultar_situacao", "executar"])
@pytest.mark.parametrize("proibido", [
    "iterrows", "df.columns", "pd.Series", "col_cnpj", "col_cert", "dropna", "unique",
])
def test_a_orquestracao_nao_toca_mais_em_pandas(funcao, proibido):
    assert proibido not in _corpo_de(funcao)


def test_o_dataframe_morre_dentro_do_app_e_nao_atravessa_o_laco():
    """A conversao acontece em `executar`, e o laco so ve itens.

    Na primeira metade da 9B isto era uma divida: `main.processar` ainda recebia
    o DataFrame na assinatura. Com a leitura da planilha dentro do app, o pandas
    deixou de atravessar qualquer fronteira.
    """
    fora = _corpo_de("executar")
    assert "planilha.itens_pendentes(df)" in fora
    assert "_percorrer(execucao, itens)" in fora

    dentro = _corpo_de("_percorrer")
    for proibido in ("df", "DataFrame", "pd."):
        assert proibido not in dentro.replace("itens", "")


@pytest.mark.parametrize("funcao", ["_percorrer", "_processar_item",
                                    "_consultar_situacao", "executar"])
@pytest.mark.parametrize("proibido", [
    "COL_STATUS_DCTFWEB", "COL_STATUS_PROCESSOS", "ABA_DEBITOS", "ABA_PROCESSOS",
    "escrever_status", "anexar_debitos", "anexar_processos",
    "iter_rows", "sheetnames", "cell(", "openpyxl", "Workbook",
])
def test_a_orquestracao_nao_conhece_o_schema_fisico(funcao, proibido):
    """PLANILHA_SCHEMA_HIGH_RISK_INVARIANT continua aberto — a camada de planilha
    ainda conhece A/C/D/E, sheets e headers. O que mudou e que a orquestracao
    deixou de conhecer, o que reduz o alcance do invariant sem altera-lo.

    "Débitos" e "Processos Fiscais" aparecem em texto de console, e sao o nome
    do que o operador esta vendo — nao o nome da aba. Nao ha teste contra isso.
    """
    assert proibido not in _corpo_de(funcao)


def test_os_textos_de_status_ficam_na_planilha():
    """A orquestracao pede "registre que nao ha debitos"; o texto e da planilha."""
    for funcao in ("_consultar_situacao", "_processar_item", "_extrair_debitos",
                   "_extrair_processos"):
        corpo = _corpo_de(funcao)
        for texto in (planilha.STATUS_SEM_DEBITOS, planilha.STATUS_SEM_PROCESSOS,
                      planilha.STATUS_DEBITOS_NAO_COMPENSAVEIS):
            assert f"'{texto}'" not in corpo
