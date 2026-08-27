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

# ── Estado OWNED: o que esta execução escreve, e só isso ──────────────────────
#
# O host lease da fatia 12C exclui outras execuções de DebitosEmAberto. Ele NÃO
# exclui administrador, GPO, Chrome management nem outra ferramenta — nenhum
# deles pede o nosso lease antes de escrever nesta chave. Logo o lease não prova
# posse dos valores; o único que prova é o conteúdo.
#
# E ele prova bem, porque o nosso estado é determinístico: dado o CN, os sete
# valores saem sempre iguais. Quem tem o CN reconstrói o que escreveu, e não
# precisa guardar cópia de nada.

# ERROR_NO_MORE_ITEMS. E o unico OSError que a enumeracao pode tratar como
# "acabou": qualquer outro e ignorancia, e ignorancia nao e ausencia.
ERROR_NO_MORE_ITEMS = 259

_AUSENTE = object()     # o valor nao existe
_ALHEIO = object()      # existe, lemos, e nao e nosso
_ILEGIVEL = object()    # a leitura falhou; nao sabemos o que ha


def _valores_esperados(cn: str) -> dict[str, str]:
    """Os valores que ESTA execução escreve para `cn`. Nome -> payload."""
    return {
        str(indice): json.dumps({"pattern": url, "filter": {"SUBJECT": {"CN": cn}}})
        for indice, url in enumerate(CERT_URLS, 1)
    }


def _valor_atual(key, nome: str):
    """O conteúdo de um valor, ou um sentinela.

    Três respostas, e as três são necessárias:

        `_AUSENTE`   o valor não existe. Pode ser preenchido.
        `_ALHEIO`    existe, lemos, e não é nosso. Não pode ser tocado.
        `_ILEGIVEL`  a leitura falhou. Não sabemos o que há.

    Até a fatia 13A.2 as duas últimas eram uma só, e pior: qualquer `OSError`
    virava `_AUSENTE`. Um valor que não conseguíssemos ler passava por vago —
    e vago, para a escrita, significa "pode preencher". Ignorância não é
    ausência, e aqui ela deixava de não ser.

    Tipo inesperado continua sendo `_ALHEIO`: lemos, e o que lemos não é nosso.
    Isso é MALFORMED, e é diferente de UNREADABLE.
    """
    try:
        valor, tipo = winreg.QueryValueEx(key, nome)
    except FileNotFoundError:
        return _AUSENTE
    except OSError:
        return _ILEGIVEL
    if tipo != winreg.REG_SZ or not isinstance(valor, str):
        return _ALHEIO
    return valor


def _conflita(key, esperados: dict[str, str]) -> bool:
    """True se algum nome nosso já está ocupado por conteúdo que não é nosso."""
    return any(
        _valor_atual(key, nome) not in (_AUSENTE, esperado)
        for nome, esperado in esperados.items()
    )


# Desfechos de uma tentativa de instalacao. Strings pelo mesmo motivo dos
# desfechos em `policy_certificado`: o consumidor de hoje precisa distinguir
# tres casos, e um bool nao distingue tres.
INSTALADA = "instalada e coerente"
NAO_INSTALADA = "nao instalada, e nada nosso ficou"
RESIDUO_OWNED = "nao instalada, e sobrou estado nosso"


def _conflita_na_colmeia(raiz, esperados: dict[str, str]) -> bool:
    """Algum nome nosso ja esta ocupado por conteudo que nao e o nosso?

    Colmeia ausente nao conflita — e o caso de quem nunca teve a chave.
    Colmeia ILEGIVEL conflita: nao escrevemos no que nao conseguimos ver.
    """
    try:
        key = winreg.OpenKeyEx(raiz, REG_PATH, 0, winreg.KEY_READ)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        return _conflita(key, esperados)
    finally:
        winreg.CloseKey(key)


def _coerente(cn: str) -> bool:
    """O estado FINAL das duas colmeias e exatamente a policy deste CN?

    Usa a mesma semantica forte da fatia 12D — e nao "achei o meu CN em algum
    lugar". `EMPRESTAR` quer dizer: toda colmeia com conteudo esta completa,
    coerente e aponta para o certificado pedido. Uma colmeia ausente nao viola
    isso; uma colmeia com outra coisa dentro, viola.
    """
    decisao = policy_certificado.avaliar_estado_inicial(
        inventario_da_policy(), cn, tuple(CERT_URLS)
    )
    return decisao.decisao == policy_certificado.EMPRESTAR


