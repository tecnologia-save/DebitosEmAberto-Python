"""Auto-seleção de certificado no Chrome via policy do Windows.

Usa SOMENTE o certificado já INSTALADO no repositório do Windows. Em vez de passar
o .pfx para o Playwright (cujo proxy TLS do Node falha com ICP-Brasil — SSL alert 40),
o Chrome apresenta o certificado nativamente (CAPI) e a policy
`AutoSelectCertificateForUrls` faz o Chrome escolher o cert certo pelo CN, SEM diálogo.

A policy fica em HKCU\\Software\\Policies\\Google\\Chrome\\AutoSelectCertificateForUrls.

IMPORTANTE — limpeza garantida:
Para não deixar o auto-select "grudado" no Chrome normal do usuário, a policy é
gerenciada por um PROCESSO GUARDIÃO elevado (iniciar_guarda). Ele escreve a policy
no início e fica vigiando o PID da automação; quando a automação morre por QUALQUER
motivo (fim normal, erro, Ctrl+C, fechar console, kill forçado), o guardião remove a
policy. Como é um processo separado e já elevado, sobrevive à morte da automação e
garante a limpeza com um único UAC.
"""
import base64
import ctypes
import itertools
import json
import os
import sys
import time
import winreg
from ctypes import wintypes
from pathlib import Path

from automation import exclusividade_host, policy_certificado

# URLs do eCAC / acesso.gov.br onde o certificado é solicitado.
CERT_URLS = [
    "https://certificado.sso.acesso.gov.br",
    "https://sso.acesso.gov.br",
    "https://acesso.gov.br",
    "https://cav.receita.fazenda.gov.br",
    "https://[*.]receita.fazenda.gov.br",
    "https://[*.]fazenda.gov.br",
    "https://[*.]gov.br",
]
REG_PATH = r"Software\Policies\Google\Chrome\AutoSelectCertificateForUrls"

# A policy é escrita nas DUAS colmeias, e não só em HKCU.
#
# O guardião roda elevado. Quando o UAC eleva usando uma conta de administrador
# diferente da do usuário logado, o HKEY_CURRENT_USER do guardião é a colmeia
# DAQUELA conta — a policy é gravada com sucesso, mas o Chrome, rodando como o
# usuário normal, nunca a enxerga. O sintoma é exatamente este: o guardião
# registra "policy escrita" e mesmo assim a janela de seleção aparece.
#
# HKLM não tem essa ambiguidade: é machine-wide e o Chrome sempre a lê. HKCU
# continua sendo escrita porque funciona sem elevação em máquinas onde a ACL
# permite, e porque é o caminho usado pelas demais automações.
_COLMEIAS = (
    ("HKCU", winreg.HKEY_CURRENT_USER),
    ("HKLM", winreg.HKEY_LOCAL_MACHINE),
)


# ── Operações de registro (podem exigir elevação) ─────────────────────────────

def definir_autoselect(cn: str) -> None:
    """Escreve a policy para o Chrome auto-selecionar o certificado pelo CN.

    Grava em HKCU e HKLM. Basta uma das duas dar certo; um erro de permissão na
    outra é esperado e não interrompe.
    """
    entradas = [
        json.dumps({"pattern": url, "filter": {"SUBJECT": {"CN": cn}}})
        for url in CERT_URLS
    ]
    gravadas = []
    for rotulo, raiz in _COLMEIAS:
        try:
            key = winreg.CreateKeyEx(raiz, REG_PATH, 0, winreg.KEY_ALL_ACCESS)
            try:
                i = 1
                while True:
                    try:
                        winreg.DeleteValue(key, str(i))
                        i += 1
                    except OSError:
                        break
                for idx, entry in enumerate(entradas, 1):
                    winreg.SetValueEx(key, str(idx), 0, winreg.REG_SZ, entry)
                gravadas.append(rotulo)
            finally:
                winreg.CloseKey(key)
        except OSError as e:
            print(f"[wincert] {rotulo} indisponivel ({e.__class__.__name__}).")

    if gravadas:
        print(f"[wincert] AutoSelect configurado em {'+'.join(gravadas)} para: {cn}")
    else:
        print(f"[wincert] FALHA: nao foi possivel escrever a policy para: {cn}")


def limpar_autoselect() -> None:
    """Remove a policy das duas colmeias, devolvendo o Chrome ao normal."""
    removidas = []
    for rotulo, raiz in _COLMEIAS:
        try:
            winreg.DeleteKey(raiz, REG_PATH)
            removidas.append(rotulo)
        except FileNotFoundError:
            pass
        except OSError:
            pass
    if removidas:
        print(f"[wincert] Policy AutoSelect removida de {'+'.join(removidas)}.")


