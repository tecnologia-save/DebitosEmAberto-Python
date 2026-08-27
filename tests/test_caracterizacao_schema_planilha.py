"""O contrato da planilha, como ele e hoje: POSICOES sem verificacao.

Escrito ANTES de qualquer mudanca da fatia 14A e commitado antes dela.

    A = CNPJ        C = certificado      D = status DCTFWeb    E = status Processos

As posicoes estao no codigo desde a fatia 1, com o defeito ao lado —
PLANILHA_POSSIBLE_DEFECT, "uma coluna a mais no inicio desloca tudo em
silencio". Este arquivo deixa de chamar isso de possivel: reproduz.

O que ja e seguro hoje, e precisa continuar sendo
-------------------------------------------------
A aba principal e escolhida por NOME — `sheet_name='Empresas'` na leitura e
`wb['Empresas']` na escrita. Nao ha `workbook.active` no caminho. O proprio
`PLANILHA MODELO.xlsx` demonstra isso: a aba ativa dele e 'Processos Fiscais',
e a automacao continua lendo 'Empresas'.

Nenhuma planilha de cliente entra aqui. Todos os CNPJs, nomes e certificados
sao ficticios.
"""
import pathlib

import openpyxl
import pytest
from planilhas_sinteticas import ALFA, BETA, ler_aba

from automation import planilha, status_portal

RAIZ = pathlib.Path(__file__).resolve().parent.parent
MODELO = RAIZ / "PLANILHA MODELO.xlsx"

CERT_ALFA = "CERT ALFA"


