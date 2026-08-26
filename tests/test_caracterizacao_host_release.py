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


class _Controle:
    """Token opaco do guardiao."""

    def __init__(self, cn):
        self.cn = cn


class PlanilhaInerte:
    def __init__(self):
        self.estado = {}

    def precisa_gravar(self):
        return False

    def descartar(self):
        pass


def execucao(emissor=None, propria=True):
    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, emissor)
    ex.controle_da_policy = _Controle(CN_A) if propria else None
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
    monkeypatch.setattr(cert_windows, "policy_existe", lambda: True)
    monkeypatch.setattr(cert_windows, "pedir_limpeza", lambda c: None)
    monkeypatch.setattr(maquina.policy_certificado, "INTERVALO_LIBERACAO_S", 0)

    emitidos = []
    ex = execucao(emitidos.append)
    controle = ex.controle_da_policy
    ex.liberar_policy()

    assert [e.codigo for e in emitidos] == [eventos.POLICY_NAO_REMOVIDA]
    assert ex.controle_da_policy is controle, (
        "continua NOSSA: quem for liberar o host precisa saber que ficou estado"
    )


def test_o_evento_de_falha_passou_a_ser_alcancavel():
    """ANTES: o unico jeito de disparar POLICY_NAO_REMOVIDA era a primitiva
    levantar — o que ela nunca faz. Eu tinha criado um alarme que nao tocava.

    AGORA: a decisao vem da CONFIRMACAO, e nao de uma exception.
    """
    fonte = inspect.getsource(app._Execucao.liberar_policy)

    assert "except OSError" not in fonte
    assert "if maquina.liberar_policy_do_windows(self.controle_da_policy):" in fonte
    assert "POLICY_NAO_REMOVIDA" in fonte


def test_a_confirmacao_seria_possivel_com_o_que_ja_existe():
    """`policy_existe()` le as DUAS colmeias. A informacao para confirmar o
    cleanup ja esta na maquina; ela so nao e consultada."""
    import cert_windows

    fonte = inspect.getsource(cert_windows.policy_existe)

    assert "_COLMEIAS" in fonte and "any(" in fonte


def test_o_app_deixa_de_fingir_que_limpou(monkeypatch):
    """ANTES: `liberar_policy` zerava `policy_propria` antes de tentar, e o app
    considerava o assunto encerrado mesmo que a policy continuasse instalada.

    AGORA o estado reflete a maquina, e e ele que a 12C vai consultar.
    """
    monkeypatch.setattr(maquina, "liberar_policy_do_windows", lambda c: False)
    ex = execucao(lambda e: None)
    ex.liberar_policy()
    assert ex.controle_da_policy is not None, "não saiu; continua sendo nossa"

    monkeypatch.setattr(maquina, "liberar_policy_do_windows", lambda c: True)
    ex = execucao()
    ex.liberar_policy()
    assert ex.controle_da_policy is None, "saiu, confirmado"


# ── §4 · bug nosso no cleanup substitui a causa ───────────────────────────────

def test_bug_no_cleanup_da_policy_nao_apaga_mais_a_causa_primaria(monkeypatch):
    """CLEANUP_PRIMARY_ERROR_MASKING, fechado na 12B.1 e preservado aqui."""
    monkeypatch.setattr(maquina, "liberar_policy_do_windows",
                        lambda c: (_ for _ in ()).throw(TypeError("bug de cleanup")))

    with pytest.raises(RuntimeError, match="ERRO PRIMARIO"):
        try:
            raise RuntimeError("ERRO PRIMARIO: o portal caiu")
        finally:
            execucao().liberar_policy_sem_apagar_a_causa()


def test_falha_do_emissor_no_cleanup_da_policy_nao_apaga_mais_a_causa(monkeypatch):
    """§5. Mesma lacuna da anterior, um nivel mais fundo — e fechada junto.

    Tres falhas empilhadas: a primaria, o bug no cleanup da policy, e um bug no
    proprio adapter ao relatar o segundo.
    """
    monkeypatch.setattr(
        maquina, "liberar_policy_do_windows",
        lambda: (_ for _ in ()).throw(PermissionError("acesso negado")),
    )

    def emissor_quebrado(evento):
        raise AttributeError("bug no adapter de apresentação")

    with pytest.raises(RuntimeError, match="ERRO PRIMARIO"):
        try:
            raise RuntimeError("ERRO PRIMARIO: o portal caiu")
        finally:
            execucao(emissor_quebrado).liberar_policy_sem_apagar_a_causa()