def definir_autoselect(cn: str) -> str:
    """Instala a policy, ou nao instala em lugar nenhum.

    Historia curta desta funcao, porque cada camada corrigiu a anterior:

    ate a 13A   apagava `1`, `2`, `3`... e escrevia por cima. Regra alheia
                nesse intervalo desaparecia sem aviso.
    13A         passou a conferir antes de escrever, e a pular a colmeia em
                conflito. So que pulava SO ela, e escrevia na outra — e o
                resultado era HKCU apontando para um certificado e HKLM para
                outro. Divergencia criada por nos
                (CROSS_HIVE_PARTIAL_WRITE_RISK).
    13A.1       tres passos, e o segundo so acontece se o primeiro liberar:

        1. CONFERIR AS DUAS. Uma colmeia em conflito ja impede tudo. Instalar
           em metade das colmeias e precisamente o estado divergente que esta
           fatia existe para nunca produzir.
        2. ESCREVER onde for possivel. Colmeia indisponivel por permissao nao
           e conflito: e ausencia, e ausencia sempre fez parte do contrato.
        3. RELER O ESTADO FINAL e exigir que ele seja exatamente o nosso. Se um
           terceiro escreveu DURANTE o passo 2, o resultado nao passa aqui.

    Falhando o passo 3, COMPENSA: remove os valores owned que esta tentativa
    instalou — compare-and-delete, nunca `DeleteKey` — e devolve o que sobrou.
    Nao ha transacao de registro nenhuma nisto; ha tentar, verificar e desfazer
    o proprio rastro.

    A JANELA que resta, dita em voz alta: entre o passo 1 e o passo 2, e dentro
    do proprio passo 2, um terceiro ainda pode escrever. O que mudou e o que
    acontece depois — o passo 3 percebe e desfaz o nosso, em vez de o resultado
    divergente ficar instalado e ser aceito como valido.

    E DAI A REDACAO EXATA DA GARANTIA. Nao e "a automacao nao pode produzir
    divergencia": se a compensacao falhar, residuo owned parcial PODE ficar
    fisicamente no host. O que se garante e mais estreito e mais verdadeiro:

        nenhuma instalacao ACEITA COMO VALIDA deixa divergencia criada por nos.

    Quando a compensacao falha o desfecho e `RESIDUO_OWNED`, e ele ja diz tudo o
    que precisa ser dito: a instalacao nao e aceita, o browser nao comeca, o
    guardiao continua sendo o dono, e o host continua fail-closed.
    """
    esperados = _valores_esperados(cn)

    conflitantes = [
        rotulo for rotulo, raiz in _COLMEIAS
        if _conflita_na_colmeia(raiz, esperados)
    ]
    if conflitantes:
        print(f"[wincert] {'+'.join(conflitantes)}: ja ha configuracao de outra "
              "origem nestes valores; nada foi escrito em colmeia nenhuma.")
        return NAO_INSTALADA

    gravadas = []
    for rotulo, raiz in _COLMEIAS:
        try:
            key = winreg.CreateKeyEx(raiz, REG_PATH, 0, winreg.KEY_ALL_ACCESS)
        except OSError as e:
            print(f"[wincert] {rotulo} indisponivel ({e.__class__.__name__}).")
            continue
        try:
            for nome, valor in esperados.items():
                if _valor_atual(key, nome) is _AUSENTE:
                    winreg.SetValueEx(key, nome, 0, winreg.REG_SZ, valor)
            gravadas.append((rotulo, raiz))
        finally:
            winreg.CloseKey(key)

    if gravadas and _coerente(cn):
        rotulos = "+".join(rotulo for rotulo, _ in gravadas)
        print(f"[wincert] AutoSelect configurado em {rotulos} para: {cn}")
        return INSTALADA

    if not gravadas:
        print(f"[wincert] FALHA: nao foi possivel escrever a policy para: {cn}")
        return NAO_INSTALADA

    # O estado final nao e o nosso: alguem escreveu enquanto escreviamos. Sai o
    # que ESTA tentativa instalou, e so ele.
    print("[wincert] O estado do host mudou durante a escrita; desfazendo o que "
          "esta execucao instalou.")
    for _, raiz in gravadas:
        _remover_owned_da_colmeia(raiz, esperados)

    if policy_owned_existe(cn):
        # A compensacao nao confirmou. Ha estado nosso no host, e ele precisa de
        # dono — HOST_EXCLUSIVITY_FAIL_CLOSED.
        print("[wincert] FALHA: sobrou estado desta execucao no host.")
        return RESIDUO_OWNED
    return NAO_INSTALADA


