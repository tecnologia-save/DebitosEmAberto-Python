"""CNPJ repetido na aba 'Empresas': quem le e quem escreve nao falam da mesma
linha.

Escrito ANTES de qualquer mudanca do Bloco B e commitado antes dela.

O defeito esta registrado desde a fatia 1 como PLANILHA_POSSIBLE_DEFECT e
sobreviveu porque nunca foi reproduzido. As tres pecas:

    mapa_status      varre a aba e guarda {cnpj: (D, E)} — a ULTIMA linha vence
    escrever_status  varre a aba e escreve na PRIMEIRA linha com aquele CNPJ
    ItemPendente     identifica-se pelo CNPJ; `posicao` e o indice do DataFrame
                     JA filtrado e reordenado, e nao a linha da planilha

Nenhum teste abre navegador, contata o portal ou usa dado de cliente.
"""
import pathlib

import openpyxl
import pytest
from planilhas_sinteticas import ALFA, BETA, ler_aba

from automation import planilha, status_portal

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CABECALHO = ["CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"]
CERT_A = "CERT ALFA"
CERT_B = "CERT BETA"


def montar(caminho, linhas):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Empresas"
    ws.append(CABECALHO)
    for linha in linhas:
        ws.append(list(linha))
    wb.save(caminho)
    wb.close()
    return str(caminho)


def pendentes(caminho, sessao):
    df, _ = planilha.ler_e_ordenar(caminho)
    df, _ = planilha.linhas_pendentes(df, sessao.mapa_status(caminho),
                                      status_portal.status_encerra_linha)
    return planilha.itens_pendentes(df)


@pytest.fixture
def sessao():
    s = planilha.SessaoPlanilha()
    yield s
    s.descartar()


# ── A · duas linhas do mesmo CNPJ ────────────────────────────────────────────

def test_a_duas_linhas_IDENTICAS_viram_UMA(tmp_path, sessao):
    """`ler_e_ordenar` chama `drop_duplicates`. Duas linhas iguais em TUDO
    colapsam — e a segunda linha da planilha fica sem ninguem para escrever
    nela.

    O numero de removidas volta da funcao, e o app o descarta: ninguem e
    avisado.
    """
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], ALFA[1], CERT_A, "", ""),
        (ALFA[0], ALFA[1], CERT_A, "", ""),
    ])
    sessao.abrir(caminho)

    _df, removidas = planilha.ler_e_ordenar(caminho)

    assert removidas == 1
    assert len(pendentes(caminho, sessao)) == 1


def test_a_duas_linhas_DIFERENTES_viram_dois_itens(tmp_path, sessao):
    """Basta uma celula diferente — o nome da empresa, por exemplo — e as duas
    sobrevivem. E e aqui que o defeito aparece."""
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, "", ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, "", ""),
    ])
    sessao.abrir(caminho)

    itens = pendentes(caminho, sessao)

    assert [item.cnpj for item in itens] == [ALFA[0], ALFA[0]]


def test_a_e_as_DUAS_escritas_caem_na_MESMA_linha(tmp_path, sessao):
    """`escrever_status` para na primeira ocorrencia. A segunda linha nunca e
    marcada, e volta pendente em toda execucao futura — FIRST WRITE."""
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, "", ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, "", ""),
    ])
    sessao.abrir(caminho)

    sessao.escrever_status(ALFA[0], planilha.STATUS_CONCLUIDO,
                           planilha.COL_STATUS_DCTFWEB)
    sessao.escrever_status(ALFA[0], planilha.STATUS_SEM_PROCESSOS,
                           planilha.COL_STATUS_PROCESSOS)
    sessao.gravar()

    linhas = ler_aba(caminho, "Empresas")
    assert linhas[1][3:5] == [planilha.STATUS_CONCLUIDO,
                              planilha.STATUS_SEM_PROCESSOS]
    assert linhas[2][3:5] == [None, None], "a segunda linha nao foi tocada"


# ── B · primeira preenchida, segunda vazia ───────────────────────────────────