def test_os_dois_caminhos_de_cleanup_tem_a_mesma_protecao():
    """ANTES: a sessao tinha a protecao da fatia 10, a policy nao. AGORA os dois
    caminhos usam o mesmo principio, e nos MESMOS dois ramos de `executar`."""
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")

    assert "encerrar_sessao_sem_apagar_a_causa()" in fonte
    assert "liberar_policy_sem_apagar_a_causa()" in fonte

    inicio = fonte.index("    except BaseException:")
    ramo = fonte[inicio:fonte.index("        raise", inicio)]
    assert "encerrar_sessao_sem_apagar_a_causa()" in ramo
    assert "liberar_policy_sem_apagar_a_causa()" in ramo


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

    assert ex.controle_da_policy is None, "emprestada: nao ha guardiao nosso"


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
    from automation.policy_certificado import ATIVADA, garantir_policy

    maquina_falsa = {"cn": CN_A}

    def lancar(cn):
        maquina_falsa["cn"] = cn      # o guardiao sobrescreve os valores
        return _Controle(cn)

    resultado = garantir_policy(
        CN_B, ler_cn_atual=lambda: maquina_falsa["cn"],
        lancar_guardiao=lancar, aguardar=lambda: None,
    )

    assert resultado.situacao == ATIVADA and resultado.tem_guardiao is True
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


def test_todas_as_saidas_de_falha_fecham_o_contexto():
    """LOGIN_RESOURCE_CLEANUP_GAP, fechado.

    ANTES: as sete saidas chamavam `p.stop()` e NENHUMA chamava
    `context.close()`. Se `p.stop()` sozinho libera o perfil e a porta 9222 e
    comportamento do Playwright/Chrome, e nao do nosso codigo — e para um lock de
    host "talvez" nao basta.

    AGORA o contexto e fechado explicitamente. Autorizado pelo §13: o contexto ja
    existe, o caminho vai retornar falha, e o chamador nunca recebera ownership
    dele.
    """
    saidas = _saidas_de_falha()

    assert all("context.close()" in janela for _, janela in saidas)


def test_o_fechamento_e_o_stop_tem_guardas_separadas():
    """§14: uma falha ao fechar o contexto nao pode impedir a parada do
    Playwright — senao a correcao trocaria um vazamento por outro."""
    fonte = (RAIZ / "servicos_rf_login" / "login.py").read_text(encoding="utf-8")

    assert fonte.count("context.close()") == 7
    for janela in (j for _, j in _saidas_de_falha()):
        fecha = janela.index("context.close()")
        para = janela.index("p.stop()")
        assert fecha < para, "contexto primeiro, como no caminho de sucesso"
        assert "except Exception:" in janela[fecha:para], "cada um com sua guarda"


def test_o_fluxo_de_sucesso_do_fork_nao_foi_tocado():
    """A correcao entra SO nos caminhos que retornam falha."""
    fonte = (RAIZ / "servicos_rf_login" / "login.py").read_text(encoding="utf-8")
    final = fonte[fonte.index("return p, context, page"):]

    assert "context.close()" not in final
    assert fonte.count("return p, context, page") == 1


def test_o_caminho_de_sucesso_fecha_os_dois():
    """O contraste: quando o login DEVOLVE a sessao, quem a fecha e o app — e ai
    o contexto e fechado antes de o Playwright parar."""
    from automation import login

    fonte = inspect.getsource(login.SessaoReceita.encerrar)

    assert fonte.index('(self.contexto, "close")') < fonte.index('(self.playwright, "stop")')


# ── §20 · o guardiao nao tem canal ────────────────────────────────────────────

def test_o_guardiao_passou_a_aceitar_ordem_de_limpar():
    """R · ANTES: nao havia canal nenhum — so `WaitForSingleObject` sobre o PID.

    AGORA ha um evento nomeado do Windows, e a escolha e deliberada: o dono do
    objeto e o SO. Ele some com os processos que o abriram, nao deixa arquivo
    para tras se a maquina cair, e nao precisa de polling de disco.
    """
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]

    assert "OpenEventW(SYNCHRONIZE, False, canal)" in guarda
    assert "WaitForMultipleObjects" in guarda
