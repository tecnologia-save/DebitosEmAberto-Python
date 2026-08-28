"""A planilha: o recurso INPUT e OUTPUT da automacao.

Este e o UNICO modulo do projeto que conhece `openpyxl` e `pandas`. Fora daqui
ninguem manipula celula, indice de linha, aba ou DataFrame.

Por que a planilha e diferente de um arquivo de entrada comum
------------------------------------------------------------
Ela e lida no comeco E gravada durante a execucao — e o progresso gravado nela e
o que permite retomar depois de uma queda. INOUT_RESOURCE_CONTRACT.

RESUMABILITY_CONTRACT, o contrato que nao pode ser quebrado por conveniencia:

    - as colunas D e E da aba 'Empresas' sao o estado de progresso;
    - D = DCTFWeb, E = Processos Fiscais, INDEPENDENTES entre si;
    - uma linha so e considerada pronta com as DUAS preenchidas — ou com um
      status terminal em D, que dispensa a E;
    - o disco e tocado uma vez por CNPJ, num `finally`, e o arquivo recebido e
      sobrescrito no lugar: nao ha copia temporaria nem escrita atomica;
    - uma queda perde no maximo o CNPJ em andamento, que tambem nao chegou a ser
      marcado — entao a proxima execucao o refaz.

Trocar isso por "salva tudo no final", ou por escrita atomica em arquivo
temporario, DESTROI a retomada. Nao e detalhe de implementacao.

As abas de detalhe ('Debitos', 'Processos Fiscais') sao append-only e NAO
participam do contrato de retomada — ver PLANILHA_POSSIBLE_DEFECT no relatorio
da fatia 4.

Diagnostico fica de fora
------------------------
Nenhuma funcao daqui imprime. Quem chama decide o que registrar — a mesma
separacao usada na regra de certificado e na de status do portal. Nenhuma
mensagem de erro daqui carrega caminho, CNPJ, empresa ou conteudo de celula.
"""
from __future__ import annotations

import pathlib
import re
import zipfile
from dataclasses import dataclass
from typing import Any

import openpyxl
import pandas as pd
from openpyxl.styles import Alignment
from openpyxl.utils.exceptions import InvalidFileException

ABA_EMPRESAS = "Empresas"
ABA_DEBITOS = "Débitos"
ABA_PROCESSOS = "Processos Fiscais"

# Posicoes na aba 'Empresas', 0-based, como o codigo original as usa.
# PLANILHA_POSSIBLE_DEFECT: sao POSICOES, nao nomes — uma coluna a mais no inicio
# desloca tudo em silencio. Preservado.
COL_CNPJ = 0
COL_CERTIFICADO = 2
COL_STATUS_DCTFWEB = 3
COL_STATUS_PROCESSOS = 4

# A primeira linha de dados: a 1 e o cabecalho.
#
# E a ponte entre os dois mundos deste modulo. O pandas indexa a partir de zero
# a partir da linha 2 da planilha, entao `linha = indice + 2` — e e assim que a
# LINHA vira a identidade da unidade de trabalho.
#
# Ate aqui a identidade era o CNPJ, e isso quebrava quando ele se repetia:
# `mapa_status` guardava a ULTIMA ocorrencia e `escrever_status` marcava a
# PRIMEIRA. Uma linha ja concluida podia ser sobrescrita, outra podia nunca ser
# processada, e a segunda ocorrencia voltava pendente em toda execucao.
#
# Nao ha identificador novo, nem coluna tecnica: o indice do DataFrame ja era a
# linha, e so precisava parar de ser jogado fora a cada `reset_index`.
PRIMEIRA_LINHA_DE_DADOS = 2

# Os valores gravados nas colunas de status. Ficam aqui porque sao conteudo da
# planilha: a aplicacao pede "registre que nao ha debitos", nao escreve o texto.
STATUS_CONCLUIDO = "Concluído"
STATUS_SEM_DEBITOS = "Sem débitos"
STATUS_DEBITOS_NAO_COMPENSAVEIS = "Débitos não compensáveis"
STATUS_SEM_PROCESSOS = "Sem Processos"

