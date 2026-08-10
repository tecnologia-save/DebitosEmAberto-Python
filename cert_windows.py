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


# ── Operações de registro (podem exigir elevação) ─────────────────────────────

def definir_autoselect(cn: str) -> None:
    """Escreve a policy para o Chrome auto-selecionar o certificado pelo CN."""
    key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_ALL_ACCESS)
    try:
        i = 1
        while True:
            try:
                winreg.DeleteValue(key, str(i))
                i += 1
            except OSError:
                break
        for idx, url in enumerate(CERT_URLS, 1):
            entry = json.dumps({"pattern": url, "filter": {"SUBJECT": {"CN": cn}}})
            winreg.SetValueEx(key, str(idx), 0, winreg.REG_SZ, entry)
    finally:
        winreg.CloseKey(key)
    print(f"[wincert] AutoSelect configurado para: {cn}")


def limpar_autoselect() -> None:
    """Remove a policy, devolvendo o Chrome ao comportamento normal."""
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_PATH)
        print("[wincert] Policy AutoSelect removida.")
    except FileNotFoundError:
        pass
    except OSError:
        pass


def policy_existe() -> bool:
    """True se a policy está escrita no registro."""
    try:
        key = winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
        try:
            winreg.QueryValueEx(key, "1")
            return True
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False


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
        _log("policy escrita")
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


def iniciar_guarda(cn: str) -> bool:
    """Lança o guardião elevado (1 UAC) que escreve a policy e a remove quando ESTE
    processo terminar. Aguarda a policy ficar ativa antes de retornar.

    Retorna True se a policy ficou ativa (Chrome vai auto-selecionar).
    """
    pid = os.getpid()
    cn_b64 = base64.b64encode(cn.encode("utf-8")).decode("ascii")
    rc = _runas(_guard_args(["--guard", str(pid), cn_b64]), wait_ms=None)
    if rc != 0:
        print("[wincert] Nao foi possivel iniciar o guardiao (UAC negado?).")
        return False
    for _ in range(60):  # ~30s
        if policy_existe():
            print("[wincert] Policy ativa — guardiao vigiando p/ limpar no fim.")
            return True
        time.sleep(0.5)
    print("[wincert] Policy nao apareceu a tempo.")
    return False


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
