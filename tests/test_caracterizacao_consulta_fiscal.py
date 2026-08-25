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

CNPJ = "11111111000191"
PLANILHA = "C:/nao/existe/base.xlsx"


@pytest.fixture
def escritas(monkeypatch):
    """Registra toda gravacao que o fluxo fiscal dispara, na ordem."""
    registro = []

    monkeypatch.setattr(
        main, "escrever_coluna_d",
        lambda caminho, cnpj, valor: registro.append(("D", valor)),
    )
    monkeypatch.setattr(
        main, "escrever_coluna_e",
        lambda caminho, cnpj, valor: registro.append(("E", valor)),
    )
    monkeypatch.setattr(
        main, "escrever_aba_debitos",
        lambda caminho, dados: registro.append(("aba Débitos", len(dados))),
    )
    monkeypatch.setattr(
        main, "escrever_aba_processos_fiscais",
        lambda caminho, dados: registro.append(("aba Processos", len(dados))),
    )
    return registro


@pytest.fixture(autouse=True)
def sem_navegacao_real(monkeypatch):
    monkeypatch.setattr(main, "_aguardar_networkidle", lambda page, **k: None)
    monkeypatch.setattr(main, "_selecionar_n_por_pagina", lambda page, n: None)
    monkeypatch.setattr(main, "_expandir_todas_as_linhas", lambda page: None)
    monkeypatch.setattr(main, "_goto_seguro", lambda page, url, **k: page.goto(url))


def linhas_ficticias(quantidade):
    return [{"cnpj": CNPJ, "tipo": f"TIPO {n}", "saldo": f"{n}00,00"}
            for n in range(1, quantidade + 1)]


# ── A · situacao lida no portal ───────────────────────────────────────────────

def test_a_sem_pendencia_grava_as_duas_colunas_e_nao_navega(escritas):
    pagina = PaginaFiscal(texto_status="Sem pendência")

    assert main.verificar_pendencias(pagina, CNPJ, PLANILHA) == "sem_pendencia"
    assert escritas == [("D", "Sem débitos"), ("E", "Sem Processos")]
    assert pagina.cliques == []


def test_a_com_pendencia_sem_botao_nenhum_e_nao_compensavel(escritas):
    pagina = PaginaFiscal(texto_status="Com pendência")

    assert main.verificar_pendencias(pagina, CNPJ, PLANILHA) == "nao_compensavel"
    assert escritas == [("D", "Débitos não compensáveis"), ("E", "Sem Processos")]


def test_q_status_nao_reconhecido_nao_grava_nada(escritas, capsys):
    """FISCAL_UNKNOWN_SEMANTICS: "desconhecido" NAO e um estado que o portal
    informe. E o `else` de qualquer texto que o codigo nao entendeu — inclusive
    um texto novo que a Receita venha a usar.

    A consequencia: a linha nao e marcada e volta pendente em toda execucao.
    """
    pagina = PaginaFiscal(texto_status="Situação em análise")

    assert main.verificar_pendencias(pagina, CNPJ, PLANILHA) == "desconhecido"
    assert escritas == [], "nada gravado — a linha volta"
    assert "Status não reconhecido" in capsys.readouterr().out


@pytest.mark.parametrize("texto", ["", "  Sem pendência  ", "sem pendência", "SEM PENDÊNCIA"])
def test_q_a_comparacao_de_status_e_por_igualdade_exata(escritas, texto):
    """PORTAL_STATUS_POSSIBLE_DEFECT: caixa e espaco interno mudam o desfecho.
    So `.strip()` e aplicado — nao ha normalizacao de caixa nem de acento, ao
    contrario da regra de recusa da fatia 2."""
    pagina = PaginaFiscal(texto_status=texto)

    resultado = main.verificar_pendencias(pagina, CNPJ, PLANILHA)

    assert resultado == ("sem_pendencia" if texto.strip() == "Sem pendência" else "desconhecido")


# ── I · skips ─────────────────────────────────────────────────────────────────

def test_i_skip_dctfweb_nao_grava_a_coluna_d(escritas):
    pagina = PaginaFiscal(texto_status="Sem pendência")

    main.verificar_pendencias(pagina, CNPJ, PLANILHA, skip_dctfweb=True)

    assert escritas == [("E", "Sem Processos")]


def test_i_skip_processo_nao_grava_a_coluna_e(escritas):
    pagina = PaginaFiscal(texto_status="Sem pendência")

    main.verificar_pendencias(pagina, CNPJ, PLANILHA, skip_processo=True)

    assert escritas == [("D", "Sem débitos")]


def test_i_os_skips_vem_da_planilha_e_nao_do_portal():
    """RESUMABILITY: sao decisao de RETOMADA — nascem das colunas D e E lidas
    antes de qualquer navegacao, em processar_cnpj."""
    import inspect

    fonte = inspect.getsource(main.processar_cnpj)

    assert "skip_dctfweb  = bool(val_d)" in fonte
    assert "skip_processo = bool(val_e)" in fonte
    assert "ler_status_cnpj" in fonte


# ── G · DCTFWeb ───────────────────────────────────────────────────────────────

def test_g_dctfweb_extrai_e_so_entao_grava(escritas, monkeypatch):
    """O corte da fatia: a gravacao sao as DUAS ULTIMAS linhas, depois de toda a
    paginacao terminar."""
    monkeypatch.setattr(main, "_extrair_dados_pagina", lambda page, cnpj: linhas_ficticias(3))
    pagina = PaginaFiscal()

    main.extrair_debitos_dctfweb(pagina, CNPJ, PLANILHA)

    assert escritas == [("aba Débitos", 3), ("D", "Concluído")]


