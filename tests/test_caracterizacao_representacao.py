"""Caracterizacao da representacao COMO ELA E HOJE.

`trocar_perfil_procurador` tem 348 linhas — a maior funcao do projeto. Nenhum
teste aqui abre navegador, portal, captcha ou Gemini: a Page falsa tem so os
metodos que aquela funcao realmente chama.

CNPJs e mensagens sao ficticios.
"""
import pytest
from navegador_falso import PaginaDeRepresentacao

import main

CNPJ = "11111111000191"
CNPJ_FORMATADO = "11.111.111/0001-91"
OUTRO_CNPJ = "22222222000172"


class Relogio:
    """Tempo controlado.

    A espera ativa da representacao roda `while time.time() < deadline` com 60s
    de limite. Sem um relogio controlado, cada tentativa gastaria 60s de relogio
    de parede — 3 minutos so para caracterizar um desfecho.
    """

    def __init__(self, passo=1.0):
        self.agora = 1_000_000.0
        self.passo = passo

    def __call__(self):
        self.agora += self.passo
        return self.agora


@pytest.fixture(autouse=True)
def sem_esperas(monkeypatch):
    """O intervalo de 30s entre trocas e os sleeps nao existem nos testes."""
    monkeypatch.setattr(main, "_ultimo_troca_cnpj", 0.0)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    monkeypatch.setattr(main.time, "time", Relogio())
    monkeypatch.setattr(main, "_goto_seguro", lambda page, url, **k: page.goto(url))
    monkeypatch.setattr(main, "fechar_tutorial_pos_login", lambda page, **k: False)
    monkeypatch.setattr(main, "_resolver_captcha", lambda alvo, aguardar=None: "não deveria")


# ── H · o desfecho de sucesso ─────────────────────────────────────────────────

def test_h_representacao_confirmada_navega_para_pendencias():
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=CNPJ_FORMATADO)

    assert main.trocar_perfil_procurador(pagina, CNPJ) is None
    assert pagina.navegacoes == [main.URL_PENDENCIAS]


def test_h_o_sucesso_nao_devolve_nada():
    """O contrato de hoje: sucesso e `None`, exatamente como falha de login era."""
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=CNPJ_FORMATADO)

    assert main.trocar_perfil_procurador(pagina, CNPJ) is None


def test_k_o_cnpj_e_preenchido_no_campo_de_representacao():
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=CNPJ_FORMATADO)

    main.trocar_perfil_procurador(pagina, CNPJ)

    campos = [valor for _, valor in pagina.preenchidos]
    assert campos == [CNPJ]


def test_k_a_confirmacao_compara_so_os_digitos():
    """O cabecalho vem formatado; a comparacao normaliza os dois lados."""
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=f"CNPJ: {CNPJ_FORMATADO} — ALFA")

    assert main.trocar_perfil_procurador(pagina, CNPJ) is None


def test_l_o_intervalo_de_trinta_segundos_e_respeitado(monkeypatch):
    """O portal recusa trocas com menos de 30s. O cronometro parte do clique em
    'Representar', nao do fim da operacao."""
    esperas = []
    parado = Relogio(passo=0.0)
    monkeypatch.setattr(main.time, "sleep", lambda s: esperas.append(s))
    monkeypatch.setattr(main.time, "time", parado)
    monkeypatch.setattr(main, "_ultimo_troca_cnpj", parado())   # a troca foi "agora"

    main.trocar_perfil_procurador(PaginaDeRepresentacao(CNPJ_FORMATADO), CNPJ)

    assert len(esperas) == 1
    assert 0 < esperas[0] <= main._INTERVALO_TROCA


# ── H · recusa do CNPJ ────────────────────────────────────────────────────────

def test_h_recusa_conhecida_levanta_falha_permanente_com_status():
    """Reutiliza a classificacao da fatia 2 — nenhum texto duplicado aqui."""
    pagina = PaginaDeRepresentacao(
        mensagem_erro="Sua autorização como procurador não permite acesso a este serviço"
    )

    with pytest.raises(main.FalhaPermanente) as erro:
        main.trocar_perfil_procurador(pagina, CNPJ)

    assert erro.value.status_coluna_d == "Procuração sem autorização"


def test_h_recusa_sem_status_proprio_tambem_e_permanente():
    pagina = PaginaDeRepresentacao(mensagem_erro="Procuração vencida")

    with pytest.raises(main.FalhaPermanente) as erro:
        main.trocar_perfil_procurador(pagina, CNPJ)

    assert erro.value.status_coluna_d is None


