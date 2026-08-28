"""Caracterizacao da consulta fiscal COMO ELA E HOJE.

Nenhum teste abre navegador ou portal. A Page falsa tem so os metodos que estas
funcoes realmente chamam, e as escritas na planilha sao ESPIONADAS — e a
orquestracao entre "o que foi lido" e "o que foi gravado" que precisa ficar
registrada para a fatia de persistencia nao ter de adivinhar.

Dados fiscais totalmente ficticios.
"""
from typing import ClassVar

import pytest
from navegador_falso import PaginaFiscal

from automation import app, apresentacao_eventos, eventos, planilha
from automation import consulta_fiscal as fiscal
from automation.captcha import ConfigCaptcha

CONFIG = ConfigCaptcha(api_key="AIzaSy-SENTINELA-FICTICIA-0000")

CNPJ = "11111111000191"
PLANILHA = "C:/nao/existe/base.xlsx"


class SessaoDePagina:
    """So encanamento: a fatia 8B1 trocou `page` por `SessaoReceita` na assinatura.

    Nenhuma assercao destes testes mudou — o que mudou foi COMO a pagina chega.
    """

    def __init__(self, pagina):
        self.pagina = pagina


def sessao_de(pagina):
    return SessaoDePagina(pagina)


class PlanilhaEspia:
    """A SessaoPlanilha vista pelo app, com as PRIMITIVAS trocadas por espioes.

    CHARACTERIZATION_TARGET_CHANGE (fatia 9B): antes a substituicao era nos
    wrappers de coluna do `main`, que sumiram. Agora e em celula e aba. A troca
    FORTALECE o teste: a ordem "detalhe primeiro, status depois" passou a ser
    exercitada no codigo de verdade (`registrar_debitos`), e nao mais no dublê.
    """

    _ROTULOS: ClassVar = {planilha.COL_STATUS_DCTFWEB: "D",
                          planilha.COL_STATUS_PROCESSOS: "E"}

    def __init__(self, registro):
        self.registro = registro

    def escrever_status(self, linha, valor, coluna):
        # Fase 15: a primitiva grava por LINHA. O espiao registra o rotulo da
        # coluna, como sempre — o que este arquivo observa e a ORDEM.
        self.registro.append((self._ROTULOS[coluna], valor))
        return True

    def anexar_debitos(self, dados):
        return self._anexar("aba Débitos", dados)

    def anexar_processos(self, dados):
        return self._anexar("aba Processos", dados)

    def _anexar(self, rotulo, dados):
        self.registro.append((rotulo, len(dados)))
        return list(range(2, 2 + len(dados)))

    # A composicao semantica e a de verdade: e ela que esta sob teste.
    registrar_debitos = planilha.SessaoPlanilha.registrar_debitos
    registrar_processos = planilha.SessaoPlanilha.registrar_processos
    registrar_sem_debitos = planilha.SessaoPlanilha.registrar_sem_debitos
    registrar_sem_processos = planilha.SessaoPlanilha.registrar_sem_processos
    registrar_debitos_concluidos = planilha.SessaoPlanilha.registrar_debitos_concluidos
    registrar_debitos_nao_compensaveis = (
        planilha.SessaoPlanilha.registrar_debitos_nao_compensaveis
    )


@pytest.fixture
def escritas():
    """Registra toda gravacao que o fluxo fiscal dispara, na ordem."""
    return []


def verificar_pendencias(sessao, escritas, skip_dctfweb=False, skip_processo=False):
    """O antigo `main.verificar_pendencias`, agora no app. Devolve os EVENTOS.

    Duas mudancas de alvo, nenhuma de comportamento:

    - os `skip_*` eram parametros; hoje nascem da RETOMADA lida da planilha, que
      e de onde sempre vieram conceitualmente;
    - o valor de retorno ("sem_pendencia", "concluido"...) nao existe mais. Ele
      nao era lido por ninguem — `processar` o descartava — e a distincao que
      carregava esta nos codigos emitidos.
    """
    codigos = []
    execucao = app._Execucao(PlanilhaEspia(escritas), PLANILHA, CONFIG,
                             lambda e: codigos.append(e.codigo))
    execucao.sessao = sessao
    app._consultar_situacao(
        execucao,
        planilha.ItemPendente(posicao=0, cnpj=CNPJ, certificado="CERT FICTICIO", linha=2),
        planilha.RetomadaDaLinha(skip_dctfweb, skip_processo, encerrada=False),
    )
    return codigos


