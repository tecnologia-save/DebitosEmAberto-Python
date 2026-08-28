"""A SEMANTICA das oito colunas de cada aba de detalhe.

Escrito ANTES de qualquer mudanca da fatia 14A.1 e commitado antes dela.

A 14A fechou a aba 'Empresas' e deixou um residual: das oito colunas de
'Processos Fiscais', so seis sao conferidas. As duas de fora divergiam entre o
codigo e o `PLANILHA MODELO.xlsx`, e eu me recusei a asserir o que as fontes
nao sustentavam.

Esta fatia rastreia cada valor ate quem o produz. Nao ha voto entre artefatos:
quem decide o que uma coluna significa e o dado que cai nela.

Nenhum teste abre navegador, contata o portal ou usa dado de cliente. As
sentinelas nomeiam o proprio campo, para que a coluna onde elas caem seja a
resposta.
"""
import pathlib

import openpyxl
import pytest
from planilhas_sinteticas import ALFA, ler_aba

from automation import consulta_fiscal, planilha

RAIZ = pathlib.Path(__file__).resolve().parent.parent
MODELO = RAIZ / "PLANILHA MODELO.xlsx"
CERT_ALFA = "CERT ALFA"

CABECALHO_EMPRESAS = ["CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS",
                      "PROCESSOS FISCAIS"]


