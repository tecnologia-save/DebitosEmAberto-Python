"""O guardiao COMO ELE E hoje: um por policy, todos esperando o mesmo PID.

Escrito ANTES da mudanca da 12B.2 e commitado antes dela.

A pergunta desta fatia: o processo principal consegue pedir ao guardiao ELEVADO
que remova a policy, e saber que ela saiu, SEM morrer? Hoje nao — e estes testes
registram por que, alem de duas consequencias que a 12B nao tinha visto.

Nenhum teste eleva processo, toca registro ou abre navegador. As primitivas do
Windows entram por dublê, como o protocolo da fatia 7A previu.

CNs ficticios.
"""
import inspect
import pathlib

from automation import app, maquina
from automation.captcha import ConfigCaptcha
from automation.policy_certificado import ATIVADA, ResultadoDaPolicy, garantir_policy

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ConfigCaptcha(api_key="AIzaSy-SENTINELA-FICTICIA-0000")
CN_A = "ALFA FICTICIA:11111111000191"
CN_B = "BETA FICTICIA:22222222000172"


class Maquina:
    """O registro e os guardioes, em memoria."""

    def __init__(self, cn=""):
        self.cn = cn
        self.guardioes = []          # cada um: o CN que escreveu
        self.limpezas = []

    def ler_cn(self):
        return self.cn

    def lancar(self, cn):
        self.guardioes.append(cn)
        self.cn = cn
        return 0

    def matar_o_processo_principal(self):
        """Todo guardiao vivo acorda e roda `limpar_autoselect` — cegamente."""
        for _ in self.guardioes:
            self.limpezas.append(self.cn)
            self.cn = ""


def pedir(m, cn):
    return garantir_policy(cn, ler_cn_atual=m.ler_cn, lancar_guardiao=m.lancar,
                           aguardar=lambda: None)


# ── A · o guardiao espera SO o PID ────────────────────────────────────────────

def test_a_o_guardiao_espera_apenas_o_pid_principal():
    """Nao ha canal de pedido: uma unica espera, sobre um unico objeto."""
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]

    assert "WaitForSingleObject(h, INFINITE)" in guarda
    assert "WaitForMultipleObjects" not in guarda
    assert "OpenProcess(SYNCHRONIZE" in guarda


def test_a_o_processo_principal_nem_guarda_o_handle_do_guardiao():
    """`_runas(..., wait_ms=None)` FECHA o handle e devolve 0. Depois do
    lancamento, o processo principal nao tem como esperar nem identificar o
    guardiao que acabou de criar."""
    fonte = inspect.getsource(__import__("cert_windows")._runas)

    assert "if wait_ms is None:" in fonte
    assert "CloseHandle(sei.hProcess)" in fonte
    assert "return 0" in fonte


def test_a_o_lancamento_devolve_apenas_um_inteiro():
    """`lancar_guardiao(cn) -> int`. 0 = o Windows aceitou; nada mais atravessa."""
    fonte = inspect.getsource(garantir_policy)

    assert "if lancar_guardiao(cn) != 0:" in fonte
    assert "controle" not in fonte


def test_b_o_resultado_nao_carrega_nada_que_comande_o_guardiao():
    import dataclasses

    campos = {c.name for c in dataclasses.fields(ResultadoDaPolicy)}

    assert campos == {"situacao", "tem_guardiao"}


# ── C · D · varios guardioes, todos vivos ─────────────────────────────────────

def test_c_uma_troca_de_certificado_cria_um_SEGUNDO_guardiao():
    """MULTIPLE_POLICY_GUARDIANS_LIFETIME.

    A policy de A ainda esta escrita quando B chega; os CNs diferem, entao um
    guardiao novo e lancado. O de A nao e avisado de nada.
    """
    m = Maquina()

    assert pedir(m, CN_A).situacao == ATIVADA
    assert pedir(m, CN_B).situacao == ATIVADA

    assert m.guardioes == [CN_A, CN_B], "dois guardioes, um por policy"


def test_c_o_numero_de_guardioes_acompanha_o_numero_de_certificados():
    """Nao ha teto. Uma planilha com N certificados distintos produz N guardioes
    elevados vivos ao mesmo tempo — e N janelas de UAC."""
    m = Maquina()
    for n in range(4):
        pedir(m, f"CERT {n} FICTICIO:0000000000000{n}")

    assert len(m.guardioes) == 4


