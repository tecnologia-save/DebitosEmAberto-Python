"""O limite de liberacao do host, COMO ELE E depois da 12B.

Escrito ANTES das correcoes desta fatia e commitado antes delas.

A 12B fez a policy propria ser removida no fim da execucao e eu declarei o
limite definido. Nao esta. Estes testes registram tres coisas que a 12B nao
provou, e duas delas sao defeitos do que eu mesmo escrevi la:

    1. `limpar_autoselect` ENGOLE o erro por colmeia. Uma falha de permissao em
       HKLM nunca chega ao app, e o evento POLICY_NAO_REMOVIDA que eu adicionei
       na 12B e inalcancavel pelo caminho real. CLEANUP_REPORTED, e nao
       CLEANUP_CONFIRMED.
    2. Um bug nosso no cleanup da policy SUBSTITUI a causa primaria — a protecao
       que a fatia 10 criou para o teardown de sessao nao foi aplicada aqui.
    3. Uma falha do emissor ao relatar esse cleanup tambem substitui a causa.

Nenhum teste toca registro, UAC, processo elevado ou navegador.

CNs ficticios.
"""
import inspect
import pathlib

import pytest

from automation import app, eventos, maquina
from automation.captcha import ConfigCaptcha

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ConfigCaptcha(api_key="AIzaSy-SENTINELA-FICTICIA-0000")
CN_A = "ALFA FICTICIA:11111111000191"
CN_B = "BETA FICTICIA:22222222000172"


class PlanilhaInerte:
    def __init__(self):
        self.estado = {}

    def precisa_gravar(self):
        return False

    def descartar(self):
        pass


def execucao(emissor=None, propria=True):
    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, emissor)
    ex.policy_propria = propria
    return ex


# ── §1 · §3 · o cleanup e RELATADO, nao CONFIRMADO ────────────────────────────

def test_a_primitiva_engole_a_falha_por_colmeia():
    """`limpar_autoselect` tenta HKCU e HKLM, guarda o que conseguiu, e devolve
    `None`. Um `PermissionError` numa das duas some ali dentro."""
    import cert_windows

    fonte = inspect.getsource(cert_windows.limpar_autoselect)

    assert "except OSError:" in fonte and "pass" in fonte
    assert inspect.signature(cert_windows.limpar_autoselect).return_annotation is None
    assert "return" not in fonte.replace("return_annotation", ""), "não devolve resultado"


def test_a_falha_em_hklm_nao_chega_ao_app(monkeypatch):
    """A consequencia: o app acredita que limpou.

    O guardiao escreve ELEVADO nas duas colmeias. O cleanup normal roda no
    processo comum, que nao tem privilegio sobre HKLM — e a falha nem sequer
    aparece.
    """
    import winreg

    import cert_windows

    def so_hkcu(raiz, path):
        if raiz == winreg.HKEY_LOCAL_MACHINE:
            raise PermissionError("acesso negado ao registro")

    monkeypatch.setattr(cert_windows.winreg, "DeleteKey", so_hkcu)
    monkeypatch.setattr(maquina, "liberar_policy_do_windows",
                        cert_windows.limpar_autoselect)

    emitidos = []
    execucao(emitidos.append).liberar_policy()

    assert emitidos == [], "nenhum evento — a falha em HKLM é invisível"


def test_o_evento_de_falha_e_inalcancavel_pelo_caminho_real():
    """POLICY_NAO_REMOVIDA existe, e o unico jeito de dispara-lo e a primitiva
    levantar — o que ela nao faz. Eu criei um alarme que nao toca."""
    fonte = inspect.getsource(app._Execucao.liberar_policy)

    assert "except OSError" in fonte
    assert "policy_existe" not in fonte, "não confere se a policy realmente saiu"
    assert eventos.POLICY_NAO_REMOVIDA in eventos.CODIGOS


def test_a_confirmacao_seria_possivel_com_o_que_ja_existe():
    """`policy_existe()` le as DUAS colmeias. A informacao para confirmar o
    cleanup ja esta na maquina; ela so nao e consultada."""
    import cert_windows

    fonte = inspect.getsource(cert_windows.policy_existe)

    assert "_COLMEIAS" in fonte and "any(" in fonte


