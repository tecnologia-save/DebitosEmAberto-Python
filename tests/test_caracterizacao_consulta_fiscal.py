"""Caracterizacao da consulta fiscal COMO ELA E HOJE.

Nenhum teste abre navegador ou portal. A Page falsa tem so os metodos que estas
funcoes realmente chamam, e as escritas na planilha sao ESPIONADAS — e a
orquestracao entre "o que foi lido" e "o que foi gravado" que precisa ficar
registrada para a fatia de persistencia nao ter de adivinhar.

Dados fiscais totalmente ficticios.
"""
import pytest
from navegador_falso import PaginaFiscal

import main
from automation import consulta_fiscal as fiscal

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


@pytest.fixture
def escritas(monkeypatch):
    """Registra toda gravacao que o fluxo fiscal dispara, na ordem.

    CHARACTERIZATION_TARGET_CHANGE (fatia 9B): antes a substituicao era nos
    wrappers de coluna do `main`, que sumiram. Agora e nas PRIMITIVAS da sessao
    de planilha — celula e aba. A troca fortalece o teste em vez de enfraquece-lo:
    a ordem "detalhe primeiro, status depois" passou a ser exercitada no codigo
    de verdade (`registrar_debitos`), e nao mais no dublê.
    """
    from automation import planilha as _planilha

    registro = []
    rotulos = {_planilha.COL_STATUS_DCTFWEB: "D", _planilha.COL_STATUS_PROCESSOS: "E"}

    def escrever_status(cnpj, valor, coluna):
        registro.append((rotulos[coluna], valor))
        return True

    def anexar(rotulo):
        def anexar_dados(dados):
            registro.append((rotulo, len(dados)))
            return list(range(2, 2 + len(dados)))
        return anexar_dados

    monkeypatch.setattr(main, "_wb_sessao", lambda caminho: None)
    monkeypatch.setattr(main._SESSAO, "escrever_status", escrever_status)
    monkeypatch.setattr(main._SESSAO, "anexar_debitos", anexar("aba Débitos"))
    monkeypatch.setattr(main._SESSAO, "anexar_processos", anexar("aba Processos"))
    return registro


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

    assert main.verificar_pendencias(sessao_de(pagina), CNPJ, PLANILHA) == "sem_pendencia"
    assert escritas == [("D", "Sem débitos"), ("E", "Sem Processos")]
    assert pagina.cliques == []


def test_a_com_pendencia_sem_botao_nenhum_e_nao_compensavel(escritas):
    pagina = PaginaFiscal(texto_status="Com pendência")

    assert main.verificar_pendencias(sessao_de(pagina), CNPJ, PLANILHA) == "nao_compensavel"
    assert escritas == [("D", "Débitos não compensáveis"), ("E", "Sem Processos")]


def test_q_status_nao_reconhecido_nao_grava_nada(escritas, capsys):
    """FISCAL_UNKNOWN_SEMANTICS: "desconhecido" NAO e um estado que o portal
    informe. E o `else` de qualquer texto que o codigo nao entendeu — inclusive
    um texto novo que a Receita venha a usar.

    A consequencia: a linha nao e marcada e volta pendente em toda execucao.
    """
    pagina = PaginaFiscal(texto_status="Situação em análise")

    assert main.verificar_pendencias(sessao_de(pagina), CNPJ, PLANILHA) == "desconhecido"
    assert escritas == [], "nada gravado — a linha volta"

    # SECURITY_BEHAVIOR_CHANGE (8B1): a mensagem antiga era
    # `Status não reconhecido: '{texto}'` e ecoava o texto bruto do portal. Este
    # ponto de print e novo, entao a mensagem e constante. A perda de diagnostico
    # esta declarada no relatorio: nao da mais para descobrir pelo log QUAL texto
    # o portal passou a usar.
    saida = capsys.readouterr().out
    assert "não reconhecido" in saida
    assert "Situação em análise" not in saida


