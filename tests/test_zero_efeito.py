"""Zero item pendente e um desfecho legitimo — nao uma falha.

O ESTADO ANTES DA D8.1-B, reproduzido antes de qualquer correcao:

- `executar` carregava o catalogo de certificados ANTES de olhar a planilha. Uma
  planilha valida sem nada a fazer terminava em `certificados_indisponiveis`, e
  o adapter da plataforma relatava `ok: false` — erro de operador que nao existe.
- `linhas_pendentes` devolvia um DataFrame SEM COLUNAS quando nao havia linha
  alguma, porque a mascara vazia virava selecao de colunas. `itens_pendentes`
  entao levantava `IndexError`. O erro estava escondido atras do aborto acima.
- `aliases_de_certificado` colhia o nome de TODA linha utilizavel, inclusive das
  ja concluidas. No caminho do cofre isso e um `.pfx` baixado por linha morta.

Nada aqui abre navegador, portal, Gemini, cofre, PowerShell ou registro: as
fronteiras externas sao armadas para FALHAR, e estes testes so passam se ninguem
as tocar. CNPJs, empresas e certificados sao sentinelas ficticias.
"""
import ast
import inspect
import pathlib

import pytest
from planilhas_sinteticas import criar_planilha

from automation import (
    app,
    certificados,
    certificados_windows,
    eventos,
    planilha,
    status_portal,
)
from automation.boundary import montar_entrada
from automation.captcha import ConfigCaptcha

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ConfigCaptcha(api_key="chave-ficticia")

CNPJ_UM = "11111111000191"
CNPJ_DOIS = "22222222000172"
CONCLUIDA = (planilha.STATUS_CONCLUIDO, planilha.STATUS_SEM_PROCESSOS)

PENDENTE_A = (CNPJ_UM, "ALFA FICTICIA LTDA", "CERT-A")
PENDENTE_B = (CNPJ_DOIS, "BETA FICTICIA LTDA", "CERT-B")


class EfeitoExternoNaSuite(AssertionError):
    """Alguem tentou sair para o mundo num cenario que nao precisa de nada."""


@pytest.fixture(autouse=True)
def _fronteiras_armadas(monkeypatch):
    """Toda porta de saida falha. Quem passar por uma delas reprova o teste."""
    def recusar(nome):
        def armado(*_a, **_k):
            raise EfeitoExternoNaSuite(nome)
        return armado

    monkeypatch.setattr(app, "_percorrer", recusar("_percorrer"))
    for nome in ("garantir_policy_do_windows", "abrir_sessao", "estado_do_guardiao"):
        monkeypatch.setattr(app.maquina, nome, recusar(f"maquina.{nome}"))
    monkeypatch.setattr(app.consulta_fiscal, "consultar_debitos",
                        recusar("consulta_fiscal"), raising=False)
    monkeypatch.setattr(certificados_windows.CertificadosDoWindows, "carregar",
                        recusar("CertificadosDoWindows.carregar"))


class ProvedorQueRecusa:
    """Um catalogo que grita se alguem o consultar."""

    def carregar(self):
        raise EfeitoExternoNaSuite("provedor.carregar")

    def resolver(self, nome):
        raise EfeitoExternoNaSuite("provedor.resolver")

    def certificado(self, chave):
        raise EfeitoExternoNaSuite("provedor.certificado")


class ProvedorVazio:
    """Carrega, e o catalogo esta vazio — o caso do operador sem certificado."""

    def __init__(self):
        self.carregou = 0

    def carregar(self):
        self.carregou += 1
        return 0

    def resolver(self, nome):
        from automation.domain import ResultadoDaBusca
        return ResultadoDaBusca()

    def certificado(self, chave):
        raise KeyError(chave)


class ProvedorIlegivel:
    """Nao deu para LER o catalogo — diferente de nao haver certificado."""

    def carregar(self):
        raise certificados.FalhaAoLerCertificados("catalogo ilegivel")

    def resolver(self, nome):
        raise AssertionError("nao chega aqui")

    def certificado(self, chave):
        raise AssertionError("nao chega aqui")


