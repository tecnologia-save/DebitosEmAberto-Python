"""Uma execucao por host, imposta — e a corrida de crash que ela precisa fechar.

O lease e a EXISTENCIA de um objeto nomeado do Windows, e nao um mutex possuido
pelo processo principal. A diferenca aparece no unico cenario que importa:

    processo principal morre -> o handle dele fecha
    guardiao ainda tem handle -> o objeto CONTINUA existindo
    guardiao limpa a policy   -> so entao fecha o dele
    o objeto desaparece       -> e so agora outra execucao entra

Um mutex do parent seria liberado pelo SO no instante da morte, e uma segunda
execucao entraria no vao antes de a policy da primeira sair.

Nenhum teste cria objeto do Windows de verdade. O Win32 entra por dublê — e o
que se prova aqui e o PROTOCOLO. A validacao em maquina real esta pendente e
registrada como HOST_LEASE_WINDOWS_SECURITY_VALIDATION_REQUIRED.
"""
import ast
import pathlib

import pytest

from automation import exclusividade_host
from automation.exclusividade_host import (
    ERROR_ALREADY_EXISTS,
    ControleDaExclusividade,
    ExecucaoJaAtivaNoHost,
    FalhaAoVerificarExclusividade,
    adquirir,
    liberar,
)

RAIZ = pathlib.Path(__file__).resolve().parents[1]


class Host:
    """O namespace de objetos nomeados do Windows, em memoria.

    Um objeto existe enquanto tiver ao menos um handle aberto — que e
    exatamente a propriedade em que o lease se apoia.
    """

    def __init__(self):
        self.handles: dict[str, int] = {}
        self.proximo = 100

    def criar(self, nome):
        """CreateEventW: devolve handle valido SEMPRE, mais o codigo de erro."""
        self.proximo += 1
        ja_existia = nome in self.handles
        self.handles[nome] = self.handles.get(nome, 0) + 1
        return self.proximo, ERROR_ALREADY_EXISTS if ja_existia else 0

    def abrir(self, nome):
        """OpenEventW: 0 quando o objeto nao existe."""
        if nome not in self.handles:
            return 0
        self.proximo += 1
        self.handles[nome] += 1
        return self.proximo

    def fechar(self, handle):
        for nome, quantos in list(self.handles.items()):
            if quantos > 0:
                self.handles[nome] = quantos - 1
                if self.handles[nome] == 0:
                    del self.handles[nome]
                return

    def ocupado(self):
        return exclusividade_host.NOME_DO_LEASE in self.handles


def tomar(host):
    return adquirir(criar=host.criar, fechar=host.fechar)


# ── A · B · a aquisicao ───────────────────────────────────────────────────────

def test_a_a_primeira_execucao_toma_o_host():
    host = Host()

    controle = tomar(host)

    assert isinstance(controle, ControleDaExclusividade)
    assert host.ocupado()


def test_b_a_segunda_execucao_e_recusada():
    host = Host()
    tomar(host)

    with pytest.raises(ExecucaoJaAtivaNoHost):
        tomar(host)


def test_b_a_recusa_nao_prolonga_o_lease_alheio():
    """`CreateEventW` devolve um handle valido mesmo quando o objeto ja existe.
    Segurar esse handle manteria vivo o lease de OUTRA execucao."""
    host = Host()
    tomar(host)
    antes = host.handles[exclusividade_host.NOME_DO_LEASE]

    with pytest.raises(ExecucaoJaAtivaNoHost):
        tomar(host)

    assert host.handles[exclusividade_host.NOME_DO_LEASE] == antes


def test_a_aquisicao_nao_tem_janela_entre_verificar_e_tomar():
    """Quem decide e o Windows: um unico `CreateEventW` cria OU acusa que ja
    existia. Nao ha leitura separada que outra execucao possa atravessar."""
    fonte = inspect_fonte(exclusividade_host.adquirir)

    assert fonte.count("criar(") == 1, "uma chamada, e ela decide"
    assert "abrir" not in fonte, "nao ha consulta previa"
    # A comparacao e contra o codigo de erro do proprio Windows, e nao contra
    # uma leitura que outra execucao poderia atravessar.
    assert "erro == ERROR_ALREADY_EXISTS" in fonte


def inspect_fonte(funcao):
    import inspect

    return inspect.getsource(funcao)


def test_a_mensagem_de_recusa_nao_carrega_identificacao():
    host = Host()
    tomar(host)

    with pytest.raises(ExecucaoJaAtivaNoHost) as erro:
        tomar(host)

    texto = str(erro.value)
    for proibido in ("Global", "DebitosEmAberto-host", "pid", "11111111000191"):
        assert proibido not in texto


# ── Fail-closed na aquisicao ──────────────────────────────────────────────────