def _ler_cn(raiz) -> str:
    """CN escrito na policy da colmeia indicada, ou '' se não houver."""
    try:
        key = winreg.OpenKeyEx(raiz, REG_PATH, 0, winreg.KEY_READ)
        try:
            valor, _ = winreg.QueryValueEx(key, "1")
        finally:
            winreg.CloseKey(key)
        return json.loads(valor).get("filter", {}).get("SUBJECT", {}).get("CN", "")
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def policy_existe() -> bool:
    """True se a policy está escrita em pelo menos uma das colmeias."""
    return any(_ler_cn(raiz) for _, raiz in _COLMEIAS)


def policy_cn() -> str:
    """CN da policy que o Chrome vai aplicar, ou '' se não houver nenhuma.

    Lê da perspectiva de quem chama: se este processo (não elevado, o mesmo
    contexto do Chrome) não enxerga a policy, ela não vale — é justamente o caso
    que o guardião elevado em outra conta produziria.
    """
    for _, raiz in _COLMEIAS:
        cn = _ler_cn(raiz)
        if cn:
            return cn
    return ""


def diagnostico() -> str:
    """Resumo do estado da policy em cada colmeia, para o log."""
    partes = []
    for rotulo, raiz in _COLMEIAS:
        cn = _ler_cn(raiz)
        partes.append(f"{rotulo}={cn or '(vazio)'}")
    return "  ".join(partes)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ── Elevação (UAC) ────────────────────────────────────────────────────────────

class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
    ]


_k32 = ctypes.windll.kernel32
_shell32 = ctypes.windll.shell32
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.WaitForSingleObject.restype = wintypes.DWORD
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.CloseHandle.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.GetExitCodeProcess.restype = wintypes.BOOL
_k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
# O canal de limpeza normal (fatia 12B.2). Um evento nomeado do proprio Windows:
# o dono e o SO, ele morre com os processos que o abriram, e nao deixa arquivo
# para trás se a máquina cair no meio.
_k32.CreateEventW.restype = wintypes.HANDLE
_k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL,
                              wintypes.LPCWSTR]
_k32.OpenEventW.restype = wintypes.HANDLE
_k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k32.SetEvent.restype = wintypes.BOOL
_k32.SetEvent.argtypes = [wintypes.HANDLE]
_k32.WaitForMultipleObjects.restype = wintypes.DWORD
_k32.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
                                        wintypes.BOOL, wintypes.DWORD]

EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0x00000000


class ControleDoGuardiao:
    """O que permite PEDIR limpeza a um guardiao especifico.

    Opaco para quem o recebe: o protocolo em automation/policy_certificado.py
    nunca o inspeciona, e o app so o carrega. Nao guarda CN, CNPJ nem segredo —
    so o nome do canal e o handle do evento.
    """

    __slots__ = ("evento", "nome")

    def __init__(self, evento, nome: str):
        self.evento = evento
        self.nome = nome

    def __repr__(self) -> str:
        return "ControleDoGuardiao(...)"
_shell32.ShellExecuteExW.restype = wintypes.BOOL
_shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]


def _runas(args: list, wait_ms=None) -> int:
    """Roda sys.executable + args ELEVADO (UAC). Retorna -1 se UAC negado."""
    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    sei = _SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "runas"
    sei.lpFile = sys.executable
    sei.lpParameters = " ".join(args)
    sei.nShow = 0  # SW_HIDE
    if not _shell32.ShellExecuteExW(ctypes.byref(sei)):
        return -1
    if wait_ms is None:
        if sei.hProcess:
            _k32.CloseHandle(sei.hProcess)
        return 0
    _k32.WaitForSingleObject(sei.hProcess, wait_ms)
    code = wintypes.DWORD()
    _k32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
    _k32.CloseHandle(sei.hProcess)
    return int(code.value)


def _guard_args(extra: list) -> list:
    """Monta os args para relançar ESTE programa elevado (lida com .exe congelado)."""
    if getattr(sys, "frozen", False):
        return extra                                   # exe --guard ...
    return [f'"{Path(__file__).resolve()}"'] + extra   # python cert_windows.py --guard ...


# ── Processo guardião ──────────────────────────────────────────────────────────

# Entre tentativas de limpeza depois que o pai ja morreu. Longo de proposito:
# nao ha pressa, e um laco apertado num processo elevado seria pior que o
# problema.
INTERVALO_REPETICAO_S = 30
REPETICOES_APOS_A_MORTE = 20


