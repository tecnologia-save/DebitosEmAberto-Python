"""Uma execucao de DebitosEmAberto por host Windows, imposta pela automacao.

O contrato foi decidido na fatia 12A e nao e opcional: a policy do Chrome vive
em HKLM, a porta de depuracao e literal, e o perfil e um so. Duas execucoes no
mesmo host disputam os tres.

O mecanismo: a EXISTENCIA de um objeto nomeado do Windows
------------------------------------------------------
Nao um mutex "possuido" pelo processo principal, e nao o estado sinalizado de um
evento. O lease e o proprio OBJETO: ele existe enquanto ALGUM processo mantiver
um handle aberto, e o Windows o destroi quando o ultimo fecha.

Isso e o que fecha HOST_LOCK_CRASH_HANDOFF_RACE. Um mutex do processo principal
seria liberado pelo SO no instante em que ele morre — e o guardiao ainda levaria
alguns momentos para remover a policy. Uma segunda execucao entraria nesse vao e
teria a sua policy apagada pelo guardiao da anterior.

Com o lease como objeto compartilhado:

    processo principal morre  -> o handle dele fecha
    guardiao ainda tem handle -> o objeto CONTINUA existindo
    guardiao limpa a policy   -> so entao fecha o dele
    o objeto desaparece       -> e so agora outra execucao entra

FAIL-CLOSED
-----------
Se nao der para criar, abrir ou verificar o lease — AccessDenied, erro do Win32,
qualquer coisa — a execucao NAO comeca. Uma automacao que nao consegue provar
exclusividade nao deve produzir efeito global nenhum. Isso custa
disponibilidade, e o preco e menor que o de duas execucoes se atropelando no
registro do Windows.

O mesmo vale para uma limpeza que falhou: o guardiao segura o lease, e o host
fica bloqueado ate alguem olhar. HOST_EXCLUSIVITY_FAIL_CLOSED.

O que ainda precisa de maquina de verdade
-----------------------------------------
HOST_LEASE_WINDOWS_SECURITY_VALIDATION_REQUIRED — criacao no namespace
`Global\\`, abertura pelo processo ELEVADO atravessando a fronteira de UAC,
comportamento entre sessoes e contas diferentes. Nada disso e testavel sem
Windows real, e nenhum teste daqui finge que e.
"""
from __future__ import annotations

# `Global\` e nao `Local\`: o contrato e SINGLE_HOST, e o namespace local e por
# SESSAO — duas sessoes Windows obteriam leases independentes e nao colidiriam.
#
# O nome NAO carrega PID, usuario, certificado nem caminho do projeto: qualquer
# um desses criaria namespaces separados e derrotaria a exclusividade que o
# objeto existe para impor. So o identificador estavel da automacao.
NOME_DO_LEASE = "Global\\DebitosEmAberto-host-v1"

ERROR_ALREADY_EXISTS = 183
SYNCHRONIZE = 0x00100000


class ExecucaoJaAtivaNoHost(Exception):
    """Outra execucao — ou o guardiao dela — ainda ocupa este host.

    Mensagem CONSTANTE: nao carrega PID, usuario, nome do objeto nem caminho.
    Quem recebe isto so precisa saber que nao e a vez dele.
    """


class FalhaAoVerificarExclusividade(Exception):
    """Nao foi possivel provar que este host esta livre.

    Nao e o mesmo que "esta ocupado": e nao saber. Fail-closed — a execucao nao
    comeca. Mensagem constante, sem texto bruto do Win32.
    """


class ControleDaExclusividade:
    """O handle do lease, opaco.

    Quem o recebe nao o inspeciona; so o devolve para `liberar`. Nao carrega
    CNPJ, CN, chave nem dado fiscal — so um handle do Windows.
    """

    __slots__ = ("_handle",)

    def __init__(self, handle) -> None:
        self._handle = handle

    def __repr__(self) -> str:
        return "ControleDaExclusividade(...)"


# ── As primitivas do Windows ──────────────────────────────────────────────────
# Isoladas aqui para que a suite prove o PROTOCOLO sem Windows real. Elas sao o
# unico ponto do modulo que toca ctypes.

def _criar_evento(nome: str) -> tuple[object, int]:
    """Cria (ou abre) o objeto nomeado. Devolve (handle, codigo de erro)."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    k32.CreateEventW.restype = wintypes.HANDLE
    k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL,
                                 wintypes.LPCWSTR]
    handle = k32.CreateEventW(None, True, False, nome)
    return handle, ctypes.get_last_error() if not handle else k32.GetLastError()


def _abrir_evento(nome: str) -> object:
    """Abre um objeto que JA existe. Handle falso se nao existir."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    k32.OpenEventW.restype = wintypes.HANDLE
    k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    return k32.OpenEventW(SYNCHRONIZE, False, nome)


def _fechar(handle) -> None:
    import ctypes

    ctypes.windll.kernel32.CloseHandle(handle)


# ── O protocolo ───────────────────────────────────────────────────────────────

def adquirir(criar=_criar_evento, fechar=_fechar) -> ControleDaExclusividade:
    """Toma o host para esta execucao, ou recusa.

    A aquisicao e ATOMICA porque quem decide e o proprio Windows: `CreateEventW`
    com um nome que ja existe devolve um handle valido E
    `ERROR_ALREADY_EXISTS`. Nao ha janela entre "verificar" e "tomar" — nao
    existe verificacao separada.

    O handle devolvido no caso "ja existe" e fechado na hora: manter aberto o
    lease de outra execucao a prolongaria.

    Levanta `ExecucaoJaAtivaNoHost` se o host esta ocupado, e
    `FalhaAoVerificarExclusividade` se nao foi possivel decidir. Nos dois casos a
    execucao NAO comeca.
    """
    try:
        handle, erro = criar(NOME_DO_LEASE)
    except OSError:
        raise FalhaAoVerificarExclusividade(
            "Não foi possível verificar se há outra execução neste computador."
        ) from None

    if not handle:
        raise FalhaAoVerificarExclusividade(
            "Não foi possível verificar se há outra execução neste computador."
        )

    if erro == ERROR_ALREADY_EXISTS:
        fechar(handle)
        raise ExecucaoJaAtivaNoHost(
            "Já existe uma execução do Débitos em Aberto neste computador."
        )

    return ControleDaExclusividade(handle)


def liberar(controle: ControleDaExclusividade, fechar=_fechar) -> None:
    """Fecha o handle DESTA parte da execucao.

    Isto NAO libera o host por si: se o guardiao ainda mantiver o dele, o objeto
    continua existindo e a proxima execucao continua sendo recusada. E
    intencional — e o que impede a entrega do host com policy ainda instalada.
    """
    if controle is None:
        return
    fechar(controle._handle)


def anexar(abrir=_abrir_evento) -> object | None:
    """Abre o lease ja existente, para o processo elevado somar o handle dele.

    Chamado pelo GUARDIAO, e antes de qualquer mutacao. A partir daqui o lease
    sobrevive a morte do processo principal — que e exatamente o intervalo em
    que a corrida acontecia.

    Devolve `None` quando o objeto nao existe: nesse caso o pai ja morreu e o
    lease dele foi destruido, e o guardiao NAO deve escrever policy nenhuma.
    """
    handle = abrir(NOME_DO_LEASE)
    return handle or None