def test_b_a_leitura_pega_a_ULTIMA_e_a_escrita_a_PRIMEIRA(tmp_path, sessao):
    """LAST READ. `mapa_status` sobrescreve a entrada a cada ocorrencia, entao
    o estado que sobra e o da ULTIMA linha — mesmo quando a primeira ja esta
    concluida."""
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, planilha.STATUS_CONCLUIDO,
         planilha.STATUS_SEM_PROCESSOS),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, "", ""),
    ])
    sessao.abrir(caminho)

    assert sessao.mapa_status(caminho)[ALFA[0]] == ("", ""), "venceu a ultima"


def test_b_e_a_linha_JA_CONCLUIDA_e_reprocessada_e_SOBRESCRITA(tmp_path, sessao):
    """A consequencia mais grave do defeito.

    A primeira linha ja estava concluida. Como o mapa le a ultima, o CNPJ volta
    pendente; como a escrita acha a primeira, o resultado novo entra POR CIMA do
    resultado antigo — e a linha que faltava continua vazia.
    """
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, planilha.STATUS_CONCLUIDO,
         planilha.STATUS_SEM_PROCESSOS),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, "", ""),
    ])
    sessao.abrir(caminho)

    assert len(pendentes(caminho, sessao)) == 2, "as duas voltaram pendentes"

    sessao.escrever_status(ALFA[0], planilha.STATUS_SEM_DEBITOS,
                           planilha.COL_STATUS_DCTFWEB)
    sessao.gravar()

    linhas = ler_aba(caminho, "Empresas")
    assert linhas[1][3] == planilha.STATUS_SEM_DEBITOS, "sobrescreveu a concluida"
    assert linhas[2][3] is None, "e a vazia continua vazia"


# ── C · primeira vazia, segunda preenchida ───────────────────────────────────

def test_c_a_segunda_CONCLUIDA_esconde_a_primeira_pendente(tmp_path, sessao):
    """O inverso: a ultima linha esta pronta, entao o mapa diz que o CNPJ
    terminou — e a primeira, que esta vazia, e considerada concluida e NUNCA e
    processada."""
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, "", ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, planilha.STATUS_CONCLUIDO,
         planilha.STATUS_SEM_PROCESSOS),
    ])
    sessao.abrir(caminho)

    assert pendentes(caminho, sessao) == [], "as duas sumiram da lista"

    linhas = ler_aba(caminho, "Empresas")
    assert linhas[1][3:5] == [None, None], "e a primeira ficou vazia para sempre"


# ── D · D e E diferentes entre as duas ───────────────────────────────────────

def test_d_estados_diferentes_nao_sobrevivem_ao_mapa(tmp_path, sessao):
    """Duas ocorrencias com progresso diferente colapsam num par so. O estado da
    primeira linha desaparece da visao da automacao."""
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, planilha.STATUS_CONCLUIDO, ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, "", planilha.STATUS_SEM_PROCESSOS),
    ])
    sessao.abrir(caminho)

    assert sessao.mapa_status(caminho)[ALFA[0]] == ("", "Sem Processos")


def test_d_e_a_RETOMADA_devolve_o_mesmo_par_para_as_duas(tmp_path, sessao):
    """`retomada` chama `mapa_status` pelo CNPJ. As duas ocorrencias recebem a
    mesma resposta, entao uma delas trabalha com o progresso da outra."""
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, planilha.STATUS_CONCLUIDO, ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, "", planilha.STATUS_SEM_PROCESSOS),
    ])
    sessao.abrir(caminho)

    retomada = sessao.retomada(caminho, ALFA[0],
                               status_portal.status_encerra_linha)

    assert retomada.dctfweb_feito is False, "mas a primeira linha ja tinha D"
    assert retomada.processos_feitos is True


# ── E · certificados diferentes na mesma repeticao ───────────────────────────

def test_e_cada_item_guarda_o_certificado_da_PROPRIA_linha(tmp_path, sessao):
    """Isto ja funciona: o certificado viaja no item, e nao e buscado por CNPJ.
    E o que a correcao nao pode quebrar."""
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, "", ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_B, "", ""),
    ])
    sessao.abrir(caminho)

    itens = pendentes(caminho, sessao)

    assert sorted(item.certificado for item in itens) == [CERT_A, CERT_B]


