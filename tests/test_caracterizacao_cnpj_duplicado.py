"""CNPJ repetido na aba 'Empresas': quem le e quem escreve nao falam da mesma
linha.

Escrito ANTES de qualquer mudanca do Bloco B e commitado antes dela.

O defeito estava registrado desde a fatia 1 como PLANILHA_POSSIBLE_DEFECT e
sobreviveu porque nunca fora reproduzido. As tres pecas, ANTES:

    mapa_status      guardava {cnpj: (D, E)} — a ULTIMA linha vencia
    escrever_status  procurava e escrevia na PRIMEIRA linha com aquele CNPJ
    ItemPendente     identificava-se pelo CNPJ; `posicao` era o indice do
                     DataFrame JA filtrado e reordenado

DEPOIS a identidade e a LINHA, carregada desde a leitura. Nao ha identificador
novo: o indice do DataFrame sempre foi a linha, e so precisava parar de ser
jogado fora a cada `reset_index`.

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

def test_a_duas_linhas_IDENTICAS_continuam_DUAS(tmp_path, sessao):
    """ANTES `ler_e_ordenar` chamava `drop_duplicates`: duas linhas iguais em
    TUDO colapsavam, e a segunda ficava sem ninguem para escrever nela. O numero
    de removidas voltava da funcao e o app o descartava — ninguem era avisado.

    Repetir nao e invalido (B2). Agora sao contadas, e nao removidas.
    """
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], ALFA[1], CERT_A, "", ""),
        (ALFA[0], ALFA[1], CERT_A, "", ""),
    ])
    sessao.abrir(caminho)

    _df, removidas = planilha.ler_e_ordenar(caminho)

    assert removidas == 1, "contadas, e para quem quiser relatar"
    assert len(pendentes(caminho, sessao)) == 2, "e as duas continuam a processar"


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


def test_a_cada_escrita_cai_na_SUA_linha(tmp_path, sessao):
    """ANTES `escrever_status` parava na primeira ocorrencia, e as duas escritas
    caiam na mesma linha — FIRST WRITE.

    B2: linha 2 escreve linha 2, linha 3 escreve linha 3.
    """
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, "", ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, "", ""),
    ])
    sessao.abrir(caminho)

    sessao.escrever_status(2, planilha.STATUS_CONCLUIDO,
                           planilha.COL_STATUS_DCTFWEB)
    sessao.escrever_status(3, planilha.STATUS_SEM_PROCESSOS,
                           planilha.COL_STATUS_PROCESSOS)
    sessao.gravar()

    linhas = ler_aba(caminho, "Empresas")
    assert linhas[1][3:5] == [planilha.STATUS_CONCLUIDO, None]
    assert linhas[2][3:5] == [None, planilha.STATUS_SEM_PROCESSOS]


def test_a_e_uma_linha_que_nao_existe_devolve_FALSE(tmp_path, sessao):
    """B3: se a linha nao esta la, nao se procura 'a parecida'. Quem chamou
    fica sabendo que nao gravou."""
    caminho = montar(tmp_path / "p.xlsx", [(ALFA[0], ALFA[1], CERT_A, "", "")])
    sessao.abrir(caminho)

    assert sessao.escrever_status(99, planilha.STATUS_CONCLUIDO,
                                  planilha.COL_STATUS_DCTFWEB) is False
    assert sessao.escrever_status(1, planilha.STATUS_CONCLUIDO,
                                  planilha.COL_STATUS_DCTFWEB) is False, "o cabecalho"
    assert sessao.sujo is False, "e nada foi alterado"


# ── B · primeira preenchida, segunda vazia ───────────────────────────────────

def test_b_cada_linha_tem_o_SEU_estado_no_mapa(tmp_path, sessao):
    """ANTES, LAST READ: `mapa_status` sobrescrevia a entrada a cada ocorrencia,
    e o estado que sobrava era o da ULTIMA linha — mesmo com a primeira ja
    concluida."""
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, planilha.STATUS_CONCLUIDO,
         planilha.STATUS_SEM_PROCESSOS),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, "", ""),
    ])
    sessao.abrir(caminho)

    mapa = sessao.mapa_status(caminho)

    assert mapa[2] == (planilha.STATUS_CONCLUIDO, planilha.STATUS_SEM_PROCESSOS)
    assert mapa[3] == ("", "")


def test_b_e_a_linha_JA_CONCLUIDA_nao_e_mais_reprocessada(tmp_path, sessao):
    """ANTES, a consequencia mais grave do defeito: a primeira linha ja estava
    concluida, o mapa lia a ultima, o CNPJ voltava pendente, e o resultado novo
    entrava POR CIMA do antigo — enquanto a linha que faltava continuava vazia.
    """
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, planilha.STATUS_CONCLUIDO,
         planilha.STATUS_SEM_PROCESSOS),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, "", ""),
    ])
    sessao.abrir(caminho)

    itens = pendentes(caminho, sessao)
    assert [item.linha for item in itens] == [3], "so a que falta"

    sessao.escrever_status(itens[0].linha, planilha.STATUS_SEM_DEBITOS,
                           planilha.COL_STATUS_DCTFWEB)
    sessao.gravar()

    linhas = ler_aba(caminho, "Empresas")
    assert linhas[1][3] == planilha.STATUS_CONCLUIDO, "a concluida ficou intacta"
    assert linhas[2][3] == planilha.STATUS_SEM_DEBITOS, "e a vazia foi preenchida"


# ── C · primeira vazia, segunda preenchida ───────────────────────────────────

def test_c_a_segunda_CONCLUIDA_nao_esconde_mais_a_primeira(tmp_path, sessao):
    """ANTES o inverso acontecia: a ultima linha estava pronta, o mapa dizia que
    o CNPJ terminara, e a primeira — vazia — era considerada concluida e nunca
    processada."""
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, "", ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, planilha.STATUS_CONCLUIDO,
         planilha.STATUS_SEM_PROCESSOS),
    ])
    sessao.abrir(caminho)

    itens = pendentes(caminho, sessao)

    assert [item.linha for item in itens] == [2], "a que falta e a primeira"


# ── D · D e E diferentes entre as duas ───────────────────────────────────────

def test_d_estados_diferentes_SOBREVIVEM_ao_mapa(tmp_path, sessao):
    """ANTES duas ocorrencias com progresso diferente colapsavam num par so, e o
    estado da primeira desaparecia da visao da automacao. B4."""
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, planilha.STATUS_CONCLUIDO, ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, "", planilha.STATUS_SEM_PROCESSOS),
    ])
    sessao.abrir(caminho)

    mapa = sessao.mapa_status(caminho)

    assert mapa[2] == (planilha.STATUS_CONCLUIDO, "")
    assert mapa[3] == ("", planilha.STATUS_SEM_PROCESSOS)


def test_d_e_a_RETOMADA_e_de_CADA_uma(tmp_path, sessao):
    """ANTES `retomada` consultava o mapa pelo CNPJ: as duas ocorrencias
    recebiam a mesma resposta, e uma trabalhava com o progresso da outra.

    B4: cada linha so refaz o que falta NELA.
    """
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, planilha.STATUS_CONCLUIDO, ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, "", planilha.STATUS_SEM_PROCESSOS),
    ])
    sessao.abrir(caminho)

    primeira = sessao.retomada(caminho, 2, status_portal.status_encerra_linha)
    segunda = sessao.retomada(caminho, 3, status_portal.status_encerra_linha)

    assert (primeira.dctfweb_feito, primeira.processos_feitos) == (True, False)
    assert (segunda.dctfweb_feito, segunda.processos_feitos) == (False, True)


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


def test_e_e_o_RESULTADO_de_cada_um_cai_na_SUA_linha(tmp_path, sessao):
    """ANTES o certificado independente nao bastava: a linha da MATRIZ podia
    receber o que fora consultado sob o certificado da FILIAL. B5."""
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, "", ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_B, "", ""),
    ])
    sessao.abrir(caminho)

    itens = pendentes(caminho, sessao)
    por_certificado = {item.certificado: item.linha for item in itens}

    sessao.escrever_status(por_certificado[CERT_B], planilha.STATUS_SEM_DEBITOS,
                           planilha.COL_STATUS_DCTFWEB)
    sessao.gravar()

    linhas = ler_aba(caminho, "Empresas")
    assert linhas[1][3] is None, "a linha do CERT ALFA nao foi tocada"
    assert linhas[2][2] == CERT_B
    assert linhas[2][3] == planilha.STATUS_SEM_DEBITOS


# ── F · a proxima execucao ───────────────────────────────────────────────────

def test_f_o_laco_ACABA(tmp_path, sessao):
    """ANTES: cada execucao reprocessava o CNPJ, escrevia de novo na primeira
    linha, e a segunda continuava chamando por trabalho — sem fim.

    Agora uma execucao que processa o que a lista pede termina o servico, e a
    seguinte nao encontra nada.
    """
    caminho = montar(tmp_path / "p.xlsx", [
        (ALFA[0], "MATRIZ FICTICIA", CERT_A, "", ""),
        (ALFA[0], "FILIAL FICTICIA", CERT_A, "", ""),
    ])

    restantes = []
    for _ in range(3):
        s = planilha.SessaoPlanilha()
        try:
            s.abrir(caminho)
            itens = pendentes(caminho, s)
            restantes.append(len(itens))
            for item in itens:
                s.escrever_status(item.linha, planilha.STATUS_CONCLUIDO,
                                  planilha.COL_STATUS_DCTFWEB)
                s.escrever_status(item.linha, planilha.STATUS_SEM_PROCESSOS,
                                  planilha.COL_STATUS_PROCESSOS)
            s.gravar()
        finally:
            s.descartar()

    assert restantes == [2, 0, 0], "duas na primeira execucao, e nada depois"


# ── B1 · a `posicao` de hoje NAO e a linha da planilha ───────────────────────

def test_b1_a_posicao_continua_OPACA_e_a_linha_e_a_identidade(tmp_path, sessao):
    """ANTES a unica coisa parecida com identidade era `posicao`, e ela nao
    servia: saia de `df.iterrows()` DEPOIS de `drop_duplicates`, `sort_values` e
    `reset_index`.

    Ela continua sendo a ordem na lista — e agora existe `linha`, que aponta
    para a planilha.
    """
    caminho = montar(tmp_path / "p.xlsx", [
        (BETA[0], "BETA FICTICIA", "ZZZ CERT", "", ""),
        (ALFA[0], "ALFA FICTICIA", "AAA CERT", "", ""),
    ])
    sessao.abrir(caminho)

    itens = pendentes(caminho, sessao)

    assert [item.posicao for item in itens] == [0, 1], "a ordem de trabalho"
    assert itens[0].cnpj == ALFA[0], "a ordenacao por certificado inverteu"
    assert [item.linha for item in itens] == [3, 2], "e a linha acompanha o dono"


def test_b1_e_o_item_carrega_a_linha_de_origem():
    """ANTES eram tres campos, e nenhum deles dizia onde escrever."""
    campos = list(planilha.ItemPendente.__dataclass_fields__)

    assert campos == ["posicao", "cnpj", "certificado", "linha"]


# ── B6 · as abas de detalhe ──────────────────────────────────────────────────

def test_b6_o_detalhe_continua_identificado_so_pelo_CNPJ(tmp_path, sessao):
    """B6, e a resposta e: agrupamento, e nao erro.

    Duas ocorrencias do mesmo CNPJ produzem linhas de detalhe indistinguiveis
    entre si. Isso NAO afeta correcao nem retomada: quem decide o que refazer
    sao as colunas D e E da aba 'Empresas', que agora sao por linha. O detalhe e
    saida acumulativa, e o documento e o que o operador usa para procurar nela.

    Separar as ocorrencias exigiria uma coluna nova na saida — mudanca de
    contrato de output, que nao e desta fase.
    """
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


# ── B7 · o criterio de fechamento, no nivel do app ───────────────────────────

class PlanilhaEspiaDeLinhas:
    """A `SessaoPlanilha` vista pelo app, registrando A LINHA de cada gravacao."""

    def __init__(self):
        self.escritas = []
        self.detalhes = []
        self.progresso = {}

    def escrever_status(self, linha, valor, coluna):
        self.escritas.append((linha, coluna, valor))
        d, e = self.progresso.get(linha, ("", ""))
        self.progresso[linha] = ((valor, e) if coluna == planilha.COL_STATUS_DCTFWEB
                                 else (d, valor))
        return True

    def anexar_debitos(self, dados):
        self.detalhes.append(("Débitos", len(dados)))
        return [10]

    def anexar_processos(self, dados):
        self.detalhes.append(("Processos", len(dados)))
        return [20]

    registrar_debitos = planilha.SessaoPlanilha.registrar_debitos
    registrar_processos = planilha.SessaoPlanilha.registrar_processos
    registrar_sem_debitos = planilha.SessaoPlanilha.registrar_sem_debitos
    registrar_sem_processos = planilha.SessaoPlanilha.registrar_sem_processos
    registrar_debitos_concluidos = planilha.SessaoPlanilha.registrar_debitos_concluidos

    def retomada(self, caminho, linha, encerra_linha):
        d, e = self.progresso.get(linha, ("", ""))
        return planilha.RetomadaDaLinha(
            dctfweb_feito=bool(d), processos_feitos=bool(e),
            encerrada=bool(encerra_linha(d)),
        )

    def precisa_gravar(self):
        return False

    def descartar(self):
        pass


def test_b7_o_app_grava_a_linha_do_ITEM_e_nao_a_do_CNPJ():
    """O criterio de fechamento, verificado onde o defeito doia: no app.

    Dois itens do mesmo CNPJ, linhas diferentes. Cada gravacao tem de citar a
    linha do proprio item.
    """
    from automation import app
    from automation.captcha import ConfigCaptcha

    espia = PlanilhaEspiaDeLinhas()
    execucao = app._Execucao(espia, "p.xlsx", ConfigCaptcha(api_key="x"), None)

    for linha in (2, 7):
        item = planilha.ItemPendente(posicao=0, cnpj=ALFA[0], certificado=CERT_A,
                                     linha=linha)
        execucao.registrar(0, "registrar_sem_debitos", item.posicao, item.linha)
        execucao.registrar(0, "registrar_sem_processos", item.posicao, item.linha)

    assert [linha for linha, _, _ in espia.escritas] == [2, 2, 7, 7]


def test_b7_e_a_retomada_de_cada_item_e_a_da_sua_linha():
    """B3: no retry, o progresso relido e o DAQUELA linha."""
    espia = PlanilhaEspiaDeLinhas()
    espia.progresso[2] = (planilha.STATUS_CONCLUIDO, "")

    da_linha_2 = espia.retomada("p.xlsx", 2, status_portal.status_encerra_linha)
    da_linha_7 = espia.retomada("p.xlsx", 7, status_portal.status_encerra_linha)

    assert da_linha_2.dctfweb_feito is True
    assert da_linha_7.dctfweb_feito is False


def test_b7_e_o_app_nao_manda_CNPJ_para_gravacao_nenhuma():
    """Prova estrutural: nenhuma chamada de gravacao no app passa `item.cnpj`.

    O CNPJ continua indo para o PORTAL e para as linhas de detalhe — que e onde
    ele significa alguma coisa.
    """
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")
    codigo = chr(10).join(linha for linha in fonte.splitlines()
                          if not linha.lstrip().startswith("#"))

    for metodo in ("registrar_sem_debitos", "registrar_sem_processos",
                   "registrar_debitos_nao_compensaveis",
                   "registrar_debitos_concluidos", "registrar_recusa_do_portal",
                   "registrar_debitos", "registrar_processos"):
        i = codigo.find(f'"{metodo}"')
        if i == -1:
            i = codigo.find(f"planilha.{metodo}(")
        assert i != -1, metodo
        trecho = codigo[i:i + 220]
        assert "item.linha" in trecho, metodo