def _planilha(tmp_path, linhas=(), status=None):
    caminho = tmp_path / "empresas.xlsx"
    criar_planilha(str(caminho), linhas=linhas, status=status or {})
    return caminho


def _executar(caminho, provedor):
    """Roda a aplicacao e devolve os codigos de evento observados."""
    recebidos = []
    app.executar(montar_entrada({"planilha": str(caminho)}), CONFIG,
                 emitir_evento=lambda e: recebidos.append(e.codigo),
                 provedor_de_certificados=provedor)
    return recebidos


def _aliases(caminho):
    """Pela mesma porta da borda: quem escolhe a regra e a aplicacao."""
    return app.aliases_necessarios(str(caminho))


# ── a planilha vazia atravessa o fluxo inteiro ───────────────────────────────

def test_a_planilha_so_com_cabecalho_nao_perde_as_colunas(tmp_path):
    """A causa do `IndexError`, presa onde ela acontecia.

    Com a mascara de dtype object, `df[mask]` selecionava COLUNAS: o DataFrame
    voltava com zero colunas, e `itens_pendentes` pedia `df.columns[0]`.
    """
    caminho = _planilha(tmp_path)
    df, _ = planilha.ler_e_ordenar(str(caminho))
    sessao = planilha.SessaoPlanilha()
    sessao.abrir(str(caminho))
    try:
        filtrado, _ = planilha.linhas_pendentes(
            df, sessao.mapa_status(str(caminho)), status_portal.status_encerra_linha)
    finally:
        sessao.descartar()

    assert len(filtrado.columns) == len(df.columns), "as colunas continuam la"
    assert planilha.itens_pendentes(filtrado) == []


def test_a_planilha_so_com_cabecalho_nao_pede_alias_nenhum(tmp_path):
    assert _aliases(_planilha(tmp_path)) == ()


# ── o catalogo so e consultado quando existe item ────────────────────────────

def test_sem_item_pendente_o_catalogo_NAO_e_carregado(tmp_path):
    """O provedor recusa qualquer consulta: o teste passa por ninguem consultar."""
    recebidos = _executar(_planilha(tmp_path), ProvedorQueRecusa())

    assert recebidos == [], "nenhum evento — nem sucesso falso, nem erro"


def test_a_linha_ja_concluida_tambem_dispensa_o_catalogo(tmp_path):
    caminho = _planilha(tmp_path, linhas=(PENDENTE_A,), status={0: CONCLUIDA})

    assert _executar(caminho, ProvedorQueRecusa()) == []


def test_o_desktop_nao_consulta_o_windows_sem_item_pendente(tmp_path):
    """Sem provedor explicito vale o do Windows, e ele le o Store por PowerShell.

    Uma execucao sem nada a fazer pagava esse custo e ainda podia abortar.
    """
    assert _executar(_planilha(tmp_path), provedor=None) == []


# ── e continua sendo erro quando o certificado FAZ falta ─────────────────────

def test_com_item_pendente_o_catalogo_vazio_continua_sendo_erro(tmp_path):
    """O objetivo nunca foi silenciar `certificados_indisponiveis`."""
    provedor = ProvedorVazio()
    caminho = _planilha(tmp_path, linhas=(PENDENTE_A,))

    recebidos = _executar(caminho, provedor)

    assert provedor.carregou == 1
    assert eventos.CERTIFICADOS_INDISPONIVEIS in recebidos


def test_com_item_pendente_o_catalogo_ilegivel_continua_sendo_o_OUTRO_erro(tmp_path):
    """A distincao da fatia 5A sobrevive a mudanca de ordem."""
    recebidos = _executar(_planilha(tmp_path, linhas=(PENDENTE_A,)), ProvedorIlegivel())

    assert eventos.LEITURA_DE_CERTIFICADOS_FALHOU in recebidos
    assert eventos.CERTIFICADOS_INDISPONIVEIS not in recebidos