def test_h_a_recusa_nao_gasta_as_tres_tentativas():
    """FalhaPermanente sobe na primeira: retentar uma procuracao vencida e inutil."""
    pagina = PaginaDeRepresentacao(mensagem_erro="Procuração vencida")

    with pytest.raises(main.FalhaPermanente):
        main.trocar_perfil_procurador(pagina, CNPJ)

    assert pagina.preenchidos == [("xpath=//*[@id=\"input-representar-cpfcnpj\"]", CNPJ)]


# ── H · anti-bot ──────────────────────────────────────────────────────────────

def test_h_anti_bot_gasta_as_tres_tentativas_e_levanta_generica():
    pagina = PaginaDeRepresentacao(mensagem_erro="Detectamos acesso automatizado")

    with pytest.raises(Exception, match="anti-bot") as erro:
        main.trocar_perfil_procurador(pagina, CNPJ)

    assert not isinstance(erro.value, main.FalhaPermanente)
    assert len(pagina.preenchidos) == 3, "as tres tentativas foram gastas"


def test_o_a_classificacao_anti_bot_vem_do_status_portal():
    """Nenhuma duplicacao de "automatizado"/"bloqueado" no main."""
    import pathlib

    fonte = (pathlib.Path(main.__file__)).read_text(encoding="utf-8-sig")

    assert '"automatizado" in' not in fonte
    assert fonte.count("_classificar_mensagem(") == 2


# ── H · CNPJ nao confirmado ───────────────────────────────────────────────────

def test_h_cnpj_nao_confirmado_esgota_as_tentativas():
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=OUTRO_CNPJ)

    with pytest.raises(Exception, match="Representação falhou") as erro:
        main.trocar_perfil_procurador(pagina, CNPJ)

    assert not isinstance(erro.value, main.FalhaPermanente)
    assert len(pagina.preenchidos) == 3


def test_h_a_mensagem_de_falha_ecoa_o_cnpj_e_o_cabecalho():
    """LOGIN/NAVIGATION: a mensagem carrega o CNPJ e o texto do portal.
    Caracterizado, nao corrigido — e o legado que a levanta."""
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=OUTRO_CNPJ)

    with pytest.raises(Exception) as erro:
        main.trocar_perfil_procurador(pagina, CNPJ)

    assert CNPJ in str(erro.value)
    assert OUTRO_CNPJ in str(erro.value)


# ── M · N · esperas e retries ─────────────────────────────────────────────────

def test_m_as_esperas_entre_tentativas_sao_preservadas():
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=OUTRO_CNPJ)

    with pytest.raises(Exception, match="Representação falhou"):
        main.trocar_perfil_procurador(pagina, CNPJ)

    assert 500 in pagina.esperas, "escape do dropdown"
    assert 2_000 in pagina.esperas, "pausa entre tentativas"
    assert pagina.keyboard.teclas.count("Escape") == 2, "uma por retentativa"


def test_n_a_confirmacao_do_cnpj_sonda_dez_vezes():
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=OUTRO_CNPJ)

    with pytest.raises(Exception, match="Representação falhou"):
        main.trocar_perfil_procurador(pagina, CNPJ)

    # 10 sondagens por tentativa, 3 tentativas.
    assert pagina.esperas.count(500) >= 30


def test_n_o_maximo_de_tentativas_de_representacao():
    import pathlib

    fonte = pathlib.Path(main.__file__).read_text(encoding="utf-8-sig")
    assert "_MAX_TENTATIVAS_REPR = 3" in fonte


# ── R · efeito: estado de sessao, nao persistente ─────────────────────────────

def test_r_representar_so_muda_o_perfil_ativo_da_sessao():
    """SESSION_STATE_CHANGE: nada e protocolado, nada e enviado. A prova esta no
    proprio uso — CNPJs sao representados em sequencia na MESMA sessao, sem
    nenhuma limpeza entre eles."""
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=CNPJ_FORMATADO)
    main.trocar_perfil_procurador(pagina, CNPJ)

    pagina.cnpj_no_cabecalho = "22.222.222/0001-72"
    assert main.trocar_perfil_procurador(pagina, OUTRO_CNPJ) is None


def test_r_a_representacao_nao_fecha_a_sessao():
    """A integracao nao e dona da sessao — nem no caminho de falha."""
    import pathlib

    fonte = pathlib.Path(main.__file__).read_text(encoding="utf-8-sig")
    inicio = fonte.index("def trocar_perfil_procurador")
    trecho = fonte[inicio:fonte.index("def verificar_pendencias")]

    for proibido in ("context.close", "p.stop", "_fechar_navegador", "_fazer_logout"):
        assert proibido not in trecho, f"a representacao chama {proibido}."