def test_g_dctfweb_sem_linhas_ainda_grava_concluido(escritas, monkeypatch):
    monkeypatch.setattr(main, "_extrair_dados_pagina", lambda page, cnpj: [])
    pagina = PaginaFiscal()

    main.extrair_debitos_dctfweb(pagina, CNPJ, PLANILHA)

    assert escritas == [("aba Débitos", 0), ("D", "Concluído")]


def test_g_dctfweb_pagina_enquanto_houver_proxima(escritas, monkeypatch):
    """A paginacao acumula tudo em memoria antes de gravar uma vez so."""
    paginas = [linhas_ficticias(2), linhas_ficticias(1)]
    monkeypatch.setattr(main, "_extrair_dados_pagina", lambda page, cnpj: paginas.pop(0))

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

    main.extrair_debitos_dctfweb(ComDuasPaginas(), CNPJ, PLANILHA)

    assert escritas == [("aba Débitos", 3), ("D", "Concluído")], "uma gravacao so"


# ── H · Processos Fiscais ─────────────────────────────────────────────────────

def test_h_processos_sem_cards_ainda_grava_concluido(escritas):
    pagina = PaginaFiscal(cards=0)

    main.extrair_processo_fiscal(pagina, CNPJ, PLANILHA)

    assert escritas == [("aba Processos", 0), ("E", "Concluído")]


def test_h_processos_percorre_cada_card_e_volta(escritas, monkeypatch):
    monkeypatch.setattr(main, "_processar_card_processo", lambda page, cnpj: linhas_ficticias(2))
    pagina = PaginaFiscal(cards=3)

    main.extrair_processo_fiscal(pagina, CNPJ, PLANILHA)

    assert escritas == [("aba Processos", 6), ("E", "Concluído")]
    assert pagina.voltas == 3, "um go_back por card"


# ── P · a orquestracao com pendencia ──────────────────────────────────────────

def test_p_com_pendencia_e_os_dois_botoes(escritas, monkeypatch):
    monkeypatch.setattr(main, "extrair_debitos_dctfweb",
                        lambda page, cnpj, caminho: escritas.append(("dctfweb", "extraiu")))
    monkeypatch.setattr(main, "extrair_processo_fiscal",
                        lambda page, cnpj, caminho: escritas.append(("processos", "extraiu")))
    pagina = PaginaFiscal(texto_status="Com pendência", tem_dctfweb=True, tem_processo=True)

    assert main.verificar_pendencias(pagina, CNPJ, PLANILHA) == "concluido"
    assert escritas == [("dctfweb", "extraiu"), ("processos", "extraiu")]
    assert main.URL_ANALISE_PENDENCIAS in pagina.navegacoes


def test_p_so_processos_marca_a_coluna_d_como_concluida(escritas, monkeypatch):
    """Sem botao de DCTFWeb, a coluna D e marcada pelo caminho de Processos."""
    monkeypatch.setattr(main, "extrair_processo_fiscal", lambda page, cnpj, caminho: None)
    pagina = PaginaFiscal(texto_status="Com pendência", tem_dctfweb=False, tem_processo=True)

    assert main.verificar_pendencias(pagina, CNPJ, PLANILHA) == "concluido"
    assert escritas == [("D", "Concluído")]


def test_p_so_dctfweb_marca_e_como_sem_processos(escritas, monkeypatch):
    monkeypatch.setattr(main, "extrair_debitos_dctfweb", lambda page, cnpj, caminho: None)
    pagina = PaginaFiscal(texto_status="Com pendência", tem_dctfweb=True, tem_processo=False)

    assert main.verificar_pendencias(pagina, CNPJ, PLANILHA) == "concluido"
    assert escritas == [("E", "Sem Processos")]


# ── V · a ordem preserva a retomada parcial ───────────────────────────────────

def test_v_dctfweb_grava_a_coluna_d_ANTES_de_processos_comecar(escritas, monkeypatch):
    """RESUMABILITY_CONTRACT — a propriedade que nao pode ser destruida.

    Se Processos falhar depois de DCTFWeb ter terminado, a coluna D ja esta
    marcada em memoria e o `finally` a grava. A proxima execucao faz SO
    Processos. Juntar as duas gravacoes no fim quebraria exatamente isso.
    """
    monkeypatch.setattr(main, "_extrair_dados_pagina", lambda page, cnpj: linhas_ficticias(1))

    def processos_falham(page, cnpj, caminho):
        raise RuntimeError("portal caiu no meio dos processos")

    monkeypatch.setattr(main, "extrair_processo_fiscal", processos_falham)
    pagina = PaginaFiscal(texto_status="Com pendência", tem_dctfweb=True, tem_processo=True)

    with pytest.raises(RuntimeError):
        main.verificar_pendencias(pagina, CNPJ, PLANILHA)

    assert escritas == [("aba Débitos", 1), ("D", "Concluído")], "o DCTFWeb sobreviveu"


# ── S · bug nosso sobe ────────────────────────────────────────────────────────

@pytest.mark.parametrize("erro", [KeyError("k"), IndexError("i"), AttributeError("a")])
def test_s_bug_na_extracao_nao_vira_status(escritas, monkeypatch, erro):
    """Nenhum KeyError/IndexError vira "sem débitos" ou "desconhecido"."""
    def quebrar(page, cnpj):
        raise erro

    monkeypatch.setattr(main, "_extrair_dados_pagina", quebrar)

    with pytest.raises(type(erro)):
        main.extrair_debitos_dctfweb(PaginaFiscal(), CNPJ, PLANILHA)

    assert escritas == [], "nada foi gravado"