# O CABECALHO da aba 'Empresas', nas quatro posicoes que o codigo usa.
#
# A evidencia e `PLANILHA MODELO.xlsx`, versionado no repositorio, vazio de
# dados e apontado pelo README como a base a usar. Nao foi inventado aqui.
#
# A coluna B ('EMPRESA') fica de fora de proposito: o codigo nunca a le, e
# exigi-la recusaria planilhas que hoje funcionam. Um deslocamento em B nao
# passa despercebido mesmo assim — ele empurra CERTIFICADO para a posicao D, e
# e a conferencia de C que o pega.
CABECALHO_EMPRESAS = {
    COL_CNPJ: "CNPJ",
    COL_CERTIFICADO: "CERTIFICADO",
    COL_STATUS_DCTFWEB: "DÉBITOS",
    COL_STATUS_PROCESSOS: "PROCESSOS FISCAIS",
}

CABECALHO_DEBITOS = [
    "CNPJ", "TIPO", "TRIBUTO", "Rec.", "PA/Ex.",
    "Dt.Vcto.", "Valor Original", "Saldo Devedor",
]
CABECALHO_PROCESSOS = [
    "CNPJ", "TIPO", "RECEITA", "PA/Ex.", "Dt.Vcto.",
    "Valor Original", "Saldo Devedor", "Processo de Crédito",
]

# TODAS as oito posicoes de cada aba de detalhe sao conferidas (fatia 14A.1).
# A automacao escreve oito valores; conferir seis deixava a possibilidade de o
# oitavo cair sob um cabecalho de outro significado.
#
# A oitava de `Processos Fiscais` foi decidida pelo PRODUTOR, e nao por voto
# entre artefatos. O valor sai de `div.processo-credito`, revelado por um botao
# cujo `aria-label` e "Expandir processo de crédito": e o processo de credito
# daquele card, e nada mais. O `PLANILHA MODELO.xlsx` a chamava de 'Informações
# Complementares' — que e o rotulo do botao que ABRE o card, e nao o nome do
# dado. O modelo foi corrigido; o codigo ja estava certo.
#
# As duas grafias aceitas na quinta posicao NAO sao normalizacao: sao um
# conjunto finito e comprovado. O modelo trazia 'Dt. Vcto' nesta aba e
# 'Dt.Vcto.' na de Débitos — discordava de si mesmo. A forma canonica e a do
# codigo, e a antiga fica como alias para nao recusar planilha ja em uso.
ALIAS_DO_CABECALHO = {
    ABA_PROCESSOS: {4: ("Dt. Vcto",)},
}

CAMPOS_DEBITO = (
    "cnpj", "tipo", "tributo", "receita", "pa_ex", "dt_vcto", "valor_original", "saldo",
)
CAMPOS_PROCESSO = (
    "cnpj", "tipo", "receita", "pa_ex", "dt_vcto", "valor_original", "saldo",
    "processo_credito",
)


# ── Regras puras ──────────────────────────────────────────────────────────────

def normalizar_cnpj(valor) -> str:
    """Remove qualquer formatacao de CNPJ e devolve 14 digitos."""
    return re.sub(r"\D", "", str(valor)).zfill(14)


def linha_vazia(ws, idx: int) -> bool:
    """True se a linha nao tem nenhum valor.

    So o conteudo conta. Formatacao remanescente, bordas e preenchimento de
    linhas que um dia tiveram dados e foram apagadas sao ignorados — e por isso
    que nao se pode usar ws.max_row / ws.append() aqui: eles enxergam essas
    linhas fantasma como ocupadas e empurram a gravacao para muito abaixo.
    """
    if idx > ws.max_row:
        return True
    return all(
        celula.value is None or str(celula.value).strip() == ""
        for celula in ws[idx]
    )


def proximas_linhas_vazias(ws, quantidade: int) -> list[int]:
    """Indices das proximas `quantidade` linhas vazias, de cima para baixo.

    Comeca na linha 2 (a 1 e o cabecalho) e devolve toda linha sem conteudo,
    tenha ela sido usada antes ou nao. Linhas ocupadas sao puladas, NUNCA
    sobrescritas — entao se a lacuna do topo for menor que o volume de dados, o
    restante continua depois da ultima linha ocupada.
    """
    livres: list[int] = []
    idx = 2
    while len(livres) < quantidade:
        if linha_vazia(ws, idx):
            livres.append(idx)
        idx += 1
    return livres