def _rodar(funcao, sessao, escritas):
    codigos = []
    execucao = app._Execucao(PlanilhaEspia(escritas), PLANILHA, CONFIG,
                             lambda e: codigos.append(e.codigo))
    execucao.sessao = sessao
    funcao(execucao, planilha.ItemPendente(posicao=0, cnpj=CNPJ,
                                           certificado="CERT FICTICIO", linha=2))
    return codigos


def extrair_debitos(sessao, escritas):
    """O antigo `main.extrair_debitos_dctfweb` — consulta e gravacao do DCTFWeb."""
    return _rodar(app._extrair_debitos, sessao, escritas)


def extrair_processos(sessao, escritas):
    """O antigo `main.extrair_processo_fiscal`."""
    return _rodar(app._extrair_processos, sessao, escritas)


@pytest.fixture(autouse=True)
def sem_navegacao_real(monkeypatch):
    """Os helpers de paginacao mudaram de casa na 8B2 — so encanamento."""
    monkeypatch.setattr(fiscal, "selecionar_itens_por_pagina", lambda page, n: None)
    monkeypatch.setattr(fiscal, "expandir_linhas", lambda page: None)
    monkeypatch.setattr(fiscal, "navegar", lambda page, url, **k: page.goto(url))
    monkeypatch.setattr(fiscal, "aguardar_rede", lambda page, **k: None)


def usar_leitor(monkeypatch, nome, funcao):
    """Substitui um leitor que a 8B2 moveu para a integracao."""
    monkeypatch.setattr(fiscal, nome, funcao)


def linhas_ficticias(quantidade):
    return [{"cnpj": CNPJ, "tipo": f"TIPO {n}", "saldo": f"{n}00,00"}
            for n in range(1, quantidade + 1)]


# ── A · situacao lida no portal ───────────────────────────────────────────────

def test_a_sem_pendencia_grava_as_duas_colunas_e_nao_navega(escritas):
    pagina = PaginaFiscal(texto_status="Sem pendência")

    assert eventos.SEM_DEBITOS_REGISTRADO in verificar_pendencias(sessao_de(pagina), escritas)
    assert escritas == [("D", "Sem débitos"), ("E", "Sem Processos")]
    assert pagina.cliques == []


def test_a_com_pendencia_sem_botao_nenhum_e_nao_compensavel(escritas):
    pagina = PaginaFiscal(texto_status="Com pendência")

    assert eventos.DEBITOS_NAO_COMPENSAVEIS_REGISTRADO in verificar_pendencias(
        sessao_de(pagina), escritas)
    assert escritas == [("D", "Débitos não compensáveis"), ("E", "Sem Processos")]


def test_q_status_nao_reconhecido_nao_grava_nada(escritas, capsys):
    """FISCAL_UNKNOWN_SEMANTICS: "desconhecido" NAO e um estado que o portal
    informe. E o `else` de qualquer texto que o codigo nao entendeu — inclusive
    um texto novo que a Receita venha a usar.

    A consequencia: a linha nao e marcada e volta pendente em toda execucao.
    """
    pagina = PaginaFiscal(texto_status="Situação em análise")

    assert verificar_pendencias(sessao_de(pagina), escritas) == [
        eventos.SITUACAO_FISCAL_NAO_RECONHECIDA]
    assert escritas == [], "nada gravado — a linha volta"

    # SECURITY_BEHAVIOR_CHANGE (8B1): a mensagem antiga era
    # `Status não reconhecido: '{texto}'` e ecoava o texto bruto do portal. Este
    # ponto de print e novo, entao a mensagem e constante. A perda de diagnostico
    # esta declarada no relatorio: nao da mais para descobrir pelo log QUAL texto
    # o portal passou a usar.
    assert capsys.readouterr().out == "", "o app não imprime; ele emite o fato"

    frase = apresentacao_eventos.frase(
        eventos.EventoOperacional(eventos.SITUACAO_FISCAL_NAO_RECONHECIDA, posicao=0)
    )
    assert "não reconhecido" in frase
    assert "Situação em análise" not in frase