def _remover_owned_da_colmeia(raiz, esperados: dict[str, str]) -> bool:
    """Compare-and-delete numa colmeia. True se removeu alguma coisa."""
    try:
        key = winreg.OpenKeyEx(raiz, REG_PATH, 0, winreg.KEY_ALL_ACCESS)
    except OSError:
        return False
    removeu = False
    try:
        for nome, esperado in esperados.items():
            if _valor_atual(key, nome) != esperado:
                continue
            try:
                winreg.DeleteValue(key, nome)
                removeu = True
            except OSError:
                # Sem privilegio para remover. Quem percebe e a confirmacao, que
                # le depois — engolir aqui e mentir la e o defeito que a fatia
                # 12D.1 tirou do caminho.
                pass
    finally:
        winreg.CloseKey(key)
    return removeu


def remover_autoselect_owned(cn: str) -> None:
    """Remove SO os valores que ainda sao exatamente os que escrevemos.

    Compare-and-delete, e nunca `DeleteKey`. Tres consequencias deliberadas:

    - valor extra que outra origem acrescentou (`8`, `RegraDaEmpresa`)
      permanece — a fatia 13A existe por causa dele;
    - valor com nome nosso cujo conteudo alguem trocou permanece: o nosso ja
      nao esta la, e a posse daquele nome terminou quando foi sobrescrito.
      Restaurar o nosso seria destruir o de outra pessoa;
    - a chave pode ficar vazia, e fica. Conferir que esta vazia e so entao
      apaga-la abriria uma corrida nova, para um ganho puramente cosmetico — e
      a decisao de startup ja trata chave vazia como host limpo.

    `limpar_autoselect` continua existindo para o `--clean` manual, que e outra
    coisa: la quem manda apagar tudo e uma pessoa.
    """
    removidas = [
        rotulo for rotulo, raiz in _COLMEIAS
        if _remover_owned_da_colmeia(raiz, _valores_esperados(cn))
    ]
    if removidas:
        print(f"[wincert] Policy desta execucao removida de "
              f"{'+'.join(removidas)}.")


def policy_owned_existe(cn: str) -> bool:
    """Ainda resta algum valor que ESTA execução instalou?

    É a pergunta do ciclo de vida OWNED, e não é a mesma de `policy_existe`:

        policy_existe        "existe algum estado de policy no host?"
        policy_owned_existe  "existe estado que ainda é nosso?"

    Depois da remoção não destrutiva as duas divergem de propósito. Estado
    externo que decidimos preservar responde True à primeira e False à segunda,
    e é a segunda que decide se o host pode ser devolvido — senão preservar uma
    regra alheia prenderia o host para sempre.

    Colmeia ilegível responde True: ignorância não é ausência, e pode haver
    estado nosso ali (UNREADABLE_HIVE_BLOCKS_HOST_RELEASE).

    O QUE "OWNED" PROVA, E O QUE NAO PROVA
    --------------------------------------
    Prova CONTENT_MATCH: o payload e exatamente o que esta execucao instalaria
    para este CN. E o suficiente para a decisao que esta funcao serve — remover
    so o nosso, e nao segurar o host por causa do alheio.

    NAO prova proveniencia. Se um terceiro reescrever um nome nosso com um
    payload identico ao nosso, a igualdade e a mesma e nao ha como distinguir os
    dois pelo conteudo — IDENTICAL_EXTERNAL_POLICY_REASSERTION_AMBIGUITY,
    subcaso de EXTERNAL_POLICY_MUTATION_DURING_EXECUTION. Registrado, e nao
    corrigido: distinguir exigiria marcador de dono, que a fatia 12D avaliou e
    recusou por nao resolver o estado que ja esta no host.
    """
    esperados = _valores_esperados(cn)
    for _, raiz in _COLMEIAS:
        try:
            key = winreg.OpenKeyEx(raiz, REG_PATH, 0, winreg.KEY_READ)
        except FileNotFoundError:
            continue
        except OSError:
            return True
        try:
            for nome, esperado in esperados.items():
                atual = _valor_atual(key, nome)
                # `_ILEGIVEL` responde True pelo mesmo motivo que a colmeia
                # inteira ilegivel responde: pode ser nosso, e nao sabemos.
                if atual is _ILEGIVEL or atual == esperado:
                    return True
        finally:
            winreg.CloseKey(key)
    return False


