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


def test_3_o_README_deixou_de_divergir_do_modelo():
    """ANTES documentava quatro colunas e chamava a D de 'RESULTADO'; o modelo
    tem cinco e as chama de 'DÉBITOS' e 'PROCESSOS FISCAIS'.

    A prova de que ele passou a descrever um formato ACEITO esta em
    `test_caracterizacao_schema_detalhe.py`, montando a planilha que a tabela
    descreve. Aqui fica so a ausencia do rotulo antigo.
    """
    texto = (RAIZ / "README.md").read_text(encoding="utf-8")
    tabela = texto[texto.index("## Formato da Planilha"):]
    tabela = tabela[: tabela.index("## Execução")]

    assert "RESULTADO" not in tabela


def test_3_e_o_cabecalho_dos_DETALHES_agora_bate_nos_dois():
    """ANTES `Processos Fiscais` divergia na oitava: o modelo a chamava de
    'Informações Complementares' e o codigo escreve 'Processo de Crédito' ali.
    A fatia 14A.1 rastreou o valor ate o produtor e corrigiu o modelo.

    A NONA coluna de `Débitos` continua sendo do modelo e nao do codigo — a
    automacao nunca escreve nela.
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

    assert [str(valor).strip() for valor in processos] == \
        planilha.CABECALHO_PROCESSOS


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


def test_1_o_pre_voo_passou_a_olhar_o_cabecalho(tmp_path):
    """ANTES a aba existir bastava: o que houvesse dentro dela nao era
    verificado, e o pre-voo devolvia sem levantar nada."""
    caminho = montar(tmp_path / "p.xlsx",
                     ["QUALQUER", "COISA", "AQUI", "MESMO", "ASSIM"],
                     [("x", "y", "z", "w", "v")])

    with pytest.raises(planilha.PlanilhaIndisponivel):
        planilha.validar_recurso(caminho)


# ── §4 · o risco posicional, reproduzido ─────────────────────────────────────

def test_4a_a_coluna_C_com_OUTRA_semantica_e_RECUSADA(tmp_path, sessao):
    """A: alguem removeu a coluna EMPRESA.

    ANTES a automacao lia a posicao C, encontrava o status que estava em D, e
    saia procurando um certificado chamado 'Concluído'.
    """
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], CERT_ALFA, "Concluído", "")],
    )

    with pytest.raises(planilha.PlanilhaIndisponivel):
        sessao.abrir(caminho)


def test_4b_D_e_E_TROCADAS_sao_RECUSADAS(tmp_path, sessao):
    """B: o cabecalho diz que D e 'PROCESSOS FISCAIS' e E e 'DÉBITOS'.

    ANTES a automacao nao lia cabecalho e gravava o resultado do DCTFWeb na
    coluna dos processos. §23: agora nada e escrito.
    """
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "EMPRESA", "CERTIFICADO", "PROCESSOS FISCAIS", "DÉBITOS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, "", "")],
    )
    antes = ler_aba(caminho, "Empresas")

    with pytest.raises(planilha.PlanilhaIndisponivel):
        sessao.abrir(caminho)

    assert ler_aba(caminho, "Empresas") == antes


def test_4c_uma_coluna_INSERIDA_no_inicio_e_RECUSADA(tmp_path, sessao):
    """C: o defeito descrito na fatia 1.

    ANTES uma coluna nova antes do CNPJ fazia a automacao ler o numero da
    filial como CNPJ e o nome da empresa como certificado.
    """
    caminho = montar(
        tmp_path / "p.xlsx",
        ["FILIAL", "CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS",
         "PROCESSOS FISCAIS"],
        [("01", ALFA[0], ALFA[1], CERT_ALFA, "", "")],
    )

    with pytest.raises(planilha.PlanilhaIndisponivel):
        sessao.abrir(caminho)


def test_4c_e_o_CERTIFICADO_nao_e_mais_sobrescrito(tmp_path, sessao):
    """A outra metade de C, e a que importa: ANTES nao era so leitura errada,
    era MUTACAO errada — o status ia por cima do nome do certificado.

    §23: conteudo antes == conteudo depois.
    """
    caminho = montar(
        tmp_path / "p.xlsx",
        ["FILIAL", "CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS",
         "PROCESSOS FISCAIS"],
        [("01", ALFA[0], ALFA[1], CERT_ALFA, "", "")],
    )
    antes = ler_aba(caminho, "Empresas")

    with pytest.raises(planilha.PlanilhaIndisponivel):
        sessao.abrir(caminho)

    assert ler_aba(caminho, "Empresas") == antes
    assert sessao.wb is None, "a planilha recusada nem virou estado da sessao"


def test_4d_cabecalho_ERRADO_com_valores_plausiveis_e_RECUSADO(tmp_path,
                                                                sessao):
    """D: ANTES nada no arquivo denunciava o problema — os tipos continuavam
    parecendo certos, e a automacao processava.

    E o caso que so o cabecalho pega: pelo CONTEUDO, este arquivo e
    indistinguivel de um valido.
    """
    caminho = montar(
        tmp_path / "p.xlsx",
        ["DOCUMENTO", "RAZAO", "RESPONSAVEL", "COLUNA 4", "COLUNA 5"],
        [(BETA[0], BETA[1], "CERT BETA", "", "")],
    )

    with pytest.raises(planilha.PlanilhaIndisponivel):
        sessao.abrir(caminho)


# ── §14 · as abas de detalhe entram no mesmo risco ───────────────────────────

def test_14_a_aba_de_detalhe_INCOMPATIVEL_e_RECUSADA(tmp_path, sessao):
    """ANTES uma aba 'Débitos' com outro significado nas colunas recebia os
    valores assim mesmo — `anexar` so perguntava se o NOME existia, e o CNPJ ia
    parar sob 'OBSERVACAO'.

    §15: falha segura. Nao apaga, nao recria, nao renomeia, nao adapta.
    """
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, "", "")],
        extras=[("Débitos", ["OBSERVACAO", "RESPONSAVEL", "DATA"],
                 [("nota antiga", "fulano", "01/01/2020")])],
    )
    antes = ler_aba(caminho, "Débitos")

    with pytest.raises(planilha.PlanilhaIndisponivel):
        sessao.abrir(caminho)

    assert ler_aba(caminho, "Débitos") == antes, "a aba alheia ficou intacta"


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


# ── §5 · §6 · o que a validacao faz, e o que ela recusa fazer ────────────────

def test_5_a_validacao_NAO_procura_colunas(tmp_path, sessao):
    """§5: nada de auto-descoberta. A planilha tem as colunas certas com os
    nomes certos, so que em outras posicoes — e isso e recusa, e nao um convite
    a reordenar."""
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CERTIFICADO", "EMPRESA", "CNPJ", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(CERT_ALFA, ALFA[1], ALFA[0], "", "")],
    )

    with pytest.raises(planilha.PlanilhaIndisponivel):
        sessao.abrir(caminho)


def test_5_e_o_codigo_nao_tem_busca_aproximada():
    """A prova estrutural do §5 e do §6: nada de semelhanca, substring ou
    aproximacao no modulo."""
    fonte = (RAIZ / "automation" / "planilha.py").read_text(encoding="utf-8")
    codigo = chr(10).join(linha for linha in fonte.splitlines()
                          if not linha.lstrip().startswith("#"))

    for proibido in ("difflib", "SequenceMatcher", "get_close_matches",
                     "startswith(", "fuzz", "levenshtein"):
        assert proibido not in codigo


@pytest.mark.parametrize("variacao", [
    "  CNPJ  ",
    "cnpj",
    "CnPj",
    "CNPJ\n",
    "CNPJ",
])
def test_6_espaco_e_caixa_sao_normalizados(variacao, tmp_path, sessao):
    """§6: as duas normalizacoes que o proprio modelo demonstra — ele mistura
    CAIXA ALTA e Caixa de Titulo entre abas, e traz 'Saldo Devedor ' com espaco
    sobrando."""
    caminho = montar(
        tmp_path / "p.xlsx",
        [variacao, "EMPRESA", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, "", "")],
    )

    sessao.abrir(caminho)

    assert sessao.wb is not None


def test_6_e_ACENTO_nao_e_normalizado(tmp_path, sessao):
    """Nao ha evidencia de que 'DEBITOS' sem acento seja uma forma historica, e
    inventar a tolerancia seria inventar contrato. Registrado como escolha."""
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "EMPRESA", "CERTIFICADO", "DEBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, "", "")],
    )

    with pytest.raises(planilha.PlanilhaIndisponivel):
        sessao.abrir(caminho)


# ── §9 · o erro nao entrega dado nenhum ──────────────────────────────────────

def test_9_a_recusa_nao_carrega_conteudo_da_planilha(tmp_path, sessao):
    """§9: nem CNPJ, nem certificado, nem o cabecalho lido, nem o caminho — o
    nome do arquivo costuma ser o nome do cliente."""
    caminho = montar(
        tmp_path / "SEGREDO DO CLIENTE.xlsx",
        ["DOCUMENTO", "RAZAO", "RESPONSAVEL", "COLUNA 4", "COLUNA 5"],
        [(BETA[0], BETA[1], "CERT BETA", "", "")],
    )

    with pytest.raises(planilha.PlanilhaIndisponivel) as erro:
        sessao.abrir(caminho)

    texto = str(erro.value)
    for proibido in (BETA[0], BETA[1], "CERT BETA", "DOCUMENTO", "RAZAO",
                     "RESPONSAVEL", "SEGREDO", ".xlsx", str(tmp_path)):
        assert proibido not in texto


# ── §24 · a planilha correta continua funcionando ────────────────────────────

def test_24_o_MODELO_versionado_e_aceito():
    """O criterio mais direto que existe: o template que o README manda usar
    passa pela validacao."""
    planilha.validar_recurso(str(MODELO))


def test_24_o_ciclo_completo_continua_igual(tmp_path, sessao):
    """Le, marca D, marca E, cria as abas de detalhe, salva — e retoma."""
    from planilhas_sinteticas import linhas_de_debito, linhas_de_processo

    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, "", ""),
         (BETA[0], BETA[1], "CERT BETA", "", "")],
    )
    sessao.abrir(caminho)

    assert len(pendentes(caminho, sessao)) == 2

    sessao.registrar_debitos(ALFA[0], linhas_de_debito(ALFA[0], 2))
    sessao.registrar_processos(ALFA[0], linhas_de_processo(ALFA[0], 1))
    sessao.gravar()
    sessao.marcar_gravado()
    sessao.descartar()

    empresas = ler_aba(caminho, "Empresas")
    assert empresas[1][3] == planilha.STATUS_CONCLUIDO
    assert empresas[1][4] == planilha.STATUS_CONCLUIDO
    assert ler_aba(caminho, "Débitos")[0] == planilha.CABECALHO_DEBITOS
    assert len(ler_aba(caminho, "Débitos")) == 3

    # §12: a retomada ve so o que sobrou.
    outra = planilha.SessaoPlanilha()
    try:
        outra.abrir(caminho)
        assert [item.cnpj for item in pendentes(caminho, outra)] == [BETA[0]]
    finally:
        outra.descartar()


def test_24_e_as_abas_de_detalhe_CRIADAS_por_nos_sao_reabertas(tmp_path, sessao):
    """A validacao nao pode recusar o que a propria automacao escreveu na
    execucao anterior."""
    from planilhas_sinteticas import linhas_de_processo

    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, "", "")],
    )
    sessao.abrir(caminho)
    sessao.anexar_processos(linhas_de_processo(ALFA[0], 1))
    sessao.gravar()
    sessao.descartar()

    planilha.validar_recurso(caminho)
    sessao.abrir(caminho)

    assert sessao.wb is not None


def test_24_e_as_abas_de_detalhe_do_MODELO_tambem(tmp_path, sessao):
    """As do modelo tem uma coluna a mais em `Débitos` e espaco sobrando em
    `Saldo Devedor `. Sao validas: e o artefato que o projeto distribui."""
    modelo_debitos = ["CNPJ", "TIPO", "TRIBUTO", "Rec.", "PA/Ex.", "Dt.Vcto.",
                      "Valor Original", "Saldo Devedor",
                      "Informações Complementares"]
    modelo_processos = ["CNPJ", "TIPO", "RECEITA", "PA/Ex.", "Dt.Vcto.",
                        "Valor Original", "Saldo Devedor ",
                        "Processo de Crédito"]
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, "", "")],
        extras=[("Débitos", modelo_debitos, []),
                ("Processos Fiscais", modelo_processos, [])],
    )

    sessao.abrir(caminho)

    assert sessao.wb is not None


def test_24_e_a_aba_de_detalhe_do_MODELO_ANTIGO_e_RECUSADA(tmp_path, sessao):
    """PLANILHA_SCHEMA_FAIL_CLOSED, aplicado ao template anterior.

    Uma planilha montada a partir do modelo ANTES da fatia 14A.1 tem
    'Informações Complementares' na oitava coluna de `Processos Fiscais` — e o
    que a automacao grava ali e o processo de credito. Ela para, e quem opera
    corrige o cabecalho ou parte do modelo novo.
    """
    antigo = ["CNPJ", "TIPO", "RECEITA", "PA/Ex.", "Dt. Vcto", "Valor Original",
              "Saldo Devedor ", "Informações Complementares"]
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, "", "")],
        extras=[("Processos Fiscais", antigo, [])],
    )

    with pytest.raises(planilha.PlanilhaIndisponivel):
        sessao.abrir(caminho)


# ── §18 · o que continua fora de escopo ──────────────────────────────────────

def test_18_o_CNPJ_duplicado_continua_como_estava(tmp_path, sessao):
    """PLANILHA_POSSIBLE_DEFECT separado: `escrever_status` marca a PRIMEIRA
    linha e `mapa_status` guarda a ULTIMA. A validacao de estrutura nao mascara
    isso, e nao e para mascarar."""
    caminho = montar(
        tmp_path / "p.xlsx",
        ["CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS", "PROCESSOS FISCAIS"],
        [(ALFA[0], ALFA[1], CERT_ALFA, "", ""),
         (ALFA[0], ALFA[1], CERT_ALFA, "", "")],
    )
    sessao.abrir(caminho)

    sessao.escrever_status(ALFA[0], planilha.STATUS_CONCLUIDO,
                           planilha.COL_STATUS_DCTFWEB)
    sessao.gravar()

    linhas = ler_aba(caminho, "Empresas")
    assert linhas[1][3] == planilha.STATUS_CONCLUIDO
    assert linhas[2][3] in (None, ""), "a segunda linha continua sem marca"


# ── §8 · §10 · a validacao acontece ANTES de qualquer mutacao ────────────────

def test_8_o_app_recusa_antes_de_abrir_navegador(tmp_path, monkeypatch):
    """A cadeia inteira: `app.executar` para na abertura da planilha, e nem
    chega a descobrir certificado, nem a pedir policy, nem a abrir sessao."""
    from automation import app, boundary
    from automation.captcha import ConfigCaptcha

    caminho = montar(
        tmp_path / "p.xlsx",
        ["FILIAL", "CNPJ", "EMPRESA", "CERTIFICADO", "DÉBITOS",
         "PROCESSOS FISCAIS"],
        [("01", ALFA[0], ALFA[1], CERT_ALFA, "", "")],
    )
    antes = ler_aba(caminho, "Empresas")

    def nao_deveria(*_a, **_k):
        raise AssertionError("o app passou da planilha")

    monkeypatch.setattr(app.maquina, "garantir_policy_do_windows", nao_deveria)
    monkeypatch.setattr(app.maquina, "abrir_sessao", nao_deveria)
    monkeypatch.setattr(app.certificados_windows, "descobrir",
                        lambda: ({"cert alfa": {"subject_cn": "X:1", "serial": "1",
                                                "display": "CERT ALFA"}}, 0))

    entrada = boundary.montar_entrada({"planilha": caminho})

    with pytest.raises(planilha.PlanilhaIndisponivel):
        app.executar(entrada, ConfigCaptcha(api_key="ficticia"))

    assert ler_aba(caminho, "Empresas") == antes


def test_10_a_validacao_mora_em_UM_lugar_so():
    """§10: `runner`, `local` e `main` continuam sendo adapters irmaos. Nenhum
    deles tem regra de cabecalho — os tres chamam o mesmo pre-voo, e a
    conferencia em si vive na integracao da planilha."""
    for nome in ("runner.py", "local.py", "main.py"):
        fonte = (RAIZ / nome).read_text(encoding="utf-8")
        assert "CABECALHO" not in fonte
        assert "validar_schema" not in fonte
        assert "validar_recurso(entrada.planilha)" in fonte

    modulo = (RAIZ / "automation" / "planilha.py").read_text(encoding="utf-8")
    assert modulo.count("def validar_schema(") == 1