def test_c_o_mesmo_certificado_duas_vezes_nao_cria_guardiao_novo():
    """O contrapeso: se o CN ja e o pedido, e JA_ATIVA e ninguem sobe."""
    m = Maquina()
    pedir(m, CN_A)
    pedir(m, CN_A)

    assert m.guardioes == [CN_A]


def test_d_todos_esperam_o_MESMO_pid_e_todos_limpam():
    """Na morte do processo principal, cada guardiao vivo roda a limpeza cega."""
    m = Maquina()
    pedir(m, CN_A)
    pedir(m, CN_B)

    m.matar_o_processo_principal()

    assert len(m.limpezas) == 2, "dois guardioes, duas limpezas"


def test_d_o_pid_vigiado_e_sempre_o_do_processo_principal():
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")

    assert 'str(os.getpid())' in fonte
    assert fonte.count("os.getpid()") == 1, "um so lugar decide quem e vigiado"


def test_d_o_guardiao_antigo_sobrevive_e_limparia_policy_alheia():
    """STALE_GUARDIAN_DESTRUCTIVE_CLEANUP_RISK.

    O guardiao de A continua esperando o PID. Muito depois — outra execucao no
    mesmo processo, outra policy — o PID morre, e ele acorda e apaga o que
    encontrar. Ele nao sabe qual CN esta escrito; `limpar_autoselect` nao le
    nada antes de remover.

    Mecanismo possivel. Nao afirmo ocorrencia.
    """
    m = Maquina()
    pedir(m, CN_A)          # guardiao A
    pedir(m, CN_B)          # guardiao B; A continua vivo

    m.matar_o_processo_principal()

    assert m.limpezas[0] == CN_B, "o primeiro a acordar apaga a policy de B"

    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    limpeza = fonte[fonte.index("def limpar_autoselect"):fonte.index("def _ler_cn")]
    assert "_ler_cn" not in limpeza, "não confere o CN antes de apagar"


# ── E · o cleanup normal de hoje ──────────────────────────────────────────────

def test_e_o_cleanup_normal_roda_no_processo_comum():
    """NORMAL_POLICY_CLEANUP_PRIVILEGE_GAP: quem escreveu HKLM foi o elevado."""
    fonte = inspect.getsource(maquina.liberar_policy_do_windows)

    # A prosa fala do guardiao; o CODIGO nao o aciona — e essa a diferenca.
    codigo = fonte[fonte.index('import cert_windows'):]
    assert "cert_windows.limpar_autoselect()" in codigo
    assert "runas" not in codigo.lower()
    assert "guardiao" not in codigo.lower()


def test_e_o_app_ja_conserva_o_estado_quando_a_remocao_nao_confirma(monkeypatch):
    """O que a 12B.1 deixou pronto: `policy_propria` reflete a maquina."""
    monkeypatch.setattr(maquina, "liberar_policy_do_windows", lambda: False)

    ex = app._Execucao(_PlanilhaInerte(), "p.xlsx", CONFIG, lambda e: None)
    ex.policy_propria = True
    ex.liberar_policy()

    assert ex.policy_propria is True


class _PlanilhaInerte:
    def __init__(self):
        self.estado = {}

    def precisa_gravar(self):
        return False

    def descartar(self):
        pass


def test_e_a_troca_de_certificado_nao_libera_a_policy_anterior(monkeypatch):
    """Hoje a policy anterior e apenas SOBRESCRITA, e o guardiao dela fica.

    A sessao anterior ja foi encerrada — `trocar_certificado` comeca por
    `encerrar_sessao` — entao nao ha navegador usando a policy velha. A janela
    para libera-la existe; ela so nao e usada.
    """
    fonte = inspect.getsource(app._Execucao.trocar_certificado)

    assert "self.encerrar_sessao()" in fonte
    assert "liberar_policy" not in fonte
    assert fonte.index("encerrar_sessao()") < fonte.index("garantir_policy_do_windows")


# ── F · o crash path, que precisa ser preservado ──────────────────────────────

def test_f_o_crash_path_limpa_e_e_a_razao_de_o_guardiao_existir():
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]

    assert "finally:" in guarda
    assert "for _ in range(10):" in guarda
    assert "limpar_autoselect()" in guarda
    assert "if not policy_existe():" in guarda, "ele ja confirma o que removeu"


def test_f_a_confirmacao_ja_existe_do_lado_do_guardiao():
    """O guardiao SEMPRE confirmou a remocao — `removeu` sai no log dele. O que
    falta e esse resultado chegar ao processo principal."""
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]

    assert "removeu = False" in guarda and "removeu = True" in guarda
    assert "return" not in guarda, "e ele nao devolve nada a ninguem"