def descrever_destinos(destinos: list[int]) -> str:
    """Resumo legivel das linhas usadas, sinalizando quando nao sao contiguas."""
    if not destinos:
        return "nenhuma linha"
    if len(destinos) == 1:
        return f"L{destinos[0]}"
    contiguo = destinos == list(range(destinos[0], destinos[0] + len(destinos)))
    faixa = f"L{destinos[0]}-L{destinos[-1]}"
    return faixa if contiguo else f"{faixa} (com saltos)"


def _valores(registro: dict, campos) -> list:
    return [registro.get(c, "") for c in campos]


# ── Leitura: o unico ponto de pandas ──────────────────────────────────────────

def ler_e_ordenar(caminho: str) -> tuple[pd.DataFrame, int]:
    """Le a aba 'Empresas', remove duplicatas e ordena pela coluna do certificado.

    Ordenar por certificado e o que permite um unico login servir varios CNPJs —
    e propriedade funcional, nao estetica.

    O ramo de CSV esta PRESERVADO do original e e inalcancavel pela fronteira
    nova, que so aceita .xlsx (BOUNDARY_BEHAVIOR_CHANGE). Removido quando o
    ultimo caminho legado que chama isto tambem passar pela fronteira.
    """
    if caminho.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(caminho, sheet_name=ABA_EMPRESAS, dtype=str)
    else:
        df = pd.read_csv(caminho, dtype=str)

    # O INDICE E A IDENTIDADE daqui para a frente. Nada de `reset_index` nem de
    # `ignore_index`: filtrar e reordenar mudam a ORDEM, e nao quem cada linha e.
    df = df.dropna(how="all")

    # As repetidas sao CONTADAS, e nao removidas.
    #
    # Descartar linha repetida era descartar uma linha da planilha que ninguem
    # mais escreveria: ela ficava em branco para sempre. E ninguem era avisado —
    # o numero voltava daqui e o app o descartava.
    #
    # Repetir nao e invalido. Duas linhas do mesmo CNPJ podem existir por motivo
    # legitimo, e agora cada uma tem o proprio destino.
    repetidas = int(df.duplicated().sum())

    col_certificado = df.columns[COL_CERTIFICADO]
    df = df.sort_values(by=col_certificado, kind="stable")
    # A contagem volta em vez de virar print: a integracao nao decide o que
    # aparece no console.
    return df, repetidas


def linhas_pendentes(df: pd.DataFrame, mapa: dict, encerra_linha) -> tuple[pd.DataFrame, int]:
    """Remove do DataFrame as linhas ja concluidas.

    Roda antes de abrir o navegador. Sem isso a automacao fazia login num
    certificado para so entao descobrir, CNPJ a CNPJ, que todas as linhas dele ja
    estavam prontas — pagando um login inteiro a toa.

    `encerra_linha` chega como parametro em vez de import: a regra de quais
    status terminam uma linha e do portal, nao da planilha.
    """
    def _pendente(indice) -> bool:
        val_d, val_e = mapa.get(int(indice) + PRIMEIRA_LINHA_DE_DADOS, ("", ""))
        if encerra_linha(val_d):
            return False
        return not (val_d and val_e)

    mask = pd.Series(df.index.map(_pendente), index=df.index)
    return df[mask], int((~mask).sum())


# ── O item que a aplicacao consome ────────────────────────────────────────────
# A orquestracao lia `df.iterrows()`, `df.columns[0]` e `df.columns[2]` direto.
# Isso obrigava quem coordena a automacao a conhecer pandas e a posicao fisica
# das colunas. O item abaixo e a MENOR travessia possivel: o que o laco de fato
# usa, e nada mais.

# Valores que aparecem na celula de CNPJ e nao sao CNPJ. Preservados como estao:
# "nan"/"None" sao o que `str()` produz sobre uma celula vazia lida pelo pandas.
CNPJS_IGNORADOS = ("", "nan", "None", "00000000000000")