def policy_owned_ainda_existe(controle) -> bool:
    """O mesmo, para quem só tem o controle do guardião.

    Existe para que `maquina` não precise abrir o token: quem sabe o que há
    dentro dele é este módulo, que o criou.
    """
    return policy_owned_existe(controle.cn)


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
    """True se RESTOU estado da policy em alguma colmeia.

    Esta é a pergunta da LIMPEZA, e ela não é a mesma da decisão de startup:

        startup   "posso usar, criar, ou preciso recusar?"  → interpreta regras
        limpeza   "sobrou estado que deveria ter saído?"    → só precisa vê-las

    Até a fatia 12D.1 as duas eram respondidas pela mesma leitura: `_ler_cn`,
    que extrai um CN do valor "1". Isso fazia estado ILEGÍVEL responder
    "limpo" — um payload malformado, um valor com outro nome, um tipo
    inesperado. `DeleteKey` falhando numa colmeia com resíduo desse tipo
    produzia limpeza CONFIRMADA sem nada ter sido removido
    (CLEANUP_CONFIRMATION_FALSE_NEGATIVE), e sobre essa confirmação repousam o
    fim do guardião e a devolução do host.

    Estado ilegível é estado existente. Uma colmeia que não pôde ser lida também
    conta: ignorância não é ausência.

    ASSIMETRIA DELIBERADA com `avaliar_estado_inicial`: uma chave que existe e
    está VAZIA é "host limpo" para o startup — sem regras o Chrome não seleciona
    nada — e é "restou estado" aqui, porque a chave é coisa que a nossa escrita
    cria e a nossa limpeza tem de remover. Perguntas diferentes, respostas
    diferentes.
    """
    return any(
        colmeia.existe or not colmeia.legivel
        for colmeia in inventario_da_policy()
    )


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


def _interpretar(nome: str, bruto: object) -> policy_certificado.RegraDaPolicy:
    """Uma entrada do registro virada regra — ou marcada como ilegível.

    Nada de palpite: um payload que não tem a forma que conhecemos sai com
    `reconhecida=False`, e não com campos meio preenchidos.
    """
    try:
        dado = json.loads(bruto)
        padrao = dado["pattern"]
        cn = dado["filter"]["SUBJECT"]["CN"]
        if not isinstance(padrao, str) or not isinstance(cn, str):
            raise TypeError(nome)
        if set(dado) != {"pattern", "filter"}:
            raise ValueError(nome)
    except (TypeError, ValueError, KeyError):
        return policy_certificado.RegraDaPolicy(nome, reconhecida=False)
    return policy_certificado.RegraDaPolicy(nome, padrao=padrao, cn=cn)


