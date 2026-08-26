"""Caracterizacao da representacao COMO ELA E HOJE.

`trocar_perfil_procurador` tem 348 linhas — a maior funcao do projeto. Nenhum
teste aqui abre navegador, portal, captcha ou Gemini: a Page falsa tem so os
metodos que aquela funcao realmente chama.

CNPJs e mensagens sao ficticios.
"""
import pytest
from navegador_falso import PaginaDeRepresentacao

from automation import representacao
from automation.captcha import ConfigCaptcha
from automation.representacao import (
    ANTI_BOT_ESGOTADO,
    NAO_CONFIRMADO,
    RECUSA_DO_CNPJ,
    REPRESENTADO,
)

CONFIG = ConfigCaptcha(api_key="AIzaSy-SENTINELA-FICTICIA-0000")

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


def representar(pagina, cnpj=None):
    """CHARACTERIZATION_TARGET_CHANGE (fatia 8A2): as mesmas situacoes, agora na
    fronteira definitiva. O comportamento original esta em 445b9a7."""
    return representacao._executar_representacao(pagina, cnpj or CNPJ, CONFIG)


@pytest.fixture(autouse=True)
def sem_esperas(monkeypatch):
    """O intervalo de 30s entre trocas e os sleeps nao existem nos testes."""
    monkeypatch.setattr(representacao, "_ultimo_troca_cnpj", 0.0)
    monkeypatch.setattr(representacao.time, "sleep", lambda s: None)
    monkeypatch.setattr(representacao.time, "time", Relogio())
    monkeypatch.setattr(representacao.navegador, "navegar",
                        lambda page, url, **k: page.goto(url))
    monkeypatch.setattr(representacao, "_fechar_tutorial", lambda page: None)
    monkeypatch.setattr(representacao.captcha, "resolver",
                        lambda *a, **k: pytest.fail("nao deveria resolver captcha"))


# ── H · o desfecho de sucesso ─────────────────────────────────────────────────

def test_h_representacao_confirmada_navega_para_pendencias():
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=CNPJ_FORMATADO)

    assert representar(pagina).situacao == REPRESENTADO
    assert pagina.navegacoes == [representacao.URL_PENDENCIAS]


def test_h_o_sucesso_nao_devolve_nada():
    """Era `None`; hoje e um desfecho nomeado. Mesma decisao observavel."""
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=CNPJ_FORMATADO)

    assert representar(pagina).representado is True


def test_k_o_cnpj_e_preenchido_no_campo_de_representacao():
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=CNPJ_FORMATADO)

    representar(pagina)

    campos = [valor for _, valor in pagina.preenchidos]
    assert campos == [CNPJ]


def test_k_a_confirmacao_compara_so_os_digitos():
    """O cabecalho vem formatado; a comparacao normaliza os dois lados."""
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=f"CNPJ: {CNPJ_FORMATADO} — ALFA")

    assert representar(pagina).representado is True


def test_l_o_intervalo_de_trinta_segundos_e_respeitado(monkeypatch):
    """O portal recusa trocas com menos de 30s. O cronometro parte do clique em
    'Representar', nao do fim da operacao."""
    esperas = []
    parado = Relogio(passo=0.0)
    monkeypatch.setattr(representacao.time, "sleep", lambda s: esperas.append(s))
    monkeypatch.setattr(representacao.time, "time", parado)
    monkeypatch.setattr(representacao, "_ultimo_troca_cnpj", parado())

    representar(PaginaDeRepresentacao(CNPJ_FORMATADO))

    assert len(esperas) == 1
    assert 0 < esperas[0] <= representacao._INTERVALO_TROCA


# ── H · recusa do CNPJ ────────────────────────────────────────────────────────

def test_h_recusa_conhecida_levanta_falha_permanente_com_status():
    """Reutiliza a classificacao da fatia 2 — nenhum texto duplicado aqui."""
    pagina = PaginaDeRepresentacao(
        mensagem_erro="Sua autorização como procurador não permite acesso a este serviço"
    )

    resultado = representar(pagina)

    assert resultado.situacao == RECUSA_DO_CNPJ
    assert resultado.status_coluna_d == "Procuração sem autorização"


def test_h_recusa_sem_status_proprio_tambem_e_permanente():
    pagina = PaginaDeRepresentacao(mensagem_erro="Procuração vencida")

    resultado = representar(pagina)

    assert resultado.situacao == RECUSA_DO_CNPJ
    assert resultado.status_coluna_d is None


