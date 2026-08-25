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
import json
import os
import sys
import time
import winreg
from ctypes import wintypes
from pathlib import Path

from automation import policy_certificado

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

def guardiao(pid: int, cn: str) -> None:
    """(roda ELEVADO) Escreve a policy e fica vigiando o processo `pid`. Quando ele
    termina — por QUALQUER motivo — remove a policy."""
    _glog = Path(__file__).parent / "_guard_log.txt"
    def _log(m):
        try:
            with open(_glog, "a", encoding="utf-8") as f:
                f.write(f"{time.time():.1f} {m}\n")
        except Exception:
            pass
    _log(f"=== guardiao start pid={pid} admin={is_admin()} ===")
    try:
        definir_autoselect(cn)
        # Registra em QUAL colmeia caiu: se só HKCU tiver valor e o processo
        # principal não enxergar, é sinal de elevação em outra conta de usuário
        _log(f"policy escrita | {diagnostico()}")
    except Exception as e:
        _log(f"erro definir: {type(e).__name__}: {e}")
    SYNCHRONIZE = 0x00100000
    INFINITE = 0xFFFFFFFF
    h = None
    try:
        h = _k32.OpenProcess(SYNCHRONIZE, False, int(pid))
        _log(f"OpenProcess -> h={h}")
        if h:
            r = _k32.WaitForSingleObject(h, INFINITE)
            _log(f"WaitForSingleObject retornou {r}")
        else:
            _log("OpenProcess falhou (processo ja morreu?)")
            time.sleep(2)
    finally:
        if h:
            _k32.CloseHandle(h)
        removeu = False
        for _ in range(10):
            limpar_autoselect()
            if not policy_existe():
                removeu = True
                break
            time.sleep(0.5)
        _log(f"limpeza removeu={removeu}")


def _lancar_guardiao(cn: str) -> int:
    """Relança ESTE programa elevado no modo guardião. 0 = o Windows aceitou.

    O CN vai em base64 apenas como QUOTING — remove espaços e acentos do argv.
    Não é proteção: qualquer um que veja a linha de comando o decodifica.
    """
    cn_b64 = base64.b64encode(cn.encode("utf-8")).decode("ascii")
    return _runas(_guard_args(["--guard", str(os.getpid()), cn_b64]), wait_ms=None)


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
            guardiao(int(sys.argv[2]), base64.b64decode(sys.argv[3]).decode("utf-8"))
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
