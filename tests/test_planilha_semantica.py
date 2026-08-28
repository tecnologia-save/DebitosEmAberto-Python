"""A API application-facing da SessaoPlanilha.

Quem coordena a automacao pede "registre que nao ha debitos". Nao escreve o
texto, nao escolhe a coluna, nao sabe em que aba o detalhe vai parar.

Planilhas .xlsx sinteticas em tmp_path; nenhum arquivo de cliente.
"""
import pytest
from planilhas_sinteticas import ALFA, BETA, criar_planilha, ler_aba

from automation import planilha as p
from automation.planilha import SessaoPlanilha
from automation.status_portal import status_encerra_linha

# A planilha sintetica poe ALFA na linha 2, BETA na 3 e GAMA na 4 — o cabecalho
# e a 1. Desde a fatia 15 a identidade da unidade de trabalho e a LINHA, e nao o
# CNPJ; o que cada teste daqui afirma nao mudou.
LINHA_ALFA = 2
LINHA_BETA = 3
LINHA_AUSENTE = 99

AUSENTE = "99999999000199"


@pytest.fixture
def sessao(tmp_path):
    caminho = str(criar_planilha(tmp_path / "base.xlsx"))
    s = SessaoPlanilha()
    s.abrir(caminho)
    yield s, caminho
    s.descartar()


def coluna(caminho, linha, col):
    return ler_aba(caminho, "Empresas")[linha][col]


# ── Os status, pedidos por nome ───────────────────────────────────────────────

@pytest.mark.parametrize("metodo, col, esperado", [
    ("registrar_sem_debitos", 3, p.STATUS_SEM_DEBITOS),
    ("registrar_debitos_nao_compensaveis", 3, p.STATUS_DEBITOS_NAO_COMPENSAVEIS),
    ("registrar_debitos_concluidos", 3, p.STATUS_CONCLUIDO),
    ("registrar_sem_processos", 4, p.STATUS_SEM_PROCESSOS),
])
def test_cada_desfecho_grava_a_sua_celula(sessao, metodo, col, esperado):
    s, caminho = sessao

    assert getattr(s, metodo)(LINHA_ALFA) is True
    s.gravar()

    assert coluna(caminho, 1, col) == esperado


def test_a_recusa_do_portal_grava_o_status_recebido(sessao):
    """O texto vem da classificacao da recusa, nao da mensagem bruta do portal."""
    s, caminho = sessao

    assert s.registrar_recusa_do_portal(LINHA_ALFA, "Procuração sem autorização") is True
    s.gravar()

    assert coluna(caminho, 1, 3) == "Procuração sem autorização"


def test_cnpj_ausente_devolve_false_em_vez_de_levantar(sessao):
    """PLANILHA_POSSIBLE_DEFECT preservado: nada e gravado. O que mudou e que
    agora quem chama SABE — antes so havia um print."""
    s, _ = sessao

    assert s.registrar_sem_debitos(LINHA_AUSENTE) is False
    assert s.sujo is False


# ── Detalhe e status, na ordem que e o contrato ───────────────────────────────

def test_debitos_gravam_a_aba_e_depois_a_coluna(sessao):
    from planilhas_sinteticas import linhas_de_debito

    s, caminho = sessao
    registro = s.registrar_debitos(LINHA_ALFA, linhas_de_debito(ALFA[0], 2))
    s.gravar()

    assert registro.linhas == 2
    assert registro.marcado is True
    assert len(ler_aba(caminho, "Débitos")) == 3, "cabecalho + 2"
    assert coluna(caminho, 1, 3) == p.STATUS_CONCLUIDO


def test_processos_gravam_a_aba_e_depois_a_coluna(sessao):
    from planilhas_sinteticas import linhas_de_processo

    s, caminho = sessao
    registro = s.registrar_processos(LINHA_BETA, linhas_de_processo(BETA[0], 3))
    s.gravar()

    assert registro.linhas == 3
    assert coluna(caminho, 2, 4) == p.STATUS_CONCLUIDO