def _inventariar(rotulo: str, raiz) -> policy_certificado.ColmeiaDaPolicy:
    try:
        key = winreg.OpenKeyEx(raiz, REG_PATH, 0, winreg.KEY_READ)
    except FileNotFoundError:
        return policy_certificado.ColmeiaDaPolicy(rotulo, existe=False)
    except OSError:
        # Existe e não pudemos ler. Não é o mesmo que não existir, e tratá-los
        # igual seria escrever por cima do que não vimos.
        return policy_certificado.ColmeiaDaPolicy(rotulo, existe=True, legivel=False)

    regras = []
    completa = False
    try:
        indice = 0
        while True:
            try:
                nome, bruto, _ = winreg.EnumValue(key, indice)
            except OSError as erro:
                # ERROR_NO_MORE_ITEMS e o fim da lista. Qualquer outro erro e
                # ignorancia: parar nele e devolver o que ja tinhamos seria
                # entregar uma lista TRUNCADA como se fosse a colmeia inteira,
                # e quem a lesse acreditaria ter visto tudo.
                completa = getattr(erro, "winerror", None) == ERROR_NO_MORE_ITEMS
                break
            regras.append(_interpretar(nome, bruto))
            indice += 1
    finally:
        winreg.CloseKey(key)

    if not completa:
        return policy_certificado.ColmeiaDaPolicy(rotulo, existe=True, legivel=False)
    return policy_certificado.ColmeiaDaPolicy(
        rotulo, existe=True, regras=tuple(regras)
    )