def _limpar_confirmando(_log) -> bool:
    """Remove a policy e CONFIRMA que ela saiu. Dez tentativas curtas."""
    for _ in range(10):
        limpar_autoselect()
        if not policy_existe():
            _log("limpeza confirmada")
            return True
        time.sleep(0.5)
    _log("limpeza NAO confirmada")
    return False



def guardiao(pid: int, cn: str, canal: str = "") -> None:
    """(roda ELEVADO) Escreve a policy e a remove quando não for mais necessária.

    Duas formas de saber que chegou a hora, e é a segunda que a fatia 12B.2
    acrescentou:

        PID morreu   — crash, Ctrl+C, kill. É a razão de este processo existir,
                       e continua intacta.
        canal        — o processo principal PEDIU a limpeza, e ainda está vivo.
                       Sem isso, num adapter reutilizável a policy ficava
                       instalada por tempo indefinido depois de a execução
                       acabar.

    `canal` é opcional: sem ele o comportamento é exatamente o de antes.
    """
    _glog = Path(__file__).parent / "_guard_log.txt"
    def _log(m):
        try:
            with open(_glog, "a", encoding="utf-8") as f:
                f.write(f"{time.time():.1f} {m}\n")
        except Exception:
            pass
    _log(f"=== guardiao start pid={pid} admin={is_admin()} ===")

    # ── ORDEM OBRIGATORIA, e ela e a prova da fatia 12C ──────────────────────
    # 1. anexar ao lease do host    2. conferir que o pai vive    3. so entao
    # escrever. Invertida, a corrida volta: o pai morre, o lease dele some, uma
    # segunda execucao entra, e este guardiao escreve policy por cima dela.
    lease = exclusividade_host.anexar()
    if lease is None:
        _log("lease do host nao existe — o pai ja morreu; abortando sem escrever")
        return

    h = _k32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not h:
        _log("pai ja morreu antes da escrita; abortando")
        _k32.CloseHandle(lease)
        return
    # O pai estava vivo quando ja tinhamos o lease. Dai em diante a morte dele
    # nao destroi o objeto: o nosso handle o mantem.

    try:
        definir_autoselect(cn)
        # Registra em QUAL colmeia caiu: se só HKCU tiver valor e o processo
        # principal não enxergar, é sinal de elevação em outra conta de usuário
        _log(f"policy escrita | {diagnostico()}")
    except Exception as e:
        _log(f"erro definir: {type(e).__name__}: {e}")
    evento = _k32.OpenEventW(SYNCHRONIZE, False, canal) if canal else None
    _log(f"canal -> {evento}")
    try:
        while True:
            if evento:
                alvos = (wintypes.HANDLE * 2)(h, evento)
                r = _k32.WaitForMultipleObjects(2, alvos, False, INFINITE)
                pai_morreu = r == WAIT_OBJECT_0
            else:
                _k32.WaitForSingleObject(h, INFINITE)
                pai_morreu = True
            _log(f"acordou ({'pid morreu' if pai_morreu else 'limpeza pedida'})")

            if _limpar_confirmando(_log):
                break

            # NAO confirmado. Nao encerramos: uma policy OWNED sem processo
            # elevado responsavel e pior do que um host ocupado.
            if not pai_morreu:
                _log("limpeza pedida falhou; voltando a vigiar o pai")
                continue

            # O pai ja morreu e a limpeza falhou. HOST_EXCLUSIVITY_FAIL_CLOSED.
            _log("FAIL-CLOSED: policy owned continua; host permanece ocupado")
            for _ in range(REPETICOES_APOS_A_MORTE):
                time.sleep(INTERVALO_REPETICAO_S)
                if _limpar_confirmando(_log):
                    break
            else:
                # Esgotou. NAO fechamos o lease: uma policy owned sem processo
                # elevado responsavel e pior do que um host ocupado. E nao
                # ficamos girando sobre o registro — este processo PARA aqui,
                # bloqueado no proprio lease, que nunca e sinalizado. Sem CPU,
                # sem laco, e o host continua nosso ate alguem olhar.
                _log("FAIL-CLOSED definitivo: aguardando intervencao")
                _k32.WaitForSingleObject(lease, INFINITE)
            break
    finally:
        if h:
            _k32.CloseHandle(h)
        if evento:
            _k32.CloseHandle(evento)
        # POR ULTIMO: enquanto este handle existir, o host continua ocupado.
        _k32.CloseHandle(lease)
        _log("lease do host liberado")


_SEQUENCIA_DE_GUARDIOES = itertools.count(1)