def montar(caminho, cabecalho, linhas, aba="Empresas", ativa=None, extras=()):
    """Um .xlsx sintetico com o cabecalho e as linhas que o teste quiser."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = aba
    ws.append(list(cabecalho))
    for linha in linhas:
        ws.append(list(linha))
    for nome, cab, corpo in extras:
        outra = wb.create_sheet(nome)
        outra.append(list(cab))
        for linha in corpo:
            outra.append(list(linha))
    if ativa is not None:
        wb.active = wb.sheetnames.index(ativa)
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


# ── §3 · a evidencia historica: o template versionado ────────────────────────

def test_3_o_modelo_versionado_existe_e_esta_vazio():
    """`PLANILHA MODELO.xlsx` e a evidencia do contrato — e o README manda usa-lo
    como base. Nenhuma linha de dado: nao ha CNPJ, empresa nem certificado real
    dentro dele."""
    assert MODELO.exists()

    wb = openpyxl.load_workbook(MODELO)
    try:
        for nome in wb.sheetnames:
            linhas = [linha for linha in wb[nome].iter_rows(min_row=2,
                                                            values_only=True)
                      if any(valor is not None for valor in linha)]
            assert linhas == [], nome
    finally:
        wb.close()


def test_3_o_modelo_prova_os_cabecalhos_de_A_a_E():
    """A evidencia que faltava para validar por nome, e nao por posicao nua."""
    wb = openpyxl.load_workbook(MODELO)
    try:
        cabecalho = next(wb["Empresas"].iter_rows(max_row=1, values_only=True))
    finally:
        wb.close()

    assert cabecalho == ("CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS",
                         "PROCESSOS FISCAIS")


def test_3_e_o_README_esta_DESATUALIZADO():
    """REPORTADO. O README documenta quatro colunas e chama a D de 'RESULTADO';
    o modelo tem cinco e as chama de 'DÉBITOS' e 'PROCESSOS FISCAIS'.

    Quando duas fontes discordam, a que vale e o artefato que a automacao le.
    """
    texto = (RAIZ / "README.md").read_text(encoding="utf-8")
    tabela = texto[texto.index("## Formato da Planilha"):]
    tabela = tabela[: tabela.index("## Execução")]

    assert "| D | RESULTADO |" in tabela
    assert "PROCESSOS FISCAIS" not in tabela, "a coluna E nem aparece"


def test_3_e_o_cabecalho_dos_DETALHES_bate_em_um_e_diverge_no_outro():
    """A outra metade da evidencia, e ela nao e limpa.

    `Débitos`: o modelo tem NOVE colunas e as oito primeiras sao exatamente as
    que o codigo escreve. A nona, 'Informações Complementares', o codigo nunca
    preenche.

    `Processos Fiscais`: o modelo tem oito, e a oitava e
    'Informações Complementares' — enquanto o codigo escreve
    'Processo de Crédito' ali. As duas fontes discordam sobre o que essa coluna
    significa.
    """
    wb = openpyxl.load_workbook(MODELO)
    try:
        debitos = next(wb["Débitos"].iter_rows(max_row=1, values_only=True))
        processos = next(wb["Processos Fiscais"].iter_rows(max_row=1,
                                                           values_only=True))
    finally:
        wb.close()

    assert list(debitos[:8]) == planilha.CABECALHO_DEBITOS
    assert debitos[8] == "Informações Complementares"

    assert processos[7] == "Informações Complementares"
    assert planilha.CABECALHO_PROCESSOS[7] == "Processo de Crédito"


# ── §1 · §2 · a aba principal e escolhida por NOME ───────────────────────────

def test_1_a_leitura_e_por_NOME_e_a_aba_ativa_nao_importa(tmp_path, sessao):
    """§2 e §21: nao ha `workbook.active` no caminho. Uma planilha com a aba
    errada ativa — como o proprio modelo — e lida corretamente."""
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, "", "")],
        extras=[("Processos Fiscais", planilha.CABECALHO_PROCESSOS, [])],
        ativa="Processos Fiscais",
    )
    sessao.abrir(caminho)

    itens = pendentes(caminho, sessao)

    assert [item.cnpj for item in itens] == [ALFA[0]]


def test_1_e_a_aba_AUSENTE_ja_falha_no_pre_voo(tmp_path):
    """O pre-voo da fatia 5B ja recusa a planilha sem a aba 'Empresas' — antes
    do navegador, e sem tocar em nada."""
    caminho = montar(tmp_path / "p.xlsx", ["CNPJ"], [], aba="OutraCoisa")

    with pytest.raises(planilha.PlanilhaIndisponivel):
        planilha.validar_recurso(caminho)


def test_1_o_pre_voo_de_hoje_NAO_olha_cabecalho_nenhum(tmp_path):
    """E o buraco. A aba existe, e o que ha dentro dela nao e verificado."""
    caminho = montar(tmp_path / "p.xlsx",
                     ["QUALQUER", "COISA", "AQUI", "MESMO", "ASSIM"],
                     [("x", "y", "z", "w", "v")])

    planilha.validar_recurso(caminho)     # passa


# ── §4 · o risco posicional, reproduzido ─────────────────────────────────────

def test_4a_a_coluna_C_com_OUTRA_semantica_vira_o_certificado(tmp_path, sessao):
    """A: alguem removeu a coluna EMPRESA. A automacao le a posicao C e encontra
    o status que estava em D — e sai procurando um certificado chamado
    'Concluído'."""
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], CERT_ALFA, "Concluído", "")],
    )
    sessao.abrir(caminho)

    itens = pendentes(caminho, sessao)

    assert [item.certificado for item in itens] == ["Concluído"], \
        "o nome do certificado passou a ser um status"


def test_4b_D_e_E_TROCADAS_sao_lidas_e_escritas_ao_contrario(tmp_path, sessao):
    """B: o cabecalho diz que D e 'PROCESSOS FISCAIS' e E e 'DÉBITOS'. A
    automacao nao le cabecalho: escreve o resultado do DCTFWeb na coluna dos
    processos."""
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "EMPRESA", "CERTIFICADO", "PROCESSOS FISCAIS", "DÉBITOS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, "", "")],
    )
    sessao.abrir(caminho)

    sessao.escrever_status(ALFA[0], planilha.STATUS_CONCLUIDO,
                           planilha.COL_STATUS_DCTFWEB)
    sessao.gravar()

    linhas = ler_aba(caminho, "Empresas")
    assert linhas[0][3] == "PROCESSOS FISCAIS"
    assert linhas[1][3] == "Concluído", "gravou na coluna dos PROCESSOS"


def test_4c_uma_coluna_INSERIDA_no_inicio_desloca_tudo(tmp_path, sessao):
    """C: o defeito descrito na fatia 1, agora reproduzido. Uma coluna nova
    antes do CNPJ e a automacao le a coluna errada em todas as quatro
    posicoes."""
    caminho = montar(
        tmp_path / "p.xlsx",
        ["FILIAL", "CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS",
         "PROCESSOS FISCAIS"],
        [("01", ALFA[0], ALFA[1], CERT_ALFA, "", "")],
    )
    sessao.abrir(caminho)

    itens = pendentes(caminho, sessao)

    assert [item.cnpj for item in itens] == ["00000000000001"], \
        "o numero da filial virou CNPJ"
    assert [item.certificado for item in itens] == [ALFA[1]], \
        "e o nome da empresa virou o certificado"


def test_4c_e_a_escrita_tambem_vai_para_a_coluna_errada(tmp_path, sessao):
    """A outra metade de C: nao e so leitura errada, e mutacao errada."""
    caminho = montar(
        tmp_path / "p.xlsx",
        ["FILIAL", "CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS",
         "PROCESSOS FISCAIS"],
        [("01", ALFA[0], ALFA[1], CERT_ALFA, "", "")],
    )
    sessao.abrir(caminho)

    assert sessao.escrever_status("00000000000001", planilha.STATUS_CONCLUIDO,
                                  planilha.COL_STATUS_DCTFWEB) is True
    sessao.gravar()

    linhas = ler_aba(caminho, "Empresas")
    assert linhas[0][3] == "CERTIFICADO", "a coluna D e a do certificado aqui"
    assert linhas[1][3] == "Concluído", "e o status foi escrito por cima dele"


def test_4d_cabecalho_ERRADO_com_valores_plausiveis_passa_batido(tmp_path,
                                                                 sessao):
    """D: nada no arquivo denuncia o problema — os tipos continuam parecendo
    certos, e a automacao processa."""
    caminho = montar(
        tmp_path / "p.xlsx",
        ["DOCUMENTO", "RAZAO", "RESPONSAVEL", "COLUNA 4", "COLUNA 5"],
        [(BETA[0], BETA[1], "CERT BETA", "", "")],
    )
    sessao.abrir(caminho)

    itens = pendentes(caminho, sessao)

    assert len(itens) == 1
    assert itens[0].utilizavel is True


# ── §14 · as abas de detalhe entram no mesmo risco ───────────────────────────

def test_14_a_aba_de_detalhe_EXISTENTE_recebe_append_sem_verificacao(tmp_path,
                                                                     sessao):
    """Uma aba 'Débitos' com outro significado nas colunas recebe os valores
    assim mesmo: `anexar` so pergunta se o NOME existe."""
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, "", "")],
        extras=[("Débitos", ["OBSERVACAO", "RESPONSAVEL", "DATA"],
                 [("nota antiga", "fulano", "01/01/2020")])],
    )
    sessao.abrir(caminho)

    from planilhas_sinteticas import linhas_de_debito

    sessao.anexar_debitos(linhas_de_debito(ALFA[0], 1))
    sessao.gravar()

    linhas = ler_aba(caminho, "Débitos")
    cabecalho_alheio = ["OBSERVACAO", "RESPONSAVEL", "DATA"]
    assert linhas[0][:3] == cabecalho_alheio, "o cabecalho alheio ficou"
    assert linhas[2][0] == ALFA[0], "e o CNPJ foi parar sob 'OBSERVACAO'"


def test_14_e_o_cabecalho_so_e_escrito_quando_a_aba_NAO_existe(tmp_path, sessao):
    """O outro lado: aba ausente e criada com o cabecalho do codigo."""
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, "", "")],
    )
    sessao.abrir(caminho)

    from planilhas_sinteticas import linhas_de_processo

    sessao.anexar_processos(linhas_de_processo(ALFA[0], 1))
    sessao.gravar()

    assert ler_aba(caminho, "Processos Fiscais")[0] == \
        planilha.CABECALHO_PROCESSOS


# ── §17 · planilha parcialmente processada e ESTRUTURA valida ────────────────

@pytest.mark.parametrize("valor_d, valor_e", [
    ("Concluído", ""),
    ("", "Sem Processos"),
    ("Concluído", "Sem Processos"),
    ("", ""),
])
def test_17_os_quatro_estados_de_progresso_sao_todos_validos(valor_d, valor_e,
                                                             tmp_path, sessao):
    """Estado de processamento nao e incompatibilidade. Os quatro tem de
    continuar abrindo."""
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, valor_d, valor_e)],
    )

    planilha.validar_recurso(caminho)
    sessao.abrir(caminho)

    assert sessao.mapa_status(caminho)[ALFA[0]] == (valor_d, valor_e)