@dataclass(frozen=True)
class ItemPendente:
    """Uma linha da aba 'Empresas' a processar.

    `posicao` e OPACA: serve so para dizer ao operador em que ponto da lista a
    execucao esta. Ela muda com a ordenacao por certificado e NAO identifica
    nada.

    `linha` e a identidade. E o numero da linha na aba 'Empresas', carregado
    desde a leitura, e e por ele que o progresso e lido e gravado. Antes disso a
    identidade era o CNPJ, e duas linhas com o mesmo CNPJ disputavam o mesmo
    destino.

    O que NAO esta aqui, de proposito: o estado das colunas D e E. Ele muda
    DURANTE a execucao — o DCTFWeb grava D antes de os Processos comecarem — e
    uma retentativa da mesma linha precisa ler o valor novo, nao o do inicio da
    lista. Congelar D/E no item quebraria a retomada dentro da propria execucao.
    """

    posicao: int
    cnpj: str
    certificado: str
    linha: int

    @property
    def utilizavel(self) -> bool:
        """False quando a celula de CNPJ nao trazia um CNPJ."""
        return self.cnpj not in CNPJS_IGNORADOS


def itens_pendentes(df: pd.DataFrame) -> list[ItemPendente]:
    """Converte o DataFrame ja filtrado na lista que a aplicacao percorre.

    Aqui morre o pandas do fluxo: daqui para cima ninguem ve DataFrame, Series,
    indice ou numero de coluna.
    """
    col_cnpj = df.columns[COL_CNPJ]
    col_cert = df.columns[COL_CERTIFICADO]
    return [
        ItemPendente(
            posicao=posicao,
            cnpj=normalizar_cnpj(re.sub(r"\.0+$", "", str(linha[col_cnpj]).strip())),
            certificado=str(linha[col_cert]).strip(),
            linha=int(idx) + PRIMEIRA_LINHA_DE_DADOS,
        )
        for posicao, (idx, linha) in enumerate(df.iterrows())
    ]


def certificados_dos_itens(itens: list[ItemPendente]) -> list[tuple[str, int]]:
    """(certificado, quantidade de itens), na ordem de primeira aparicao."""
    contagem: dict[str, int] = {}
    for item in itens:
        contagem[item.certificado] = contagem.get(item.certificado, 0) + 1
    return list(contagem.items())


# ── O recurso stateful ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RetomadaDaLinha:
    """O progresso ja gravado para um CNPJ, sem os textos que o produziram."""

    dctfweb_feito: bool
    processos_feitos: bool
    encerrada: bool

    @property
    def concluida(self) -> bool:
        return self.dctfweb_feito and self.processos_feitos


@dataclass(frozen=True)
class RegistroDeDetalhe:
    """O que uma gravacao de detalhe produziu, para quem quiser relatar."""

    destinos: list[int]
    marcado: bool

    @property
    def linhas(self) -> int:
        return len(self.destinos)