def test_erro_do_windows_impede_a_execucao():
    """§29: nao saber se o host esta livre nao e o mesmo que estar livre."""
    def negado(nome):
        raise PermissionError("acesso negado ao objeto")

    with pytest.raises(FalhaAoVerificarExclusividade) as erro:
        adquirir(criar=negado, fechar=lambda h: None)

    assert "acesso negado" not in str(erro.value), "sem texto bruto do Win32"


def test_handle_invalido_impede_a_execucao():
    with pytest.raises(FalhaAoVerificarExclusividade):
        adquirir(criar=lambda nome: (0, 0), fechar=lambda h: None)


# ── C · o release normal ──────────────────────────────────────────────────────

def test_c_sem_guardiao_o_release_libera_o_host():
    host = Host()
    controle = tomar(host)

    liberar(controle, fechar=host.fechar)

    assert not host.ocupado()
    tomar(host)   # a proxima entra


def test_c_liberar_none_nao_quebra():
    liberar(None, fechar=lambda h: None)


# ── D · E · F · a corrida de crash ────────────────────────────────────────────

def test_d_parent_e_guardiao_seguram_o_MESMO_lease():
    host = Host()
    tomar(host)

    assert exclusividade_host.anexar(abrir=host.abrir) is not None
    assert host.handles[exclusividade_host.NOME_DO_LEASE] == 2


def test_e_o_host_continua_ocupado_quando_o_parent_morre():
    """A prova central. HOST_LOCK_CRASH_HANDOFF_RACE.

    Um mutex do parent seria liberado pelo SO agora. O objeto nao e: o guardiao
    ainda tem um handle.
    """
    host = Host()
    parent = tomar(host)
    exclusividade_host.anexar(abrir=host.abrir)      # o guardiao anexa

    liberar(parent, fechar=host.fechar)              # o parent morre

    assert host.ocupado(), "o host continua nosso"
    with pytest.raises(ExecucaoJaAtivaNoHost):
        tomar(host)


def test_f_so_depois_do_cleanup_o_guardiao_devolve_o_host():
    host = Host()
    parent = tomar(host)
    guardiao = exclusividade_host.anexar(abrir=host.abrir)

    liberar(parent, fechar=host.fechar)
    assert host.ocupado()

    # o guardiao terminou a limpeza e so entao fecha o dele
    host.fechar(guardiao)

    assert not host.ocupado()
    tomar(host)   # a proxima execucao entra, e a policy ja saiu


def test_a_ordem_inversa_seria_a_corrida_que_o_lease_existe_para_fechar():
    """O contraste explicito: se o guardiao fechasse ANTES de limpar, haveria um
    intervalo em que o host esta livre e a policy da execucao anterior continua
    escrita. E esse intervalo que o lease elimina."""
    host = Host()
    parent = tomar(host)
    guardiao = exclusividade_host.anexar(abrir=host.abrir)

    host.fechar(guardiao)          # ordem ERRADA, de proposito
    liberar(parent, fechar=host.fechar)

    assert not host.ocupado(), "aqui a segunda execucao entraria cedo demais"


# ── G · o guardiao atrasado ───────────────────────────────────────────────────

def test_g_guardiao_atrasado_com_parent_morto_nao_encontra_lease():
    """O pai morreu antes de o guardiao anexar: o objeto foi destruido, e
    `anexar` devolve None. O guardiao aborta sem escrever policy nenhuma."""
    host = Host()
    parent = tomar(host)
    liberar(parent, fechar=host.fechar)              # o pai morre primeiro

    assert exclusividade_host.anexar(abrir=host.abrir) is None


def test_g_o_guardiao_aborta_sem_escrever_quando_nao_ha_lease():
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]

    trecho = guarda[:guarda.index("definir_autoselect(cn)")]
    assert "if lease is None:" in trecho
    assert "return" in trecho


def test_g_o_nome_ser_o_mesmo_nao_basta_e_por_isso_o_pai_e_conferido():
    """§14. Um guardiao atrasado pode abrir, PELO NOME, o lease de uma execucao
    NOVA — o objeto tem o mesmo nome.

    A protecao nao e o lease: e conferir que o pai DELE continua vivo antes de
    mutar. Se morreu, aborta.
    """
    host = Host()
    parent_a = tomar(host)
    liberar(parent_a, fechar=host.fechar)            # A morre
    tomar(host)                                      # B entra, mesmo nome

    # O guardiao atrasado de A consegue abrir — e o objeto de B.
    assert exclusividade_host.anexar(abrir=host.abrir) is not None

    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]
    trecho = guarda[:guarda.index("definir_autoselect(cn)")]
    assert "OpenProcess(SYNCHRONIZE, False, int(pid))" in trecho
    assert "pai ja morreu antes da escrita" in trecho


# ── §15 · o handshake ─────────────────────────────────────────────────────────

