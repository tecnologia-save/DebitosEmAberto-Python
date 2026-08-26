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


class _Controle:
    """Token opaco do guardiao. O protocolo nunca o inspeciona."""

    def __init__(self, cn):
        self.cn = cn


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
        return _Controle(cn)

    def matar_o_processo_principal(self):
        """Todo guardiao vivo acorda e roda `limpar_autoselect` — cegamente."""
        for _ in self.guardioes:
            self.limpezas.append(self.cn)
            self.cn = ""


def pedir(m, cn):
    return garantir_policy(cn, ler_cn_atual=m.ler_cn, lancar_guardiao=m.lancar,
                           aguardar=lambda: None)


# ── A · o guardiao espera SO o PID ────────────────────────────────────────────

def test_a_o_guardiao_espera_o_pid_OU_o_pedido_de_limpeza():
    """ANTES: uma unica espera, sobre o PID. A policy so saia com a morte do
    processo.

    AGORA: duas, e a primeira que acontecer vence. O crash path continua sendo o
    mesmo objeto de sempre.
    """
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]

    assert "WaitForMultipleObjects(2, alvos, False, INFINITE)" in guarda
    assert "OpenProcess(SYNCHRONIZE" in guarda, "o PID continua vigiado"
    assert "OpenEventW(SYNCHRONIZE" in guarda, "e agora tambem o canal"
    assert "WaitForSingleObject(h, INFINITE)" in guarda, "sem canal, o de sempre"


def test_a_o_processo_principal_nem_guarda_o_handle_do_guardiao():
    """`_runas(..., wait_ms=None)` FECHA o handle e devolve 0. Depois do
    lancamento, o processo principal nao tem como esperar nem identificar o
    guardiao que acabou de criar."""
    fonte = inspect.getsource(__import__("cert_windows")._runas)

    assert "if wait_ms is None:" in fonte
    assert "CloseHandle(sei.hProcess)" in fonte
    assert "return 0" in fonte


def test_o_lancamento_devolve_o_controle_do_guardiao():
    """ANTES: `-> int`, e um inteiro nao permite PEDIR nada ao processo elevado.

    AGORA o controle atravessa, e `None` passa a significar UAC recusado.
    """
    fonte = inspect.getsource(garantir_policy)

    assert "controle = lancar_guardiao(cn)" in fonte
    assert "if controle is None:" in fonte
    assert "controle=controle" in fonte


def test_b_o_resultado_carrega_o_controle_e_nao_o_expoe():
    """A menor extensao possivel: um campo OPACO. Sem Win32 no tipo, sem CN.

    `repr=False` para que um handle do Windows nunca caia num log.
    """
    import dataclasses

    campos = {c.name for c in dataclasses.fields(ResultadoDaPolicy)}
    assert campos == {"situacao", "tem_guardiao", "controle"}

    controle = _Controle(CN_A)
    resultado = ResultadoDaPolicy(ATIVADA, tem_guardiao=True, controle=controle)
    assert resultado.controle is controle
    assert "Controle" not in repr(resultado)
    assert CN_A not in repr(resultado)


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
    """O crash path nao mudou: o guardiao continua vigiando o processo que o
    lancou. O que mudou e que agora ha outra forma de acordar."""
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")

    assert 'str(os.getpid())' in fonte
    # O PID entra no argv do guardiao e no NOME do canal — um por guardiao.
    assert fonte.count("os.getpid()") == 2
    assert "DebitosEmAberto-guardiao-" in fonte


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

def test_e_o_cleanup_normal_passou_a_ser_pedido_ao_elevado():
    """NORMAL_POLICY_CLEANUP_PRIVILEGE_GAP, fechado.

    ANTES: o processo comum chamava `limpar_autoselect` sozinho, e a falha em
    HKLM — escrita pelo elevado — sumia dentro da primitiva.

    AGORA: quem remove e o guardiao, que ja e elevado; este processo pede e
    CONFIRMA lendo o registro.
    """
    codigo = inspect.getsource(maquina.liberar_policy_do_windows)
    codigo = codigo[codigo.index("import cert_windows"):]

    assert "policy_certificado.liberar_policy(" in codigo
    assert "cert_windows.pedir_limpeza" in codigo
    assert "cert_windows.policy_existe" in codigo
    assert "limpar_autoselect" not in codigo, "o processo comum nao remove mais"


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