class SessaoPlanilha:
    """O workbook aberto uma vez e mantido em memoria.

    Antes, cada leitura recarregava o arquivo inteiro do disco e cada escrita o
    regravava inteiro. Medido numa planilha de 1031 empresas e 20 mil linhas de
    resultado: 1,2s so para ler duas celulas e 2,9s por gravacao, com ate quatro
    gravacoes por CNPJ. So a verificacao de "ja concluido" custava 21 minutos.

    OWNERSHIP: quem abre nao e dono. A sessao abre sob demanda, mas quem decide
    QUANDO gravar e QUANDO fechar e o fluxo de processamento — hoje `main`, no
    futuro o app. Por isso `gravar()` levanta em vez de tratar: o dono decide o
    que fazer com a falha, e o diagnostico fica com ele.

    `estado` e um dicionario exposto de proposito: TRANSITIONAL, e a superficie
    que o legado ainda le direto. Sai quando `main` falar so pelos metodos.
    """

    def __init__(self) -> None:
        self.estado: dict[str, Any] = {
            "caminho": None, "wb": None, "sujo": False, "status": None
        }

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    @property
    def caminho(self) -> str | None:
        return self.estado["caminho"]

    @property
    def wb(self):
        return self.estado["wb"]

    @property
    def sujo(self) -> bool:
        return bool(self.estado["sujo"])

    def precisa_abrir(self, caminho: str) -> bool:
        """True se ainda nao ha workbook, ou se e outro arquivo."""
        return self.estado["wb"] is None or self.estado["caminho"] != caminho

    def abrir(self, caminho: str) -> None:
        """Abre o workbook — e so o adota se a estrutura dele conferir.

        A conferencia acontece AQUI porque este e o ponto onde o recurso que
        sera mutado entra. O pre-voo tambem a faz, mais cedo e sem navegador
        aberto; a daqui e a que nenhum caminho contorna.

        Uma planilha recusada nao chega a virar estado da sessao: o workbook e
        fechado e nada nesta sessao passa a apontar para ele.
        """
        wb = openpyxl.load_workbook(caminho)
        try:
            validar_schema(wb)
        except PlanilhaIndisponivel:
            wb.close()
            raise

        self.estado["caminho"] = caminho
        self.estado["wb"] = wb
        self.estado["sujo"] = False

    def descartar(self) -> None:
        """Solta o workbook da memoria SEM gravar. Gravar e decisao do dono."""
        if self.estado["wb"] is not None:
            try:
                self.estado["wb"].close()
            except (OSError, AttributeError):
                pass
        self.estado.update({"caminho": None, "wb": None, "sujo": False, "status": None})

    # ── Gravacao ──────────────────────────────────────────────────────────────

    def precisa_gravar(self) -> bool:
        return self.estado["wb"] is not None and bool(self.estado["sujo"])

    def gravar(self) -> None:
        """Sobrescreve o arquivo recebido. Levanta se nao conseguir.

        A sessao NAO limpa o sinal de sujo aqui — quem trata a falha e quem
        decide se o dado ainda esta pendente. Ver `marcar_gravado`.
        """
        self.estado["wb"].save(self.estado["caminho"])

    def marcar_gravado(self) -> None:
        self.estado["sujo"] = False

    # ── Aba 'Empresas' ────────────────────────────────────────────────────────

    def mapa_status(self, caminho: str) -> dict[int, tuple[str, str]]:
        """Mapa {linha: (coluna_D, coluna_E)}, montado uma so vez por sessao.

        Varrer a aba a cada consulta era O(n) por consulta; com o mapa ela vira
        uma busca em dicionario.

        A chave e a LINHA, e nao o CNPJ. Com o CNPJ, duas ocorrencias colapsavam
        numa entrada so — a ultima vencia — e o progresso de uma era lido como
        se fosse o da outra.
        """
        if self.estado["status"] is None or self.estado["caminho"] != caminho:
            ws = self.wb[ABA_EMPRESAS]
            mapa: dict[int, tuple[str, str]] = {}
            for numero, linha in enumerate(ws.iter_rows(min_row=PRIMEIRA_LINHA_DE_DADOS),
                                           start=PRIMEIRA_LINHA_DE_DADOS):
                if not linha[COL_CNPJ].value:
                    continue
                val_d = (
                    str(linha[COL_STATUS_DCTFWEB].value or "").strip()
                    if len(linha) > COL_STATUS_DCTFWEB else ""
                )
                val_e = (
                    str(linha[COL_STATUS_PROCESSOS].value or "").strip()
                    if len(linha) > COL_STATUS_PROCESSOS else ""
                )
                mapa[numero] = (val_d, val_e)
            self.estado["status"] = mapa
        return self.estado["status"]

    def escrever_status(self, linha: int, valor: str, coluna: int) -> bool:
        """Escreve na coluna indicada DAQUELA linha. False se ela nao serve.

        Nao ha busca. A linha vem do item que esta sendo processado, e o item a
        carrega desde a leitura — e por isso que duas ocorrencias do mesmo CNPJ
        deixaram de disputar o mesmo destino.

        `False` quando a linha esta fora da aba ou nao tem CNPJ. Nao se procura
        "a linha parecida": quem chamou fica sabendo que nao gravou.
        """
        ws = self.wb[ABA_EMPRESAS]

        if PRIMEIRA_LINHA_DE_DADOS <= linha <= ws.max_row:
            celulas = ws[linha]
            if celulas[COL_CNPJ].value:
                celula = celulas[coluna]
                celula.value = valor
                celula.alignment = Alignment(horizontal="center", vertical="center")
                self.estado["sujo"] = True

                # Mantem o mapa em sincronia com a celula.
                mapa = self.estado["status"]
                if mapa is not None:
                    val_d, val_e = mapa.get(linha, ("", ""))
                    mapa[linha] = (
                        (valor, val_e) if coluna == COL_STATUS_DCTFWEB else (val_d, valor)
                    )
                return True

        return False

    # ── API application-facing ────────────────────────────────────────────────
    # Acima ficam as primitivas (coluna, aba, celula). Daqui para baixo, o
    # vocabulario de quem coordena a automacao. Quem chama nao soletra "coluna
    # D", nem o texto do status, nem em que aba o detalhe vai parar.
    #
    # Cada metodo devolve o que aconteceu, em vez de imprimir: o diagnostico
    # continua sendo de quem chama.

    def retomada(self, caminho: str, linha: int, encerra_linha) -> RetomadaDaLinha:
        """O que ja foi feito por ESTA linha em execucoes anteriores.

        `encerra_linha` entra por parametro pelo mesmo motivo de
        `linhas_pendentes`: quais status terminam uma linha e regra do portal,
        nao da planilha.

        Lida a cada consulta de proposito. Dentro de uma mesma execucao o valor
        muda — o DCTFWeb grava D antes de os Processos comecarem, e uma
        retentativa da mesma linha tem de enxergar o D novo.

        Por LINHA, e nao por CNPJ: com duas ocorrencias do mesmo documento, uma
        recebia o progresso da outra.
        """
        val_d, val_e = self.mapa_status(caminho).get(linha, ("", ""))
        return RetomadaDaLinha(
            dctfweb_feito=bool(val_d),
            processos_feitos=bool(val_e),
            encerrada=bool(encerra_linha(val_d)),
        )

    def registrar_sem_debitos(self, linha: int) -> bool:
        return self.escrever_status(linha, STATUS_SEM_DEBITOS, COL_STATUS_DCTFWEB)

    def registrar_debitos_nao_compensaveis(self, linha: int) -> bool:
        return self.escrever_status(
            linha, STATUS_DEBITOS_NAO_COMPENSAVEIS, COL_STATUS_DCTFWEB
        )

    def registrar_sem_processos(self, linha: int) -> bool:
        return self.escrever_status(linha, STATUS_SEM_PROCESSOS, COL_STATUS_PROCESSOS)

    def registrar_debitos_concluidos(self, linha: int) -> bool:
        """Fecha a coluna do DCTFWeb sem ter havido tabela de DCTFWeb.

        Acontece quando so existe Processo Fiscal: o portal nao oferece a divida
        DCTFWeb, e a linha nao pode ficar pendente para sempre por causa disso.
        """
        return self.escrever_status(linha, STATUS_CONCLUIDO, COL_STATUS_DCTFWEB)

    def registrar_recusa_do_portal(self, linha: int, status: str) -> bool:
        """Grava o motivo pelo qual o portal recusou o CNPJ.

        O texto vem da classificacao da recusa, nao da mensagem bruta do portal.
        """
        return self.escrever_status(linha, status, COL_STATUS_DCTFWEB)

    def registrar_debitos(self, linha: int, linhas: list[dict]) -> RegistroDeDetalhe:
        """Detalhe primeiro, status depois — nesta ordem, e ela e o contrato.

        Se a gravacao do detalhe cair, a coluna nao e marcada e a proxima
        execucao refaz o DCTFWeb inteiro. O inverso perderia os dados em
        silencio. RESUMABILITY_CONTRACT.
        """
        destinos = self.anexar_debitos(linhas)
        return RegistroDeDetalhe(
            destinos=destinos,
            marcado=self.escrever_status(linha, STATUS_CONCLUIDO, COL_STATUS_DCTFWEB),
        )

    def registrar_processos(self, linha: int, linhas: list[dict]) -> RegistroDeDetalhe:
        """Mesma ordem e mesmo motivo do DCTFWeb, na coluna dos Processos."""
        destinos = self.anexar_processos(linhas)
        return RegistroDeDetalhe(
            destinos=destinos,
            marcado=self.escrever_status(linha, STATUS_CONCLUIDO, COL_STATUS_PROCESSOS),
        )

    # ── Abas de detalhe ───────────────────────────────────────────────────────

    def anexar(self, nome_aba: str, cabecalho: list[str], linhas: list[list]) -> list[int]:
        """Acrescenta linhas na aba, criando-a com cabecalho se nao existir."""
        wb = self.wb
        if nome_aba not in wb.sheetnames:
            ws = wb.create_sheet(nome_aba)
            ws.append(cabecalho)
        else:
            ws = wb[nome_aba]

        destinos = proximas_linhas_vazias(ws, len(linhas))
        for destino, valores in zip(destinos, linhas, strict=True):
            for col, valor in enumerate(valores, start=1):
                ws.cell(row=destino, column=col, value=valor)

        self.estado["sujo"] = True
        return destinos

    def anexar_debitos(self, dados: list[dict]) -> list[int]:
        return self.anexar(
            ABA_DEBITOS, CABECALHO_DEBITOS, [_valores(d, CAMPOS_DEBITO) for d in dados]
        )

    def anexar_processos(self, dados: list[dict]) -> list[int]:
        return self.anexar(
            ABA_PROCESSOS, CABECALHO_PROCESSOS, [_valores(d, CAMPOS_PROCESSO) for d in dados]
        )