def inventario_da_policy() -> tuple[policy_certificado.ColmeiaDaPolicy, ...]:
    """O estado COMPLETO da policy nas duas colmeias.

    `policy_cn` responde "qual CN o Chrome vai aplicar" olhando um valor de uma
    colmeia. Isso decide bem quando o host é nosso e decide mal quando há estado
    que não escrevemos: um valor fora do índice 1 fica invisível, uma colmeia
    divergente nunca é consultada, e um payload malformado passa por ausência.

    Esta leitura não resume nada — é ela que a decisão de startup consome.
    """
    return tuple(_inventariar(rotulo, raiz) for rotulo, raiz in _COLMEIAS)


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
    nunca o inspeciona, e o app so o carrega.

    Desde a fatia 13A ele guarda o CN, e isso mudou de proposito. A limpeza
    deixou de apagar a chave inteira e passou a comparar valor a valor com o que
    aquele guardiao escreveu — e o CN e exatamente o que permite reconstruir
    esses valores, sem guardar copia de payload nenhum.

    Nao entra no `__repr__`: um CN identifica a empresa e nao pode cair em log.
    DEFESA ADICIONAL, nao garantia — acesso direto ao atributo continua expondo,
    e e por isso que so `cert_windows` o le.
    """

    __slots__ = ("evento", "nome", "cn")

    def __init__(self, evento, nome: str, cn: str):
        self.evento = evento
        self.nome = nome
        self.cn = cn

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


def _limpar_confirmando(cn: str) -> bool:
    """Remove o que ESTA execucao instalou e CONFIRMA que saiu.

    Fatia 13A: `limpar_autoselect` (DeleteKey da chave inteira) saiu daqui. O
    que se remove agora sao os valores owned, e o que se confirma e a ausencia
    DELES — nao a ausencia de qualquer estado. Estado externo preservado nao e
    motivo para segurar o host.
    """
    for _ in range(10):
        remover_autoselect_owned(cn)
        if not policy_owned_existe(cn):
            return True
        time.sleep(0.5)
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
    # SEM DIAGNOSTICO PERSISTENTE (fatia 13B). Ate aqui esta funcao acumulava um
    # arquivo de log ao lado do modulo, com o PID e com o CN do certificado
    # que a leitura de estado devolve. Ninguem o lia: era diagnostico, e nao
    # arquivo com a identidade da empresa, crescendo em disco e sobrevivendo a
    # execucao, nao se justifica por poder ajudar a depurar.
    #
    # O custo esta registrado: o guardiao roda elevado, destacado e sem janela,
    # e agora nao tem canal de observacao nenhum. Quem precisar ve-lo agir ve
    # pelo efeito — a policy no registro e o lease do host.

    # ── ORDEM OBRIGATORIA, e ela e a prova da fatia 12C ──────────────────────
    # 1. anexar ao lease do host    2. conferir que o pai vive    3. so entao
    # escrever. Invertida, a corrida volta: o pai morre, o lease dele some, uma
    # segunda execucao entra, e este guardiao escreve policy por cima dela.
    lease = exclusividade_host.anexar()
    if lease is None:
        return

    h = _k32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not h:
        _k32.CloseHandle(lease)
        return
    # O pai estava vivo quando ja tinhamos o lease. Dai em diante a morte dele
    # nao destroi o objeto: o nosso handle o mantem.

    # REVALIDACAO IMEDIATA (fatia 13A). A decisao que autoriza esta escrita foi
    # tomada no processo principal, ANTES da elevacao — antes de um prompt de
    # UAC que pode ter ficado minutos na tela. Ela nao vale para sempre: quem
    # decide se ainda e seguro escrever e quem esta a um passo de escrever.
    decisao = policy_certificado.avaliar_estado_inicial(
        inventario_da_policy(), cn, tuple(CERT_URLS)
    )
    if decisao.decisao == policy_certificado.RECUSAR:
        _k32.CloseHandle(h)
        _k32.CloseHandle(lease)
        return

    escrita = NAO_INSTALADA
    try:
        escrita = definir_autoselect(cn)
        # Registra em QUAL colmeia caiu: se só HKCU tiver valor e o processo
        # principal não enxergar, é sinal de elevação em outra conta de usuário
    except Exception:  # noqa: BLE001 — a falha vira NAO_INSTALADA logo abaixo
        pass

    if escrita == NAO_INSTALADA:
        # Nao instalamos nada e nada nosso ficou, entao nao possuimos nada — e
        # um guardiao que nao possui estado nao pode segurar o host. O processo
        # principal descobre relendo o estado, e reavalia.
        _k32.CloseHandle(h)
        _k32.CloseHandle(lease)
        return

    # INSTALADA ou RESIDUO_OWNED: em ambos ha estado nosso no host, e estado
    # nosso precisa de dono. No segundo caso a instalacao falhou e a compensacao
    # nao confirmou — o processo principal nunca vera a policy, vai recusar, e
    # este guardiao continua sendo quem limpa.
    evento = _k32.OpenEventW(SYNCHRONIZE, False, canal) if canal else None
    try:
        while True:
            if evento:
                alvos = (wintypes.HANDLE * 2)(h, evento)
                r = _k32.WaitForMultipleObjects(2, alvos, False, INFINITE)
                pai_morreu = r == WAIT_OBJECT_0
            else:
                _k32.WaitForSingleObject(h, INFINITE)
                pai_morreu = True

            if _limpar_confirmando(cn):
                break

            # NAO confirmado. Nao encerramos: uma policy OWNED sem processo
            # elevado responsavel e pior do que um host ocupado.
            if not pai_morreu:
                continue

            # O pai ja morreu e a limpeza falhou. HOST_EXCLUSIVITY_FAIL_CLOSED.
            for _ in range(REPETICOES_APOS_A_MORTE):
                time.sleep(INTERVALO_REPETICAO_S)
                if _limpar_confirmando(cn):
                    break
            else:
                # Esgotou. NAO fechamos o lease: uma policy owned sem processo
                # elevado responsavel e pior do que um host ocupado. E nao
                # ficamos girando sobre o registro — este processo PARA aqui,
                # bloqueado no proprio lease, que nunca e sinalizado. Sem CPU,
                # sem laco, e o host continua nosso ate alguem olhar.
                _k32.WaitForSingleObject(lease, INFINITE)
            break
    finally:
        if h:
            _k32.CloseHandle(h)
        if evento:
            _k32.CloseHandle(evento)
        # POR ULTIMO: enquanto este handle existir, o host continua ocupado.
        _k32.CloseHandle(lease)


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
    return ControleDoGuardiao(evento, nome, cn)


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
        avaliar_inicio=lambda: policy_certificado.avaliar_estado_inicial(
            inventario_da_policy(), cn, tuple(CERT_URLS)
        ),
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
        except Exception:
            # Antes isto gravava um arquivo de erro com a mensagem da excecao,
            # que pode carregar caminho de registro. Saiu junto com o resto do
            # diagnostico persistente (fatia 13B).
            #
            # GUARDIAN_CLEANUP_DELETES_WHOLE_KEY residual, REGISTRADO e nao
            # corrigido: esta limpeza continua sendo a destrutiva. Ela escapou
            # da fatia 13A porque vive no `__main__`, fora de `guardiao`, e o
            # subsistema de policy esta congelado ate a 12E.
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