def test_e_a_troca_de_certificado_libera_a_policy_anterior():
    """ANTES: a policy anterior era apenas SOBRESCRITA e o guardiao dela ficava
    vivo — um por certificado, todos ate a morte do processo.

    AGORA ela e liberada primeiro. E a ORDEM e o que impede a janela perigosa:
    a sessao anterior ja foi encerrada, entao nenhum navegador esta usando a
    policy quando ela sai; e so depois a proxima entra.
    """
    fonte = inspect.getsource(app._Execucao.trocar_certificado)

    assert "self.encerrar_sessao()" in fonte
    assert "self.liberar_policy()" in fonte
    assert fonte.index("encerrar_sessao()") < fonte.index("liberar_policy()")
    assert fonte.index("liberar_policy()") < fonte.index("garantir_policy_do_windows")


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


# ── §28 · o lifecycle novo ────────────────────────────────────────────────────

from automation.policy_certificado import (  # noqa: E402
    SONDAGENS_LIBERACAO,
    liberar_policy,
)


class Guardiao:
    """Um guardião elevado, em memória. Ele é quem remove — inclusive de HKLM."""

    def __init__(self, registro, cn, remove=True):
        self.registro = registro
        self.cn = cn
        self.remove = remove
        self.pedidos = 0
        self.vivo = True

    def receber_pedido(self):
        """O que acontece do outro lado do canal."""
        self.pedidos += 1
        if not self.remove:
            return
        self.registro["cn"] = ""
        self.vivo = False       # ele confirma, e encerra


def liberar(guardiao, registro, sondagens_gastas=None):
    esperas = []
    resultado = liberar_policy(
        guardiao,
        pedir_limpeza=lambda g: g.receber_pedido(),
        policy_ainda_existe=lambda: bool(registro["cn"]),
        aguardar=lambda: esperas.append(1),
    )
    if sondagens_gastas is not None:
        sondagens_gastas.extend(esperas)
    return resultado


def test_1_o_cleanup_normal_acontece_com_o_processo_principal_VIVO():
    """O objetivo central da fatia. Nada aqui depende de o PID morrer."""
    registro = {"cn": CN_A}
    guardiao = Guardiao(registro, CN_A)

    assert liberar(guardiao, registro) is True
    assert guardiao.pedidos == 1
    assert registro["cn"] == ""


def test_2_quem_remove_e_o_guardiao_e_nao_o_processo_comum():
    """NORMAL_POLICY_CLEANUP_PRIVILEGE_GAP: HKLM foi escrita elevada.

    O protocolo nao remove nada — ele PEDE e depois LE. Quem apaga e o processo
    do outro lado do canal, e ele ja e elevado.
    """
    fonte = inspect.getsource(liberar_policy)

    assert "pedir_limpeza(controle)" in fonte
    assert "policy_ainda_existe()" in fonte
    assert "limpar" not in fonte.split('"""')[2], "o protocolo nao remove"


def test_4_confirmado_so_depois_de_a_policy_sumir():
    """PEDIR nao e CONFIRMAR. O guardiao recebe, falha, e nada e confirmado."""
    registro = {"cn": CN_A}
    guardiao = Guardiao(registro, CN_A, remove=False)
    gastas = []

    assert liberar(guardiao, registro, gastas) is False
    assert guardiao.pedidos == 1, "o pedido foi feito"
    assert registro["cn"] == CN_A, "e a policy continua la"
    assert len(gastas) == SONDAGENS_LIBERACAO, "a espera e finita"


def test_4_um_guardiao_surdo_nao_trava_a_execucao():
    """Espera limitada: se ninguem responde, seguimos — com a policy ainda
    nossa, e o fallback de crash de pe."""
    registro = {"cn": CN_A}

    class Surdo:
        def receber_pedido(self):
            pass

    assert liberar(Surdo(), registro) is False


def test_5_o_guardiao_encerra_depois_do_cleanup_confirmado():
    """Criterio obrigatorio: ele nao pode sobreviver para apagar a policy de uma
    execucao futura quando o PID morrer."""
    registro = {"cn": CN_A}
    guardiao = Guardiao(registro, CN_A)

    liberar(guardiao, registro)

    assert guardiao.vivo is False


