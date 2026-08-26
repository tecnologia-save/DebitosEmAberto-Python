"""Os helpers de navegacao, e o aviso que substituiu o print.

Nenhum navegador real: uma Page falsa com os dois metodos que eles chamam.
"""
import pytest
from patchright.sync_api import Error as ErroDoNavegador

from automation.navegador import (
    PAGINACAO_NAO_ALTERADA,
    REDE_NAO_ESTABILIZOU,
    aguardar_rede,
    navegar,
)

URL_SENTINELA = "https://portal.exemplo/sessao?token=SENTINELA-TOKEN&cnpj=11111111000191"


class Pagina:
    def __init__(self, erro_no_goto=None, erro_na_rede=None):
        self.erro_no_goto = erro_no_goto
        self.erro_na_rede = erro_na_rede
        self.url = "https://antes/"
        self.navegacoes = []

    def goto(self, url, **kwargs):
        self.navegacoes.append((url, kwargs))
        if self.erro_no_goto:
            raise self.erro_no_goto
        self.url = url

    def wait_for_load_state(self, estado, timeout=None):
        if self.erro_na_rede:
            raise self.erro_na_rede


# ── navegar ───────────────────────────────────────────────────────────────────

def test_navegar_usa_domcontentloaded_e_nao_networkidle():
    """Portais Angular nunca atingem networkidle; o conteudo e verificado pelos
    `wait_for` seguintes."""
    pagina = Pagina()

    navegar(pagina, URL_SENTINELA)

    url, kwargs = pagina.navegacoes[0]
    assert url == URL_SENTINELA
    assert kwargs["wait_until"] == "domcontentloaded"


def test_navegar_repropaga_a_falha_sem_mensagem_propria(capsys):
    """A falha ja viaja como exception — nao ha o que reemitir, e nao ha
    mensagem nossa para carregar a URL."""
    pagina = Pagina(erro_no_goto=ErroDoNavegador("Timeout 60000ms exceeded"))

    with pytest.raises(ErroDoNavegador) as erro:
        navegar(pagina, URL_SENTINELA)

    assert "SENTINELA" not in str(erro.value), "a mensagem e do navegador, nao nossa"
    assert capsys.readouterr().out == ""


def test_navegar_aceita_timeout_proprio():
    pagina = Pagina()

    navegar(pagina, URL_SENTINELA, timeout=30_000)

    assert pagina.navegacoes[0][1]["timeout"] == 30_000


def test_bug_nosso_sobe_do_navegar():
    pagina = Pagina(erro_no_goto=TypeError("bug nosso"))

    with pytest.raises(TypeError):
        navegar(pagina, URL_SENTINELA)


# ── aguardar_rede ─────────────────────────────────────────────────────────────

def test_aguardar_rede_silenciosa_quando_estabiliza():
    assert aguardar_rede(Pagina()) is None


def test_aguardar_rede_e_best_effort_e_devolve_aviso(capsys):
    """No legado isto virava um print e morria ali. Agora quem chama recebe."""
    pagina = Pagina(erro_na_rede=ErroDoNavegador("Timeout"))

    assert aguardar_rede(pagina) == REDE_NAO_ESTABILIZOU
    assert capsys.readouterr().out == ""


def test_aguardar_rede_nao_levanta():
    """Preservado: o elemento-alvo e verificado pela etapa seguinte."""
    pagina = Pagina(erro_na_rede=ErroDoNavegador("Timeout"))

    aguardar_rede(pagina)   # sem pytest.raises — nao levanta


def test_bug_nosso_sobe_do_aguardar_rede():
    pagina = Pagina(erro_na_rede=AttributeError("bug nosso"))

    with pytest.raises(AttributeError):
        aguardar_rede(pagina)


# ── Os avisos ─────────────────────────────────────────────────────────────────

def test_os_dois_avisos_nao_carregam_nada_sensivel():
    for aviso in (REDE_NAO_ESTABILIZOU, PAGINACAO_NAO_ALTERADA):
        for proibido in ("SENTINELA", "http", "11111111000191", "{"):
            assert proibido not in aviso


def test_o_aviso_de_paginacao_chega_ao_resultado_da_extracao():
    """O circuito completo do seam: a integracao observa, o resultado transporta."""
    from automation.consulta_fiscal import ExtracaoFiscal

    resultado = ExtracaoFiscal(linhas=(), avisos=(PAGINACAO_NAO_ALTERADA,))

    assert PAGINACAO_NAO_ALTERADA in resultado.avisos


def test_avisos_repetidos_nao_se_acumulam():
    """Dez paginas com o mesmo problema geram UM aviso: o operador precisa saber
    que aconteceu, nao quantas vezes."""
    from automation.consulta_fiscal import _anotar

    avisos = []
    for _ in range(10):
        _anotar(avisos, REDE_NAO_ESTABILIZOU)

    assert avisos == [REDE_NAO_ESTABILIZOU]