@pytest.mark.parametrize("texto", ["", "  Sem pendência  ", "sem pendência", "SEM PENDÊNCIA"])
def test_q_a_comparacao_de_status_e_por_igualdade_exata(escritas, texto):
    """PORTAL_STATUS_POSSIBLE_DEFECT: caixa e espaco interno mudam o desfecho.
    So `.strip()` e aplicado — nao ha normalizacao de caixa nem de acento, ao
    contrario da regra de recusa da fatia 2."""
    pagina = PaginaFiscal(texto_status=texto)

    resultado = verificar_pendencias(sessao_de(pagina), escritas)

    esperado = ([eventos.SEM_DEBITOS_REGISTRADO, eventos.SEM_PROCESSOS_REGISTRADO]
                if texto.strip() == "Sem pendência"
                else [eventos.SITUACAO_FISCAL_NAO_RECONHECIDA])
    assert resultado == esperado


# ── I · skips ─────────────────────────────────────────────────────────────────

def test_i_skip_dctfweb_nao_grava_a_coluna_d(escritas):
    pagina = PaginaFiscal(texto_status="Sem pendência")

    verificar_pendencias(sessao_de(pagina), escritas, skip_dctfweb=True)

    assert escritas == [("E", "Sem Processos")]


def test_i_skip_processo_nao_grava_a_coluna_e(escritas):
    pagina = PaginaFiscal(texto_status="Sem pendência")

    verificar_pendencias(sessao_de(pagina), escritas, skip_processo=True)

    assert escritas == [("D", "Sem débitos")]


def test_i_os_skips_vem_da_planilha_e_nao_do_portal():
    """RESUMABILITY: sao decisao de RETOMADA — nascem das colunas D e E lidas
    antes de qualquer navegacao, em processar_cnpj."""
    import inspect

    fonte = inspect.getsource(app._processar_item)

    assert "planilha.retomada(" in fonte
    assert "retomada.dctfweb_feito" in inspect.getsource(app._consultar_situacao)
    assert "retomada.processos_feitos" in inspect.getsource(app._consultar_situacao)


# ── G · DCTFWeb ───────────────────────────────────────────────────────────────

def test_g_dctfweb_extrai_e_so_entao_grava(escritas, monkeypatch):
    """O corte da fatia: a gravacao sao as DUAS ULTIMAS linhas, depois de toda a
    paginacao terminar."""
    usar_leitor(monkeypatch, "_linhas_da_tabela_dctfweb",
                lambda page, cnpj: linhas_ficticias(3))
    pagina = PaginaFiscal()

    extrair_debitos(sessao_de(pagina), escritas)

    assert escritas == [("aba Débitos", 3), ("D", "Concluído")]


def test_g_dctfweb_sem_linhas_ainda_grava_concluido(escritas, monkeypatch):
    usar_leitor(monkeypatch, "_linhas_da_tabela_dctfweb", lambda page, cnpj: [])
    pagina = PaginaFiscal()

    extrair_debitos(sessao_de(pagina), escritas)

    assert escritas == [("aba Débitos", 0), ("D", "Concluído")]


def test_g_dctfweb_pagina_enquanto_houver_proxima(escritas, monkeypatch):
    """A paginacao acumula tudo em memoria antes de gravar uma vez so."""
    paginas = [linhas_ficticias(2), linhas_ficticias(1)]
    usar_leitor(monkeypatch, "_linhas_da_tabela_dctfweb",
                lambda page, cnpj: paginas.pop(0))

    class ComDuasPaginas(PaginaFiscal):
        def __init__(self):
            super().__init__()
            self.restantes = 1

        def locator(self, seletor):
            loc = super().locator(seletor)
            if "Página seguinte" in seletor:
                loc.desabilitado = self.restantes <= 0
                self.restantes -= 1
            return loc

    extrair_debitos(sessao_de(ComDuasPaginas()), escritas)

    assert escritas == [("aba Débitos", 3), ("D", "Concluído")], "uma gravacao so"


# ── H · Processos Fiscais ─────────────────────────────────────────────────────

def test_h_processos_sem_cards_ainda_grava_concluido(escritas):
    pagina = PaginaFiscal(cards=0)

    extrair_processos(sessao_de(pagina), escritas)

    assert escritas == [("aba Processos", 0), ("E", "Concluído")]


def test_h_processos_percorre_cada_card_e_volta(escritas, monkeypatch):
    usar_leitor(monkeypatch, "_linhas_do_card",
                lambda page, cnpj, avisos: linhas_ficticias(2))
    pagina = PaginaFiscal(cards=3)

    extrair_processos(sessao_de(pagina), escritas)

    assert escritas == [("aba Processos", 6), ("E", "Concluído")]
    assert pagina.voltas == 3, "um go_back por card"