def test_app_retorna_com_a_policy_possivelmente_viva(monkeypatch):
    """§1 A e B. A sequencia que impede o release do host:

        A adquire lock -> escreve policy -> cleanup falha em silencio
        -> app retorna -> lock libera -> policy de A continua instalada
    """
    monkeypatch.setattr(maquina, "liberar_policy_do_windows", lambda: None)

    ex = execucao()
    ex.liberar_policy()

    assert ex.policy_propria is False, "o app considera o assunto encerrado"


# ── §4 · bug nosso no cleanup substitui a causa ───────────────────────────────

def test_bug_no_cleanup_da_policy_apaga_a_causa_primaria(monkeypatch):
    """CLEANUP_PRIMARY_ERROR_MASKING, nova manifestacao.

    O `except OSError` da 12B nao cobre um bug nosso. A protecao que a fatia 10
    criou para o teardown de sessao nao foi aplicada a este caminho.
    """
    monkeypatch.setattr(maquina, "liberar_policy_do_windows",
                        lambda: (_ for _ in ()).throw(TypeError("bug de cleanup")))

    with pytest.raises(TypeError, match="bug de cleanup"):
        try:
            raise RuntimeError("ERRO PRIMARIO: o portal caiu")
        finally:
            execucao().liberar_policy()


def test_falha_do_emissor_no_cleanup_da_policy_tambem_apaga_a_causa(monkeypatch):
    """§5. Mesma lacuna, um nivel mais fundo."""
    monkeypatch.setattr(
        maquina, "liberar_policy_do_windows",
        lambda: (_ for _ in ()).throw(PermissionError("acesso negado")),
    )

    def emissor_quebrado(evento):
        raise AttributeError("bug no adapter de apresentação")

    with pytest.raises(AttributeError, match="bug no adapter"):
        try:
            raise RuntimeError("ERRO PRIMARIO: o portal caiu")
        finally:
            execucao(emissor_quebrado).liberar_policy()


def test_a_protecao_ja_existe_para_a_sessao_e_nao_para_a_policy():
    """O contraste, na fonte: um caminho tem a protecao, o outro nao."""
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")

    assert "encerrar_sessao_sem_apagar_a_causa" in fonte
    assert "liberar_policy_sem_apagar_a_causa" not in fonte


# ── §2 · privilegio ───────────────────────────────────────────────────────────

def test_a_escrita_e_elevada_e_a_limpeza_normal_nao_e():
    """NORMAL_POLICY_CLEANUP_PRIVILEGE_GAP.

    O projeto ja assume que o processo comum nao escreve em HKLM — e por isso o
    guardiao existe e e relancado com `_runas`. A remocao normal roda no processo
    comum, e sobre a mesma colmeia.
    """
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")

    assert "_runas(_guard_args([" in fonte, "a escrita passa por elevação"
    assert "um erro de permissão na" in fonte, "o projeto já documenta a assimetria"

    # E o caminho normal nao eleva nada:
    fiacao = inspect.getsource(maquina.liberar_policy_do_windows)
    assert "runas" not in fiacao.lower() and "admin" not in fiacao.lower()


# ── §6 · §7 · §8 · §9 · ownership do estado preexistente ──────────────────────

def test_ja_ativa_e_estado_EMPRESTADO_e_nao_proprio(monkeypatch):
    """H · JA_ATIVA e BORROWED. A execucao nao a criou e nao a remove."""
    from automation.planilha import ItemPendente
    from automation.policy_certificado import JA_ATIVA, ResultadoDaPolicy

    monkeypatch.setattr(maquina, "garantir_policy_do_windows",
                        lambda cn: ResultadoDaPolicy(JA_ATIVA, tem_guardiao=False))
    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, None)
    ex.certificados = {"c": {"subject_cn": CN_A, "serial": "0A01"}}

    ex.trocar_certificado(ItemPendente(0, "11111111000191", CN_A))

    assert ex.policy_propria is False