@pytest.mark.parametrize("metodo, anexar", [
    ("registrar_debitos", "anexar_debitos"),
    ("registrar_processos", "anexar_processos"),
])
def test_detalhe_que_cai_nao_marca_a_coluna(sessao, monkeypatch, metodo, anexar):
    """RESUMABILITY_CONTRACT: marcar antes de gravar o detalhe perderia os dados
    em silencio — a proxima execucao pularia o que nunca foi escrito."""
    s, caminho = sessao

    def cair(dados):
        raise RuntimeError("disco")

    monkeypatch.setattr(s, anexar, cair)

    with pytest.raises(RuntimeError):
        getattr(s, metodo)(ALFA[0], [])

    assert coluna(caminho, 1, 3) in (None, "")
    assert coluna(caminho, 1, 4) in (None, "")


# ── Retomada ──────────────────────────────────────────────────────────────────

def test_linha_intocada_nao_tem_nada_feito(sessao):
    s, caminho = sessao

    r = s.retomada(caminho, LINHA_ALFA, status_encerra_linha)

    assert (r.dctfweb_feito, r.processos_feitos, r.encerrada) == (False, False, False)
    assert r.concluida is False


def test_linha_com_as_duas_colunas_esta_concluida(tmp_path):
    caminho = str(criar_planilha(
        tmp_path / "pronta.xlsx", status={0: ("Concluído", "Sem Processos")}
    ))
    s = SessaoPlanilha()
    s.abrir(caminho)

    r = s.retomada(caminho, LINHA_ALFA, status_encerra_linha)

    assert r.concluida is True
    assert r.encerrada is False, "concluida por preenchimento, nao por status terminal"


def test_status_terminal_em_d_encerra_a_linha(tmp_path):
    caminho = str(criar_planilha(
        tmp_path / "recusada.xlsx", status={0: ("Procuração sem autorização", "")}
    ))
    s = SessaoPlanilha()
    s.abrir(caminho)

    r = s.retomada(caminho, LINHA_ALFA, status_encerra_linha)

    assert r.encerrada is True
    assert r.concluida is False, "a coluna E continua vazia"


def test_a_regra_de_status_terminal_entra_por_parametro(sessao):
    """Quais status terminam uma linha e regra do PORTAL. A planilha nao a
    conhece — pelo mesmo motivo de `linhas_pendentes`."""
    s, caminho = sessao
    s.registrar_debitos_concluidos(LINHA_ALFA)

    assert s.retomada(caminho, LINHA_ALFA, lambda v: True).encerrada is True
    assert s.retomada(caminho, LINHA_ALFA, lambda v: False).encerrada is False


def test_a_retomada_enxerga_o_que_foi_gravado_nesta_execucao(sessao):
    """O motivo de a retomada ser LIDA a cada consulta em vez de viajar no item.

    O DCTFWeb grava D antes de os Processos comecarem. Se os Processos caem e o
    CNPJ e retentado, a retentativa TEM de ver o D novo — senao refaz o DCTFWeb
    que ja tinha terminado.
    """
    s, caminho = sessao
    antes = s.retomada(caminho, LINHA_ALFA, status_encerra_linha)

    s.registrar_debitos_concluidos(LINHA_ALFA)
    depois = s.retomada(caminho, LINHA_ALFA, status_encerra_linha)

    assert antes.dctfweb_feito is False
    assert depois.dctfweb_feito is True, "sem gravar no disco, so em memoria"


def test_a_retomada_nao_carrega_os_textos_das_colunas(sessao):
    """Quem coordena decide o QUE fazer, nao le o texto que a planilha guardou."""
    import dataclasses

    s, caminho = sessao
    s.registrar_recusa_do_portal(LINHA_ALFA, "Procuração sem autorização")

    r = s.retomada(caminho, LINHA_ALFA, status_encerra_linha)

    assert "Procuração" not in str(dataclasses.asdict(r))
    assert all(isinstance(v, bool) for v in dataclasses.asdict(r).values())