def _lancar_guardiao(cn: str) -> ControleDoGuardiao | None:
    """Relança ESTE programa elevado no modo guardião. `None` = UAC recusado.

    O CN vai em base64 apenas como QUOTING — remove espaços e acentos do argv.
    Não é proteção: qualquer um que veja a linha de comando o decodifica.

    Devolve o CONTROLE, e não um código: sem ele o processo principal não tem
    como pedir a limpeza depois. `_runas` fecha o handle do processo elevado e
    devolve 0, então o canal precisa existir ANTES do lançamento — o nome vai
    no argv, e o guardião o abre do outro lado.

    O nome inclui PID e sequência porque cada policy tem o seu guardião: uma
    troca de certificado lança outro, e pedir limpeza ao errado apagaria a
    policy em uso (MULTIPLE_POLICY_GUARDIANS_LIFETIME).
    """
    nome = (r"Local\DebitosEmAberto-guardiao-"
            f"{os.getpid()}-{next(_SEQUENCIA_DE_GUARDIOES)}")
    evento = _k32.CreateEventW(None, True, False, nome)
    if not evento:
        return None

    cn_b64 = base64.b64encode(cn.encode("utf-8")).decode("ascii")
    if _runas(_guard_args(["--guard", str(os.getpid()), cn_b64, nome]),
              wait_ms=None) != 0:
        _k32.CloseHandle(evento)
        return None
    return ControleDoGuardiao(evento, nome)


def pedir_limpeza(controle: ControleDoGuardiao) -> None:
    """Sinaliza ao guardião que ele pode remover a policy AGORA.

    Só o pedido. Quem confirma que a policy saiu é
    `policy_certificado.liberar_policy`, lendo o registro — e é por isso que um
    guardião surdo não trava nada: a confirmação nunca chega, a policy continua
    sendo do chamador, e o fallback de crash segue de pé.
    """
    _k32.SetEvent(controle.evento)


def iniciar_guarda_detalhado(cn: str) -> policy_certificado.ResultadoDaPolicy:
    """Garante a policy e devolve COMO ela ficou — inclusive quem vai limpá-la.

    O protocolo vive em automation/policy_certificado.py; aqui ficam as
    primitivas do Windows e o que vai para o console.
    """
    resultado = policy_certificado.garantir_policy(
        cn,
        ler_cn_atual=policy_cn,
        lancar_guardiao=_lancar_guardiao,
        aguardar=lambda: time.sleep(policy_certificado.INTERVALO_SONDAGEM_S),
    )

    if resultado.situacao == policy_certificado.JA_ATIVA:
        print(f"[wincert] Policy ja ativa para: {cn}")
        print("[wincert] AVISO: nenhum guardiao desta execucao — ela nao sera "
              "removida por nos ao terminar.")
    elif resultado.situacao == policy_certificado.ATIVADA:
        print("[wincert] Policy ativa — guardiao vigiando p/ limpar no fim.")
    elif resultado.situacao == policy_certificado.ELEVACAO_RECUSADA:
        print("[wincert] Nao foi possivel iniciar o guardiao (UAC negado?).")
    else:
        atual = policy_cn()
        if atual:
            print(f"[wincert] Policy ativa com OUTRO CN ({atual}); esperado {cn}.")
        else:
            print("[wincert] Policy nao visivel deste processo — o Chrome tambem nao a vera.")
            print("          Provavel elevacao em outra conta de usuario (HKCU diferente).")
        print(f"[wincert] Estado: {diagnostico()}")

    return resultado


def iniciar_guarda(cn: str) -> bool:
    """Compatibilidade: o `policy_ok` booleano que o login consome hoje.

    Esperar pelo CN, e não só pela existência da policy, é o que torna seguro
    trocar de certificado no meio da execução: a policy do certificado anterior
    ainda está escrita quando o novo guardião sobe, e conferir só a existência
    devolveria True de imediato — o Chrome subiria com o certificado errado.
    """
    return iniciar_guarda_detalhado(cn).confiavel


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--guard":
        try:
            guardiao(int(sys.argv[2]), base64.b64decode(sys.argv[3]).decode("utf-8"),
                     sys.argv[4] if len(sys.argv) >= 5 else "")
        except Exception as e:
            try:
                (Path(__file__).parent / "_wincert_erro.log").write_text(
                    f"guard: {type(e).__name__}: {e}", encoding="utf-8")
            except Exception:
                pass
            limpar_autoselect()
        sys.exit(0)

    if len(sys.argv) >= 2 and sys.argv[1] == "--clean":
        limpar_autoselect()
        sys.exit(0)

    if len(sys.argv) >= 2:
        print("admin:", is_admin())
        print("iniciar_guarda ->", iniciar_guarda(sys.argv[1]))
        print("(o guardiao limpa quando este processo terminar — pressione ENTER)")
        try:
            input()
        except Exception:
            pass
    sys.exit(0)