@pytest.mark.parametrize("texto", ["", "  Sem pendência  ", "sem pendência", "SEM PENDÊNCIA"])
def test_q_a_comparacao_de_status_e_por_igualdade_exata(escritas, texto):
    """PORTAL_STATUS_POSSIBLE_DEFECT: caixa e espaco interno mudam o desfecho.
    So `.strip()` e aplicado — nao ha normalizacao de caixa nem de acento, ao
    contrario da regra de recusa da fatia 2."""
    pagina = PaginaFiscal(texto_status=texto)

    resultado = main.verificar_pendencias(sessao_de(pagina), CNPJ, PLANILHA)

    assert resultado == ("sem_pendencia" if texto.strip() == "Sem pendência" else "desconhecido")


# ── I · skips ─────────────────────────────────────────────────────────────────

def test_i_skip_dctfweb_nao_grava_a_coluna_d(escritas):
    pagina = PaginaFiscal(texto_status="Sem pendência")

    main.verificar_pendencias(sessao_de(pagina), CNPJ, PLANILHA, skip_dctfweb=True)

    assert escritas == [("E", "Sem Processos")]


def test_i_skip_processo_nao_grava_a_coluna_e(escritas):
    pagina = PaginaFiscal(texto_status="Sem pendência")

    main.verificar_pendencias(sessao_de(pagina), CNPJ, PLANILHA, skip_processo=True)

    assert escritas == [("D", "Sem débitos")]


def test_i_os_skips_vem_da_planilha_e_nao_do_portal():
    """RESUMABILITY: sao decisao de RETOMADA — nascem das colunas D e E lidas
    antes de qualquer navegacao, em processar_cnpj."""
    import inspect

    fonte = inspect.getsource(main.processar_cnpj)

    assert "skip_dctfweb  = retomada.dctfweb_feito" in fonte
    assert "skip_processo = retomada.processos_feitos" in fonte
    assert "retomada_da_linha" in fonte


# ── G · DCTFWeb ───────────────────────────────────────────────────────────────

def test_g_dctfweb_extrai_e_so_entao_grava(escritas, monkeypatch):
    """O corte da fatia: a gravacao sao as DUAS ULTIMAS linhas, depois de toda a
    paginacao terminar."""
    usar_leitor(monkeypatch, "_linhas_da_tabela_dctfweb",
                lambda page, cnpj: linhas_ficticias(3))
    pagina = PaginaFiscal()

    main.extrair_debitos_dctfweb(sessao_de(pagina), CNPJ, PLANILHA)

    assert escritas == [("aba Débitos", 3), ("D", "Concluído")]


def test_g_dctfweb_sem_linhas_ainda_grava_concluido(escritas, monkeypatch):
    usar_leitor(monkeypatch, "_linhas_da_tabela_dctfweb", lambda page, cnpj: [])
    pagina = PaginaFiscal()

    main.extrair_debitos_dctfweb(sessao_de(pagina), CNPJ, PLANILHA)

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

    main.extrair_debitos_dctfweb(sessao_de(ComDuasPaginas()), CNPJ, PLANILHA)

    assert escritas == [("aba Débitos", 3), ("D", "Concluído")], "uma gravacao so"


# ── H · Processos Fiscais ─────────────────────────────────────────────────────

def test_h_processos_sem_cards_ainda_grava_concluido(escritas):
    pagina = PaginaFiscal(cards=0)

    main.extrair_processo_fiscal(sessao_de(pagina), CNPJ, PLANILHA)

    assert escritas == [("aba Processos", 0), ("E", "Concluído")]


def test_h_processos_percorre_cada_card_e_volta(escritas, monkeypatch):
    usar_leitor(monkeypatch, "_linhas_do_card",
                lambda page, cnpj, avisos: linhas_ficticias(2))
    pagina = PaginaFiscal(cards=3)

    main.extrair_processo_fiscal(sessao_de(pagina), CNPJ, PLANILHA)

    assert escritas == [("aba Processos", 6), ("E", "Concluído")]
    assert pagina.voltas == 3, "um go_back por card"