def test_a_policy_visivel_ja_prova_que_o_guardiao_anexou():
    """§15: nao foi preciso inventar handshake.

    A ordem no guardiao e anexar -> conferir -> escrever, e o processo principal
    so considera ATIVADA quando OBSERVA a policy com o CN pedido. Se a policy
    apareceu, o attach ja aconteceu — a propria ordem prova o invariante.
    """
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]
    assert guarda.index("anexar()") < guarda.index("definir_autoselect(cn)")

    from automation.policy_certificado import garantir_policy

    protocolo = inspect_fonte(garantir_policy)
    assert "if ler_cn_atual() == cn:" in protocolo, "espera a policy APARECER"


# ── §6 · o lease nao usa estado sinalizado ────────────────────────────────────

def test_o_lease_nao_e_sinalizado_nem_lido():
    """A exclusividade e a EXISTENCIA do objeto. `SetEvent` e `ResetEvent` nao
    participam — quem os usa e o canal de limpeza, que e outro objeto."""
    fonte = (RAIZ / "automation" / "exclusividade_host.py").read_text(encoding="utf-8")

    for proibido in ("SetEvent", "ResetEvent", "WaitForSingleObject"):
        assert proibido not in fonte


def test_o_canal_de_limpeza_e_um_objeto_diferente():
    """Dois eventos nomeados, dois propositos. O do canal e por guardiao; o do
    lease e do host inteiro."""
    lease = exclusividade_host.NOME_DO_LEASE
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")

    assert "DebitosEmAberto-guardiao-" in fonte
    assert lease not in fonte, "cert_windows nao escreve o nome do lease"
    assert "host-v1" not in fonte


# ── §4 · o nome ───────────────────────────────────────────────────────────────

def test_o_nome_e_global_e_nao_local_a_sessao():
    """SINGLE_HOST exige que sessoes Windows diferentes colidam."""
    assert exclusividade_host.NOME_DO_LEASE.startswith("Global\\")


def test_o_nome_nao_carrega_nada_que_crie_namespaces_separados():
    """PID, usuario, certificado ou caminho do projeto derrotariam o contrato:
    cada execucao teria o seu proprio lease."""
    import os

    nome = exclusividade_host.NOME_DO_LEASE

    assert str(os.getpid()) not in nome
    for proibido in ("{", "%", "format", os.getlogin()):
        assert proibido not in nome


def test_o_nome_e_uma_constante_de_modulo():
    arvore = ast.parse(
        (RAIZ / "automation" / "exclusividade_host.py").read_text(encoding="utf-8")
    )
    atribuicoes = [
        no for no in arvore.body
        if isinstance(no, ast.Assign)
        and any(getattr(a, "id", "") == "NOME_DO_LEASE" for a in no.targets)
    ]

    assert len(atribuicoes) == 1
    assert isinstance(atribuicoes[0].value, ast.Constant), "literal, nao computado"


# ── §7 · o token ──────────────────────────────────────────────────────────────

def test_o_controle_e_opaco_e_nao_vaza_no_repr():
    controle = ControleDaExclusividade("handle-secreto-123")

    assert repr(controle) == "ControleDaExclusividade(...)"
    assert "handle-secreto-123" not in repr(controle)


def test_o_controle_nao_carrega_dado_de_negocio():
    campos = ControleDaExclusividade.__slots__

    assert campos == ("_handle",)


# ── §10 · §36 · a arquitetura ─────────────────────────────────────────────────

def _importados(caminho):
    arvore = ast.parse(caminho.read_text(encoding="utf-8-sig"))
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes.add(no.module.split(".")[0])
    return nomes


def test_o_app_nao_conhece_o_lease():
    """§10: exclusividade e assunto de runtime, e nao da aplicacao."""
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")

    assert "exclusividade_host" not in fonte
    assert "ctypes" not in fonte


def test_o_app_continua_chamavel_direto_pelos_testes():
    """§11: `app.executar` e API interna e NAO impoe exclusividade por si.

    Nenhum runtime do Windows foi enfiado nele para impedir uso interno — os
    entrypoints e que impoem.
    """
    import inspect

    from automation import app

    assert list(inspect.signature(app.executar).parameters) == [
        "entrada", "config_captcha", "emitir_evento"
    ]


@pytest.mark.parametrize("entrypoint", ["runner.py", "local.py", "main.py"])
def test_os_entrypoints_nao_falam_win32_diretamente(entrypoint):
    """§32: quem encapsula e o modulo de runtime."""
    fonte = (RAIZ / entrypoint).read_text(encoding="utf-8-sig")

    assert "ctypes" not in fonte
    assert "CreateEventW" not in fonte
    assert "exclusividade_host.adquirir()" in fonte


def test_o_dominio_nao_conhece_lease():
    for modulo in ("domain.py", "boundary.py", "status_portal.py", "eventos.py"):
        fonte = (RAIZ / "automation" / modulo).read_text(encoding="utf-8")
        assert "exclusividade" not in fonte
        assert "lease" not in fonte.lower()