# ── P · a orquestracao com pendencia ──────────────────────────────────────────

def test_p_com_pendencia_e_os_dois_botoes(escritas, monkeypatch):
    monkeypatch.setattr(app, "_extrair_debitos",
                        lambda execucao, item: escritas.append(("dctfweb", "extraiu")))
    monkeypatch.setattr(app, "_extrair_processos",
                        lambda execucao, item: escritas.append(("processos", "extraiu")))
    pagina = PaginaFiscal(texto_status="Com pendência", tem_dctfweb=True, tem_processo=True)

    assert eventos.SITUACAO_FISCAL_NAO_RECONHECIDA not in verificar_pendencias(
        sessao_de(pagina), escritas)
    assert escritas == [("dctfweb", "extraiu"), ("processos", "extraiu")]


def test_p_a_ida_para_analise_fiscal_acompanhou_os_processos(escritas, monkeypatch):
    """A navegacao para a URL de analise saiu de `verificar_pendencias` e entrou
    em `consultar_processos` — mesma sequencia, outra funcao. Ela acontece
    imediatamente antes de clicar no botao, como antes."""
    usar_leitor(monkeypatch, "_linhas_do_card", lambda page, cnpj, avisos: [])
    pagina = PaginaFiscal(cards=0)

    extrair_processos(sessao_de(pagina), escritas)

    assert pagina.navegacoes == [fiscal.URL_ANALISE_PENDENCIAS]


def test_p_so_processos_marca_a_coluna_d_como_concluida(escritas, monkeypatch):
    """Sem botao de DCTFWeb, a coluna D e marcada pelo caminho de Processos."""
    monkeypatch.setattr(app, "_extrair_processos", lambda execucao, item: None)
    pagina = PaginaFiscal(texto_status="Com pendência", tem_dctfweb=False, tem_processo=True)

    assert eventos.SITUACAO_FISCAL_NAO_RECONHECIDA not in verificar_pendencias(
        sessao_de(pagina), escritas)
    assert escritas == [("D", "Concluído")]


def test_p_so_dctfweb_marca_e_como_sem_processos(escritas, monkeypatch):
    monkeypatch.setattr(app, "_extrair_debitos", lambda execucao, item: None)
    pagina = PaginaFiscal(texto_status="Com pendência", tem_dctfweb=True, tem_processo=False)

    assert eventos.SITUACAO_FISCAL_NAO_RECONHECIDA not in verificar_pendencias(
        sessao_de(pagina), escritas)
    assert escritas == [("E", "Sem Processos")]


# ── V · a ordem preserva a retomada parcial ───────────────────────────────────

def test_v_dctfweb_grava_a_coluna_d_ANTES_de_processos_comecar(escritas, monkeypatch):
    """RESUMABILITY_CONTRACT — a propriedade que nao pode ser destruida.

    Se Processos falhar depois de DCTFWeb ter terminado, a coluna D ja esta
    marcada em memoria e o `finally` a grava. A proxima execucao faz SO
    Processos. Juntar as duas gravacoes no fim quebraria exatamente isso.
    """
    usar_leitor(monkeypatch, "_linhas_da_tabela_dctfweb",
                lambda page, cnpj: linhas_ficticias(1))

    def processos_falham(execucao, item):
        raise RuntimeError("portal caiu no meio dos processos")

    monkeypatch.setattr(app, "_extrair_processos", processos_falham)
    pagina = PaginaFiscal(texto_status="Com pendência", tem_dctfweb=True, tem_processo=True)

    with pytest.raises(RuntimeError):
        verificar_pendencias(sessao_de(pagina), escritas)

    assert escritas == [("aba Débitos", 1), ("D", "Concluído")], "o DCTFWeb sobreviveu"


# ── S · bug nosso sobe ────────────────────────────────────────────────────────

@pytest.mark.parametrize("erro", [KeyError("k"), IndexError("i"), AttributeError("a")])
def test_s_bug_na_extracao_nao_vira_status(escritas, monkeypatch, erro):
    """Nenhum KeyError/IndexError vira "sem débitos" ou "desconhecido"."""
    def quebrar(page, cnpj):
        raise erro

    usar_leitor(monkeypatch, "_linhas_da_tabela_dctfweb", quebrar)

    with pytest.raises(type(erro)):
        extrair_debitos(sessao_de(PaginaFiscal()), escritas)

    assert escritas == [], "nada foi gravado"