def test_e_mas_o_RESULTADO_dos_dois_cai_na_mesma_linha(tmp_path, sessao):
    """E por isso o certificado independente nao basta: a linha da MATRIZ pode
    receber o que foi consultado sob o certificado da FILIAL."""
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, "", ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_B, "", ""),
    ])
    sessao.abrir(caminho)

    sessao.escrever_status(ALFA[0], planilha.STATUS_SEM_DEBITOS,
                           planilha.COL_STATUS_DCTFWEB)
    sessao.gravar()

    linhas = ler_aba(caminho, "Empresas")
    assert linhas[1][2] == CERT_A
    assert linhas[1][3] == planilha.STATUS_SEM_DEBITOS
    assert linhas[2][3] is None


# ── F · a proxima execucao ───────────────────────────────────────────────────

def test_f_a_segunda_linha_volta_pendente_para_sempre(tmp_path, sessao):
    """O laco que o finding descreve: cada execucao reprocessa o CNPJ, escreve
    de novo na primeira linha, e a segunda continua chamando por trabalho."""
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, "", ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, "", ""),
    ])

    for _ in range(3):
        s = planilha.SessaoPlanilha()
        try:
            s.abrir(caminho)
            s.escrever_status(ALFA[0], planilha.STATUS_CONCLUIDO,
                              planilha.COL_STATUS_DCTFWEB)
            s.escrever_status(ALFA[0], planilha.STATUS_SEM_PROCESSOS,
                              planilha.COL_STATUS_PROCESSOS)
            s.gravar()
        finally:
            s.descartar()

    sessao.abrir(caminho)
    assert len(pendentes(caminho, sessao)) == 2, "as duas, execucao apos execucao"


# ── B1 · a `posicao` de hoje NAO e a linha da planilha ───────────────────────

def test_b1_a_posicao_e_o_indice_do_DATAFRAME_ja_reordenado(tmp_path, sessao):
    """A identidade que existe hoje nao serve: `posicao` sai de
    `df.iterrows()` DEPOIS de `drop_duplicates`, `sort_values` e
    `reset_index`.

    Duas linhas ordenadas por certificado trocam de lugar, e a posicao passa a
    apontar para outra linha da planilha.
    """
    caminho = montar(tmp_path / "p.xlsx", [
        (BETA[0], "BETA FICTICIA", "ZZZ CERT", "", ""),
        (ALFA[0], "ALFA FICTICIA", "AAA CERT", "", ""),
    ])
    sessao.abrir(caminho)

    itens = pendentes(caminho, sessao)

    assert [item.posicao for item in itens] == [0, 1]
    assert itens[0].cnpj == ALFA[0], "a segunda linha da planilha virou posicao 0"


def test_b1_e_o_item_nao_carrega_a_linha_de_origem():
    """Os tres campos de `ItemPendente` hoje. Nenhum deles diz onde escrever."""
    campos = list(planilha.ItemPendente.__dataclass_fields__)

    assert campos == ["posicao", "cnpj", "certificado"]


# ── B6 · as abas de detalhe ──────────────────────────────────────────────────

def test_b6_o_detalhe_e_identificado_so_pelo_CNPJ(tmp_path, sessao):
    """Duas ocorrencias do mesmo CNPJ produzem linhas de detalhe
    indistinguiveis entre si — elas ficam agrupadas sob o mesmo documento."""
    from planilhas_sinteticas import linhas_de_debito

    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, "", ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_B, "", ""),
    ])
    sessao.abrir(caminho)

    sessao.anexar_debitos(linhas_de_debito(ALFA[0], 1))
    sessao.anexar_debitos(linhas_de_debito(ALFA[0], 1))
    sessao.gravar()

    linhas = ler_aba(caminho, "Débitos")
    assert len(linhas) == 3, "cabecalho + duas linhas"
    assert linhas[1][0] == linhas[2][0] == ALFA[0]
    assert planilha.CABECALHO_DEBITOS[0] == "CNPJ", "e nao ha coluna de origem"