def test_o_desktop_continua_consultando_o_windows_quando_ha_item(tmp_path, monkeypatch):
    """Paridade: com item pendente, o provedor do desktop e consultado como antes."""
    consultas = []
    monkeypatch.setattr(certificados_windows.CertificadosDoWindows, "carregar",
                        lambda self: consultas.append("carregar") or 0)

    recebidos = _executar(_planilha(tmp_path, linhas=(PENDENTE_A,)), provedor=None)

    assert consultas == ["carregar"]
    assert eventos.CERTIFICADOS_INDISPONIVEIS in recebidos


# ── os aliases sao os dos itens que serao processados ────────────────────────

def test_linha_concluida_nao_pede_certificado(tmp_path):
    caminho = _planilha(tmp_path, linhas=(PENDENTE_A,), status={0: CONCLUIDA})

    assert _aliases(caminho) == ()


def test_no_lote_misto_so_o_certificado_da_linha_pendente_e_pedido(tmp_path):
    """ANTES: ('CERT-A', 'CERT-B') — dois `.pfx` baixados para processar uma linha."""
    caminho = _planilha(tmp_path, linhas=(PENDENTE_A, PENDENTE_B), status={0: CONCLUIDA})

    assert _aliases(caminho) == ("CERT-B",)


def test_o_alias_repetido_continua_entrando_uma_vez_so(tmp_path):
    repetida = (CNPJ_DOIS, "BETA FICTICIA LTDA", "CERT-A")
    caminho = _planilha(tmp_path, linhas=(PENDENTE_A, repetida))

    assert _aliases(caminho) == ("CERT-A",)


def test_o_certificado_continua_sendo_da_LINHA(tmp_path):
    """Nada aqui transforma certificado em configuracao unica da execucao: cada
    item pendente carrega o seu, e a lista tem os dois."""
    caminho = _planilha(tmp_path, linhas=(PENDENTE_A, PENDENTE_B))
    df, _ = planilha.ler_e_ordenar(str(caminho))
    itens = planilha.itens_pendentes(df)

    assert {item.certificado for item in itens} == {"CERT-A", "CERT-B"}
    assert {item.linha for item in itens} == {2, 3}, "cada um na sua linha"
    assert sorted(_aliases(caminho)) == ["CERT-A", "CERT-B"]


def test_a_linha_sem_cnpj_continua_fora_da_lista(tmp_path):
    """Preservado da fatia anterior: linha sem CNPJ nao pede certificado."""
    sem_cnpj = ("", "SEM CNPJ FICTICIA", "CERT-B")
    caminho = _planilha(tmp_path, linhas=(PENDENTE_A, sem_cnpj))

    assert _aliases(caminho) == ("CERT-A",)


def test_o_workbook_lido_para_os_aliases_sai_fechado(tmp_path):
    """No Windows um arquivo aberto nao pode ser apagado — e o espaco da execucao
    e apagado no fim dela. Um handle vazado aqui so apareceria no runtime."""
    caminho = _planilha(tmp_path, linhas=(PENDENTE_A,))

    _aliases(caminho)

    caminho.unlink()
    assert not caminho.exists()


# ── uma regra de status so ───────────────────────────────────────────────────

def test_a_borda_PERGUNTA_e_nao_escolhe_a_regra_de_status():
    """Qual status encerra uma linha e assunto da aplicacao.

    A borda so pergunta quais certificados a execucao precisa. Se ela escolhesse
    a regra, existiriam duas respostas para a mesma pergunta, e o cofre atenderia
    a errada no dia em que divergissem.
    """
    fonte = (RAIZ / "runner.py").read_text(encoding="utf-8")

    assert "status_encerra_linha" not in fonte
    assert "status_portal" not in fonte

    chamadas = [n for n in ast.walk(ast.parse(fonte))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "aliases_necessarios"]
    chamada, = chamadas
    assert isinstance(chamada.func.value, ast.Name) and chamada.func.value.id == "app"
    assert len(chamada.args) == 1, "so o caminho da planilha atravessa"

    # E a regra continua escolhida num lugar so: dentro da aplicacao.
    escolhas = inspect.getsource(app.aliases_necessarios) + inspect.getsource(app.executar)
    assert escolhas.count("status_portal.status_encerra_linha") == 2