# ── P · a orquestracao com pendencia ──────────────────────────────────────────

def test_p_com_pendencia_e_os_dois_botoes(escritas, monkeypatch):
    monkeypatch.setattr(main, "extrair_debitos_dctfweb",
                        lambda sessao, cnpj, caminho: escritas.append(("dctfweb", "extraiu")))
    monkeypatch.setattr(main, "extrair_processo_fiscal",
                        lambda sessao, cnpj, caminho: escritas.append(("processos", "extraiu")))
    pagina = PaginaFiscal(texto_status="Com pendência", tem_dctfweb=True, tem_processo=True)

    assert main.verificar_pendencias(sessao_de(pagina), CNPJ, PLANILHA) == "concluido"
    assert escritas == [("dctfweb", "extraiu"), ("processos", "extraiu")]


def test_p_a_ida_para_analise_fiscal_acompanhou_os_processos(escritas, monkeypatch):
    """A navegacao para a URL de analise saiu de `verificar_pendencias` e entrou
    em `consultar_processos` — mesma sequencia, outra funcao. Ela acontece
    imediatamente antes de clicar no botao, como antes."""
    usar_leitor(monkeypatch, "_linhas_do_card", lambda page, cnpj, avisos: [])
    pagina = PaginaFiscal(cards=0)

    main.extrair_processo_fiscal(sessao_de(pagina), CNPJ, PLANILHA)

    assert pagina.navegacoes == [fiscal.URL_ANALISE_PENDENCIAS]


def test_p_so_processos_marca_a_coluna_d_como_concluida(escritas, monkeypatch):
    """Sem botao de DCTFWeb, a coluna D e marcada pelo caminho de Processos."""
    monkeypatch.setattr(main, "extrair_processo_fiscal", lambda s, c, p: None)
    pagina = PaginaFiscal(texto_status="Com pendência", tem_dctfweb=False, tem_processo=True)

    assert main.verificar_pendencias(sessao_de(pagina), CNPJ, PLANILHA) == "concluido"
    assert escritas == [("D", "Concluído")]


def test_p_so_dctfweb_marca_e_como_sem_processos(escritas, monkeypatch):
    monkeypatch.setattr(main, "extrair_debitos_dctfweb", lambda s, c, p: None)
    pagina = PaginaFiscal(texto_status="Com pendência", tem_dctfweb=True, tem_processo=False)

    assert main.verificar_pendencias(sessao_de(pagina), CNPJ, PLANILHA) == "concluido"
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

    def processos_falham(sessao, cnpj, caminho):
        raise RuntimeError("portal caiu no meio dos processos")

    monkeypatch.setattr(main, "extrair_processo_fiscal", processos_falham)
    pagina = PaginaFiscal(texto_status="Com pendência", tem_dctfweb=True, tem_processo=True)

    with pytest.raises(RuntimeError):
        main.verificar_pendencias(sessao_de(pagina), CNPJ, PLANILHA)

    assert escritas == [("aba Débitos", 1), ("D", "Concluído")], "o DCTFWeb sobreviveu"


# ── S · bug nosso sobe ────────────────────────────────────────────────────────

@pytest.mark.parametrize("erro", [KeyError("k"), IndexError("i"), AttributeError("a")])
def test_s_bug_na_extracao_nao_vira_status(escritas, monkeypatch, erro):
    """Nenhum KeyError/IndexError vira "sem débitos" ou "desconhecido"."""
    def quebrar(page, cnpj):
        raise erro

    usar_leitor(monkeypatch, "_linhas_da_tabela_dctfweb", quebrar)

    with pytest.raises(type(erro)):
        main.extrair_debitos_dctfweb(sessao_de(PaginaFiscal()), CNPJ, PLANILHA)

    assert escritas == [], "nada foi gravado"