# ── RESOURCE_VALIDATION ───────────────────────────────────────────────────────
# A UI desktop checava `os.path.isfile` antes de entregar o caminho. A checagem e
# necessaria; a UI e que era o lugar errado — o fluxo sem Tkinter ficava sem ela.
# A fronteira valida FORMA; aqui se valida o RECURSO.

class PlanilhaIndisponivel(Exception):
    """A planilha existe como caminho valido, mas nao serve para esta execucao.

    Erro de ENTRADA — quem executou pode corrigir (arquivo errado, arquivo aberto
    no Excel, aba faltando). Nao e falha tecnica e nao e bug nosso.

    A mensagem nunca carrega caminho nem nome de arquivo: o nome da planilha
    costuma ser o nome do cliente.
    """


def _normalizar_cabecalho(valor) -> str:
    """Espaco e caixa. E so isso.

    As duas normalizacoes sao as que o proprio modelo demonstra: ele traz 'Dt.
    Vcto' onde o codigo escreve 'Dt.Vcto.', 'Saldo Devedor ' com espaco no fim,
    e mistura CAIXA ALTA na aba 'Empresas' com Caixa de Titulo nas de detalhe.
    E um arquivo editado a mao, e espaco e caixa variam.

    O que NAO se faz aqui: acento, semelhanca, substring, aproximacao. Nenhum
    deles tem evidencia, e cada um transformaria um erro de estrutura numa
    escolha silenciosa de coluna — que e exatamente o problema desta fatia.
    """
    return " ".join(str(valor if valor is not None else "").split()).upper()