def test_5_a_fonte_do_guardiao_sai_do_wait_e_cai_no_finally():
    """Do lado real: acordar pelo canal leva ao MESMO `finally` que limpa e
    encerra. Nao ha laco que o faca voltar a esperar."""
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]

    assert "WaitForMultipleObjects" in guarda
    assert guarda.index("WaitForMultipleObjects") < guarda.index("finally:")
    assert "while True" not in guarda


def test_6_o_crash_path_continua_intacto():
    """Sem pedido, o guardiao ainda acorda pela morte do PID e limpa."""
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]

    assert "OpenProcess(SYNCHRONIZE, False, int(pid))" in guarda
    assert "WaitForSingleObject(h, INFINITE)" in guarda, "sem canal, o de sempre"
    assert 'canal: str = ""' in guarda, "o canal e opcional"


def test_7_policy_emprestada_nao_e_liberada(monkeypatch):
    """JA_ATIVA continua BORROWED: sem controle, nao ha pedido."""
    from automation.policy_certificado import JA_ATIVA, ResultadoDaPolicy

    pedidos = []
    monkeypatch.setattr(maquina, "garantir_policy_do_windows",
                        lambda cn: ResultadoDaPolicy(JA_ATIVA, tem_guardiao=False))
    monkeypatch.setattr(maquina, "liberar_policy_do_windows",
                        lambda c: pedidos.append(c) or True)
    from automation.planilha import ItemPendente

    ex = app._Execucao(_PlanilhaInerte(), "p.xlsx", CONFIG, None)
    ex.certificados = {"c": {"subject_cn": CN_A, "serial": "0A01"}}
    ex.trocar_certificado(ItemPendente(0, "11111111000191", CN_A))
    ex.liberar_policy()

    assert pedidos == [], "nada foi pedido a ninguem"


def test_8_a_troca_de_certificado_nao_acumula_guardioes(monkeypatch):
    """MULTIPLE_POLICY_GUARDIANS_LIFETIME, fechado.

    A policy anterior e liberada ANTES de a proxima entrar, entao no maximo um
    guardiao proprietario existe por vez.
    """
    from automation.planilha import ItemPendente
    from automation.policy_certificado import ATIVADA as _ATIVADA
    from automation.policy_certificado import ResultadoDaPolicy

    vivos = []

    def garantir(cn):
        controle = _Controle(cn)
        vivos.append(controle)
        return ResultadoDaPolicy(_ATIVADA, tem_guardiao=True, controle=controle)

    def liberar_um(controle):
        vivos.remove(controle)
        return True

    monkeypatch.setattr(maquina, "garantir_policy_do_windows", garantir)
    monkeypatch.setattr(maquina, "liberar_policy_do_windows", liberar_um)

    ex = app._Execucao(_PlanilhaInerte(), "p.xlsx", CONFIG, None)
    ex.certificados = {"a": {"subject_cn": CN_A, "serial": "0A01"},
                       "b": {"subject_cn": CN_B, "serial": "0B02"}}

    ex.trocar_certificado(ItemPendente(0, "11111111000191", CN_A))
    assert len(vivos) == 1

    ex.trocar_certificado(ItemPendente(1, "22222222000172", CN_B))
    assert len(vivos) == 1, "o guardiao de A saiu antes de o de B entrar"

    ex.liberar_policy()
    assert vivos == []


def test_11_a_sessao_e_encerrada_ANTES_de_a_policy_sair():
    """§15: nao pode existir janela em que um navegador vivo perca sua policy."""
    fonte = inspect.getsource(app._Execucao.trocar_certificado)

    assert fonte.index("self.encerrar_sessao()") < fonte.index("self.liberar_policy()")
    assert fonte.index("self.liberar_policy()") < fonte.index("garantir_policy_do_windows")


def test_10_nenhum_cn_nem_caminho_de_registro_entra_no_evento():
    from automation import apresentacao_eventos, eventos

    evento = eventos.EventoOperacional(eventos.POLICY_NAO_REMOVIDA)
    frase = apresentacao_eventos.frase(evento)

    for proibido in (CN_A, CN_B, "Software", "HKCU", "HKLM", "AutoSelect"):
        assert proibido not in frase
    assert "guardião" in frase