def test_h_a_recusa_nao_gasta_as_tres_tentativas():
    """FalhaPermanente sobe na primeira: retentar uma procuracao vencida e inutil."""
    pagina = PaginaDeRepresentacao(mensagem_erro="Procuração vencida")

    assert representar(pagina).situacao == RECUSA_DO_CNPJ

    assert pagina.preenchidos == [("xpath=//*[@id=\"input-representar-cpfcnpj\"]", CNPJ)]


# ── H · anti-bot ──────────────────────────────────────────────────────────────

def test_h_anti_bot_gasta_as_tres_tentativas_e_levanta_generica():
    pagina = PaginaDeRepresentacao(mensagem_erro="Detectamos acesso automatizado")

    resultado = representar(pagina)

    assert resultado.situacao == ANTI_BOT_ESGOTADO
    assert resultado.encerra_a_linha is False, "e da sessao, nao do CNPJ"
    assert len(pagina.preenchidos) == 3, "as tres tentativas foram gastas"


def test_o_a_classificacao_anti_bot_vem_do_status_portal():
    """Nenhuma duplicacao de "automatizado"/"bloqueado" no main."""
    import pathlib

    fonte = pathlib.Path(representacao.__file__).read_text(encoding="utf-8")

    assert '"automatizado" in' not in fonte
    assert fonte.count("status_portal.classificar_mensagem(") == 2


# ── H · CNPJ nao confirmado ───────────────────────────────────────────────────

def test_h_cnpj_nao_confirmado_esgota_as_tentativas():
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=OUTRO_CNPJ)

    resultado = representar(pagina)

    assert resultado.situacao == NAO_CONFIRMADO
    assert resultado.encerra_a_linha is False
    assert len(pagina.preenchidos) == 3


def test_h_a_mensagem_de_falha_ecoa_o_cnpj_e_o_cabecalho():
    """O vazamento SUMIU com a conversao em resultado.

    Era: "Representacao falhou para CNPJ X ... Portal ainda exibe: 'Y'". Hoje o
    desfecho e uma constante, e nem o CNPJ nem o texto do portal viajam nele.
    """
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=OUTRO_CNPJ)

    resultado = representar(pagina)

    assert CNPJ not in resultado.situacao
    assert OUTRO_CNPJ not in resultado.situacao


# ── M · N · esperas e retries ─────────────────────────────────────────────────

def test_m_as_esperas_entre_tentativas_sao_preservadas():
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=OUTRO_CNPJ)

    assert representar(pagina).situacao == NAO_CONFIRMADO

    assert 500 in pagina.esperas, "escape do dropdown"
    assert 2_000 in pagina.esperas, "pausa entre tentativas"
    assert pagina.keyboard.teclas.count("Escape") == 2, "uma por retentativa"


def test_n_a_confirmacao_do_cnpj_sonda_dez_vezes():
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=OUTRO_CNPJ)

    assert representar(pagina).situacao == NAO_CONFIRMADO

    # 10 sondagens por tentativa, 3 tentativas.
    assert pagina.esperas.count(500) >= 30


def test_n_o_maximo_de_tentativas_de_representacao():
    import pathlib

    fonte = pathlib.Path(representacao.__file__).read_text(encoding="utf-8")
    assert "_MAX_TENTATIVAS_REPR = 3" in fonte


# ── R · efeito: estado de sessao, nao persistente ─────────────────────────────

def test_r_representar_so_muda_o_perfil_ativo_da_sessao():
    """SESSION_STATE_CHANGE: nada e protocolado, nada e enviado. A prova esta no
    proprio uso — CNPJs sao representados em sequencia na MESMA sessao, sem
    nenhuma limpeza entre eles."""
    pagina = PaginaDeRepresentacao(cnpj_no_cabecalho=CNPJ_FORMATADO)
    representar(pagina)

    pagina.cnpj_no_cabecalho = "22.222.222/0001-72"
    assert representar(pagina, OUTRO_CNPJ).representado is True


def test_r_a_representacao_nao_fecha_a_sessao():
    """A integracao nao e dona da sessao — nem no caminho de falha."""
    import pathlib

    fonte = pathlib.Path(representacao.__file__).read_text(encoding="utf-8")
    inicio = fonte.index("def _executar_representacao")
    trecho = fonte[inicio:fonte.index("def recuperar_apos_recusa")]

    for proibido in ("context.close", "p.stop", "_fechar_navegador", "_fazer_logout"):
        assert proibido not in trecho, f"a representacao chama {proibido}."