def _cabecalho_da_aba(ws) -> list:
    """A primeira linha, ou vazio se a aba nao tem linha nenhuma."""
    return list(next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ()))


def _conferir(ws, esperado: dict, alias: dict | None = None) -> None:
    """`esperado` mapeia POSICAO (0-based) -> texto canonico do cabecalho.

    `alias` mapeia POSICAO -> outras grafias HISTORICAS aceitas naquela posicao.
    Cada uma delas foi encontrada num artefato do projeto; nao ha aproximacao.
    """
    lido = _cabecalho_da_aba(ws)
    alias = alias or {}

    for posicao, texto in esperado.items():
        atual = lido[posicao] if posicao < len(lido) else None
        aceitos = {_normalizar_cabecalho(t)
                   for t in (texto, *alias.get(posicao, ()))}
        if _normalizar_cabecalho(atual) not in aceitos:
            # A mensagem nomeia a ABA e a COLUNA, que sao do formato, e nunca o
            # que foi lido: uma celula de cabecalho trocada pode conter qualquer
            # coisa que estivesse na planilha.
            raise PlanilhaIndisponivel(
                f"A planilha não está no formato esperado: a aba '{ws.title}' "
                f"não tem a coluna {chr(ord('A') + posicao)} esperada."
            )


def validar_schema(wb) -> None:
    """A ESTRUTURA da planilha, conferida antes de qualquer mutacao.

    Por que cabecalho, e nao posicao nua
    ------------------------------------
    As posicoes A/C/D/E vem do codigo original e ficam. O que muda e que elas
    deixam de ser aceitas sem prova: uma coluna a mais no inicio desloca as
    quatro, e ate aqui isso passava em silencio — a automacao lia o numero da
    filial como CNPJ e escrevia o status por cima do certificado.

    Isto NAO procura as colunas. Nao ha "onde sera que esta o CNPJ", nem
    "se D nao parece DCTF, tenta E". Ou a planilha esta no formato conhecido, ou
    e recusada. Descobrir automaticamente trocaria um erro barulhento por uma
    escolha errada e silenciosa.

    Estrutura nao e conteudo. Uma planilha meio processada — D preenchida e E
    vazia, ou o contrario — esta no formato e continua valendo.
    """
    if ABA_EMPRESAS not in wb.sheetnames:
        raise PlanilhaIndisponivel(f"A planilha não tem a aba '{ABA_EMPRESAS}'.")

    _conferir(wb[ABA_EMPRESAS], CABECALHO_EMPRESAS)

    # As abas de detalhe so existem se ja houve execucao — ou se vieram do
    # modelo. Quando existem, o append escreve nelas: conferir antes e o que
    # impede um valor de ir parar sob uma coluna de outro significado.
    #
    # Sao as OITO de cada uma. A automacao escreve oito valores, entao precisa
    # saber o que as oito colunas querem dizer. Colunas ALEM da oitava nao sao
    # conferidas: a automacao nao escreve nelas, e o proprio modelo traz uma
    # nona em `Débitos`.
    for nome, cabecalho in ((ABA_DEBITOS, CABECALHO_DEBITOS),
                            (ABA_PROCESSOS, CABECALHO_PROCESSOS)):
        if nome in wb.sheetnames:
            _conferir(wb[nome], dict(enumerate(cabecalho)),
                      ALIAS_DO_CABECALHO.get(nome))