def montar(caminho, extras=()):
    """Uma planilha valida, com as abas de detalhe que o teste pedir."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Empresas"
    ws.append(CABECALHO_EMPRESAS)
    ws.append([ALFA[0], ALFA[1], CERT_ALFA, "", ""])
    for nome, cabecalho, corpo in extras:
        outra = wb.create_sheet(nome)
        outra.append(list(cabecalho))
        for linha in corpo:
            outra.append(list(linha))
    wb.save(caminho)
    wb.close()
    return str(caminho)


def cabecalho_do_modelo(nome):
    wb = openpyxl.load_workbook(MODELO)
    try:
        return list(next(wb[nome].iter_rows(max_row=1, values_only=True)))
    finally:
        wb.close()


@pytest.fixture
def sessao():
    s = planilha.SessaoPlanilha()
    yield s
    s.descartar()


# Cada valor diz o nome do proprio campo. Onde ele cair, e o que aquela coluna
# significa na pratica.
def debito_marcado(cnpj=ALFA[0]):
    return {campo: f"<{campo}>" for campo in planilha.CAMPOS_DEBITO} | {"cnpj": cnpj}


def processo_marcado(cnpj=ALFA[0]):
    return {campo: f"<{campo}>" for campo in planilha.CAMPOS_PROCESSO} | {"cnpj": cnpj}


# ── §2 · o mapa das oito colunas de 'Débitos' ────────────────────────────────

def test_2_a_ordem_dos_campos_de_debito_e_a_do_cabecalho(tmp_path, sessao):
    """Posicao a posicao: qual CAMPO cai sob qual CABECALHO.

    Nao e teste de comprimento: cada valor carrega o nome do proprio campo.
    """
    caminho = montar(tmp_path / "p.xlsx")
    sessao.abrir(caminho)

    sessao.anexar_debitos([debito_marcado()])
    sessao.gravar()

    linhas = ler_aba(caminho, "Débitos")
    sob = dict(zip(linhas[0], linhas[1], strict=True))

    assert sob == {
        "CNPJ": ALFA[0],
        "TIPO": "<tipo>",
        "TRIBUTO": "<tributo>",
        "Rec.": "<receita>",
        "PA/Ex.": "<pa_ex>",
        "Dt.Vcto.": "<dt_vcto>",
        "Valor Original": "<valor_original>",
        "Saldo Devedor": "<saldo>",
    }


def test_2_e_o_produtor_de_cada_campo_de_debito_esta_no_extrator():
    """A origem, no JS que le a tabela do DCTFWeb. Nao ha aqui uma inferencia
    pelo nome da variavel: os rotulos estao no proprio portal."""
    fonte = consulta_fiscal.__file__
    codigo = pathlib.Path(fonte).read_text(encoding="utf-8")
    extrator = codigo[codigo.index("def _linhas_da_tabela_dctfweb"):]
    extrator = extrator[: extrator.index("\ndef ")]

    assert "if (labelText === 'Tributo')              tributo" in extrator
    assert "if (labelText === 'Valor original (R$)')  valor_original" in extrator
    assert "const tdReceita = tds.find(td => td.classList.contains('text-nowrap'))" \
        in extrator
    assert "const pa_ex   = tdsSimples[0]" in extrator
    assert "const dt_vcto = tdsSimples[1]" in extrator
    assert "cnpj, tipo, tributo, receita," in extrator


def test_2_e_o_modelo_concorda_nas_oito_primeiras_de_debitos():
    """As tres fontes — cabecalho do codigo, ordem dos campos e modelo — dizem a
    mesma coisa. A nona coluna do modelo o codigo nunca escreve."""
    modelo = cabecalho_do_modelo("Débitos")

    assert modelo[:8] == planilha.CABECALHO_DEBITOS
    assert len(modelo) == 9
    assert modelo[8] == "Informações Complementares"


# ── §3 · o mapa das oito colunas de 'Processos Fiscais' ──────────────────────

def test_3_a_ordem_dos_campos_de_processo_e_a_do_cabecalho(tmp_path, sessao):
    """A mesma prova, e e nela que a oitava posicao aparece: `processo_credito`
    cai sob o cabecalho que o CODIGO escreve."""
    caminho = montar(tmp_path / "p.xlsx")
    sessao.abrir(caminho)

    sessao.anexar_processos([processo_marcado()])
    sessao.gravar()

    linhas = ler_aba(caminho, "Processos Fiscais")
    sob = dict(zip(linhas[0], linhas[1], strict=True))

    assert sob == {
        "CNPJ": ALFA[0],
        "TIPO": "<tipo>",
        "RECEITA": "<receita>",
        "PA/Ex.": "<pa_ex>",
        "Dt.Vcto.": "<dt_vcto>",
        "Valor Original": "<valor_original>",
        "Saldo Devedor": "<saldo>",
        "Processo de Crédito": "<processo_credito>",
    }


# ── §4 · o rastro do OITAVO valor ────────────────────────────────────────────

def test_4_o_oitavo_valor_vem_do_PROCESSO_DE_CREDITO_do_portal():
    """A prova da semantica, no produtor.

    O valor sai de um elemento que o portal so revela ao clicar num botao cujo
    `aria-label` e 'Expandir processo de crédito', e o elemento revelado e
    `div.processo-credito`. Nao ha ambiguidade possivel: nem o botao, nem o
    div, nem a variavel falam de informacao complementar.
    """
    codigo = pathlib.Path(consulta_fiscal.__file__).read_text(encoding="utf-8")
    card = codigo[codigo.index("def _linhas_do_card"):]

    assert 'aria-label="Expandir processo de crédito"' in card
    assert "page.locator('div.processo-credito')" in card
    assert "processo_credito = (div_proc.text_content() or \"\").strip()" in card


def test_4_e_ele_atravessa_ate_a_OITAVA_posicao():
    """Do extrator ate a celula, sem trocar de nome no caminho."""
    codigo = pathlib.Path(consulta_fiscal.__file__).read_text(encoding="utf-8")

    assert "_linhas_da_tabela_do_card(page, cnpj, processo_credito)" in codigo
    assert "valor_original, saldo, processo_credito," in codigo

    assert planilha.CAMPOS_PROCESSO[7] == "processo_credito"
    assert planilha.CABECALHO_PROCESSOS[7] == "Processo de Crédito"


def test_4_informacoes_complementares_e_o_nome_do_CARD_e_nao_de_uma_coluna():
    """E aqui esta, provavelmente, a origem do erro do modelo.

    A expressao EXISTE no portal — mas nomeia o botao que ABRE o card do
    processo fiscal, aquele que lista os debitos dele. Nao nomeia campo nenhum
    dentro da tabela.

    Sao dois botoes diferentes, em dois momentos diferentes:

        'Expandir informações complementares do processo fiscal'  -> abre o card
        'Expandir processo de crédito'                            -> da o valor

    O valor da oitava coluna sai do segundo. Quem copiou o rotulo do primeiro
    para o cabecalho pegou o nome da tela, e nao o do dado.
    """
    codigo = pathlib.Path(consulta_fiscal.__file__).read_text(encoding="utf-8")

    assert codigo.count("informações complementares") == 1
    rotulo_do_card = ("Expandir informações complementares do "
                      "processo fiscal")
    assert f'aria-label="{rotulo_do_card}"' in codigo
    assert "CARD_PROCESSO" in codigo

    # E nenhum CAMPO se chama assim: os oito de cada aba estao nomeados, e
    # nenhum deles e informacao complementar.
    assert "informac" not in "".join(planilha.CAMPOS_PROCESSO)
    assert "informac" not in "".join(planilha.CAMPOS_DEBITO)


# ── §7 · a coluna 5, e a inconsistencia do proprio modelo ────────────────────

def test_7_as_duas_grafias_da_coluna_5_e_de_onde_vem_cada_uma():
    """O codigo escreve 'Dt.Vcto.'. O modelo traz 'Dt.Vcto.' na aba de Débitos e
    'Dt. Vcto' na de Processos Fiscais — ele discorda de SI MESMO, e e isso que
    torna a segunda forma um erro de digitacao, e nao um contrato."""
    debitos = cabecalho_do_modelo("Débitos")
    processos = cabecalho_do_modelo("Processos Fiscais")

    assert planilha.CABECALHO_DEBITOS[5] == "Dt.Vcto."
    assert planilha.CABECALHO_PROCESSOS[4] == "Dt.Vcto."

    assert debitos[5] == "Dt.Vcto."
    assert processos[4] == "Dt. Vcto"


def test_7_e_a_coluna_7_diverge_so_por_ESPACO():
    """Essa a 14A ja resolve: espaco e normalizado."""
    processos = cabecalho_do_modelo("Processos Fiscais")

    assert processos[6] == "Saldo Devedor "
    assert processos[6].strip() == planilha.CABECALHO_PROCESSOS[6]


# ── §10 · o residual que a 14A deixou aberto ─────────────────────────────────

def test_10_hoje_seis_colunas_certas_bastam_para_o_append_passar(tmp_path,
                                                                  sessao):
    """O buraco, reproduzido: a oitava coluna diz outra coisa, a validacao nao
    olha para ela, e `processo_credito` e gravado sob o cabecalho errado."""
    cabecalho_alheio = [*planilha.CABECALHO_PROCESSOS[:7], "OUTRA COISA"]
    caminho = montar(tmp_path / "p.xlsx",
                     extras=[("Processos Fiscais", cabecalho_alheio, [])])

    sessao.abrir(caminho)                       # passa
    sessao.anexar_processos([processo_marcado()])
    sessao.gravar()

    linhas = ler_aba(caminho, "Processos Fiscais")
    assert linhas[0][7] == "OUTRA COISA"
    assert linhas[1][7] == "<processo_credito>", "gravado sob outro significado"


def test_10_e_a_aba_do_MODELO_ATUAL_tem_exatamente_esse_problema(tmp_path,
                                                                 sessao):
    """E nao e um caso hipotetico: e o template que o projeto distribui."""
    caminho = montar(
        tmp_path / "p.xlsx",
        extras=[("Processos Fiscais", cabecalho_do_modelo("Processos Fiscais"),
                 [])],
    )

    sessao.abrir(caminho)                       # passa
    sessao.anexar_processos([processo_marcado()])
    sessao.gravar()

    linhas = ler_aba(caminho, "Processos Fiscais")
    assert linhas[0][7] == "Informações Complementares"
    assert linhas[1][7] == "<processo_credito>"


# ── §13 · o README, como ele esta ────────────────────────────────────────────

def test_13_o_README_descreve_um_formato_que_o_validador_RECUSA(tmp_path,
                                                                sessao):
    """Marcador estrutural minimo, e nao busca de prosa: monto a planilha que o
    README manda montar e mostro que ela nao passa.

    Quatro colunas, com a D chamada de 'RESULTADO'.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Empresas"
    ws.append(["CNPJ", "EMPRESA", "CERTIFICADO", "RESULTADO"])
    ws.append([ALFA[0], ALFA[1], CERT_ALFA, ""])
    caminho = str(tmp_path / "readme.xlsx")
    wb.save(caminho)
    wb.close()

    with pytest.raises(planilha.PlanilhaIndisponivel):
        sessao.abrir(caminho)