def test_o_lock_nao_provaria_quem_criou_a_policy_preexistente():
    """PREEXISTING_POLICY_OWNERSHIP_AMBIGUITY — corrige uma conclusao da 12B.

    Eu escrevi que, com o lock, JA_ATIVA passaria a ser provadamente stale DESTA
    automacao. Nao passa. O lock prova apenas que nenhuma outra execucao
    PROTEGIDA esta ativa; ele nao diz nada sobre quem escreveu um estado que ja
    existia antes da aquisicao — pode ser configuracao manual, um administrador,
    outra automacao ou software externo.

    O registro nao carrega dono, e nao ha o que consultar.
    """
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    escrita = fonte[fonte.index("def definir_autoselect"):fonte.index("def limpar_autoselect")]

    assert "pattern" in escrita and "filter" in escrita
    for marcador in ("pid", "getpid", "uuid", "owner", "DebitosEmAberto", "timestamp"):
        assert marcador not in escrita, f"nenhum {marcador} no payload"


def test_policy_preexistente_com_outro_cn_e_destruida(monkeypatch):
    """PREEXISTING_POLICY_DESTRUCTIVE_REPLACEMENT.

    Se a policy que estava la pertencia a uma configuracao externa legitima, ela
    e sobrescrita — e o cleanup final da NOSSA execucao remove a chave inteira.
    O que existia antes nao volta. Isso e independente de concorrencia: um lock
    de host nao resolve ownership de estado externo.
    """
    from automation.policy_certificado import ATIVADA, ResultadoDaPolicy, garantir_policy

    maquina_falsa = {"cn": CN_A}

    def lancar(cn):
        maquina_falsa["cn"] = cn      # o guardiao sobrescreve os valores
        return 0

    resultado = garantir_policy(
        CN_B, ler_cn_atual=lambda: maquina_falsa["cn"],
        lancar_guardiao=lancar, aguardar=lambda: None,
    )

    assert resultado == ResultadoDaPolicy(ATIVADA, tem_guardiao=True)
    assert maquina_falsa["cn"] == CN_B, "o CN externo foi substituído"


# ── §11 · §12 · os caminhos de falha do login ─────────────────────────────────

def _saidas_de_falha():
    """Cada `return None` de `fazer_login` depois de o contexto existir."""
    fonte = (RAIZ / "servicos_rf_login" / "login.py").read_text(encoding="utf-8")
    linhas = fonte.split("\n")
    ctx = next(i for i, linha in enumerate(linhas)
               if "context = p.chromium.launch_persistent_context" in linha)
    return [
        (i + 1, "\n".join(linhas[max(0, i - 8):i]))
        for i, linha in enumerate(linhas)
        if linha.strip() == "return None" and i > ctx
    ]


def test_todas_as_saidas_de_falha_param_o_playwright():
    saidas = _saidas_de_falha()

    assert len(saidas) == 7, "sete caminhos de falha depois do contexto existir"
    assert all("p.stop()" in janela for _, janela in saidas)


def test_nenhuma_saida_de_falha_fecha_o_contexto():
    """LOGIN_RESOURCE_CLEANUP_GAP, com os numeros.

    Se `p.stop()` libera o perfil e a porta 9222 e comportamento do
    Playwright/Chrome, e nao do nosso codigo. Para um lock de host, "talvez" nao
    basta.
    """
    saidas = _saidas_de_falha()

    assert all("context.close()" not in janela for _, janela in saidas)


def test_o_caminho_de_sucesso_fecha_os_dois():
    """O contraste: quando o login DEVOLVE a sessao, quem a fecha e o app — e ai
    o contexto e fechado antes de o Playwright parar."""
    from automation import login

    fonte = inspect.getsource(login.SessaoReceita.encerrar)

    assert fonte.index('(self.contexto, "close")') < fonte.index('(self.playwright, "stop")')


# ── §20 · o guardiao nao tem canal ────────────────────────────────────────────

def test_o_guardiao_nao_aceita_ordem_de_limpar_antes_da_morte_do_pid():
    """R · SIM/NAO respondido pela fonte: nao ha canal nenhum.

    Ele espera `WaitForSingleObject(h, INFINITE)` sobre o PID e nada mais. Nao ha
    arquivo-sinal, pipe, evento nomeado ou socket para pedir limpeza antecipada.
    """
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]

    assert "WaitForSingleObject(h, INFINITE)" in guarda
    # "sinal" sai da lista: aparece num comentario em prosa sobre elevacao.
    for canal in ("Pipe", "socket", "CreateEvent", "OpenEvent", "mmap", "NamedPipe"):
        assert canal not in guarda