def validar_recurso(caminho: str) -> None:
    """Pre-voo: da para ler E gravar esta planilha antes de abrir o navegador?

    Existe uma razao concreta para checar a GRAVACAO antes e nao depois: a falha
    real mais comum e o arquivo estar aberto no Excel, e descobrir isso so no
    primeiro save significa ter feito login e processado um CNPJ a toa.

    Abrir em "r+b" testa o bloqueio sem escrever byte nenhum.
    """
    arquivo = pathlib.Path(caminho)

    if not arquivo.exists():
        raise PlanilhaIndisponivel("A planilha informada não foi encontrada.")
    if not arquivo.is_file():
        raise PlanilhaIndisponivel("O caminho informado não é um arquivo.")

    try:
        with arquivo.open("r+b"):
            pass
    except PermissionError:
        raise PlanilhaIndisponivel(
            "A planilha está bloqueada para gravação. Feche-a no Excel e tente de novo."
        ) from None
    except OSError:
        raise PlanilhaIndisponivel("Não foi possível abrir a planilha.") from None

    # As tres familias foram confirmadas por sonda, nao presumidas:
    #   BadZipFile        arquivo que nao e zip (texto, vazio, truncado);
    #   KeyError          zip valido sem as partes do OOXML;
    #   InvalidFileException  extensao que o openpyxl recusa de saida.
    try:
        wb = openpyxl.load_workbook(caminho, read_only=True)
    except (zipfile.BadZipFile, KeyError, InvalidFileException):
        # `from None` corta o encadeamento: a mensagem do openpyxl traz o caminho.
        raise PlanilhaIndisponivel("O arquivo não é uma planilha .xlsx válida.") from None

    try:
        validar_schema(wb)
    finally:
        wb.close()
