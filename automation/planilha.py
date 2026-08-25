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

CABECALHO_DEBITOS = [
    "CNPJ", "TIPO", "TRIBUTO", "Rec.", "PA/Ex.",
    "Dt.Vcto.", "Valor Original", "Saldo Devedor",
]
CABECALHO_PROCESSOS = [
    "CNPJ", "TIPO", "RECEITA", "PA/Ex.", "Dt.Vcto.",
    "Valor Original", "Saldo Devedor", "Processo de Crédito",
]

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

    df = df.dropna(how="all").reset_index(drop=True)

    total_antes = len(df)
    df = df.drop_duplicates(ignore_index=True)
    removidas = total_antes - len(df)

    col_certificado = df.columns[COL_CERTIFICADO]
    df = df.sort_values(by=col_certificado, kind="stable", ignore_index=True)
    # A contagem de duplicatas volta em vez de virar print: a integracao nao
    # decide o que aparece no console.
    return df, removidas


def linhas_pendentes(df: pd.DataFrame, mapa: dict, encerra_linha) -> tuple[pd.DataFrame, int]:
    """Remove do DataFrame as linhas ja concluidas.

    Roda antes de abrir o navegador. Sem isso a automacao fazia login num
    certificado para so entao descobrir, CNPJ a CNPJ, que todas as linhas dele ja
    estavam prontas — pagando um login inteiro a toa.

    `encerra_linha` chega como parametro em vez de import: a regra de quais
    status terminam uma linha e do portal, nao da planilha.
    """
    col_cnpj = df.columns[COL_CNPJ]

    def _pendente(valor) -> bool:
        cnpj = normalizar_cnpj(re.sub(r"\.0+$", "", str(valor).strip()))
        val_d, val_e = mapa.get(cnpj, ("", ""))
        if encerra_linha(val_d):
            return False
        return not (val_d and val_e)

    mask = df[col_cnpj].map(_pendente)
    return df[mask].reset_index(drop=True), int((~mask).sum())


# ── O recurso stateful ────────────────────────────────────────────────────────

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
        self.estado["caminho"] = caminho
        self.estado["wb"] = openpyxl.load_workbook(caminho)
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

    def mapa_status(self, caminho: str) -> dict[str, tuple[str, str]]:
        """Mapa {cnpj: (coluna_D, coluna_E)}, montado uma so vez por sessao.

        Varrer a aba a cada consulta era O(n) por CNPJ; com o mapa a consulta
        vira uma busca em dicionario.
        """
        if self.estado["status"] is None or self.estado["caminho"] != caminho:
            ws = self.wb[ABA_EMPRESAS]
            mapa: dict[str, tuple[str, str]] = {}
            for linha in ws.iter_rows(min_row=2):
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
                mapa[normalizar_cnpj(linha[COL_CNPJ].value)] = (val_d, val_e)
            self.estado["status"] = mapa
        return self.estado["status"]

    def escrever_status(self, cnpj: str, valor: str, coluna: int) -> bool:
        """Escreve na coluna indicada da linha do CNPJ. False se nao achou a linha.

        PLANILHA_POSSIBLE_DEFECT preservado: com o CNPJ repetido na aba, escreve
        na PRIMEIRA linha, enquanto `mapa_status` guarda a ULTIMA. A segunda
        linha nunca e marcada e volta pendente em toda execucao.
        """
        ws = self.wb[ABA_EMPRESAS]

        for linha in ws.iter_rows(min_row=2):
            celula_cnpj = linha[COL_CNPJ]
            if celula_cnpj.value and normalizar_cnpj(celula_cnpj.value) == cnpj:
                celula = linha[coluna]
                celula.value = valor
                celula.alignment = Alignment(horizontal="center", vertical="center")
                self.estado["sujo"] = True

                # Mantem o mapa em sincronia com a celula.
                mapa = self.estado["status"]
                if mapa is not None:
                    val_d, val_e = mapa.get(cnpj, ("", ""))
                    mapa[cnpj] = (
                        (valor, val_e) if coluna == COL_STATUS_DCTFWEB else (val_d, valor)
                    )
                return True

        return False

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
        if ABA_EMPRESAS not in wb.sheetnames:
            raise PlanilhaIndisponivel(f"A planilha não tem a aba '{ABA_EMPRESAS}'.")
    finally:
        wb.close()
