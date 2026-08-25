"""Descoberta dos certificados instalados no Windows — SOMENTE leitura.

Este modulo comeca no Windows Certificate Store e termina numa estrutura Python
pronta para alimentar o match puro de `automation.domain`. Ele nao configura o
Chrome, nao escreve no registro, nao pede UAC e nao inicia guardiao: essas
responsabilidades existem para servir o LOGIN, e serao avaliadas junto dele
(WINDOWS_BROWSER_CERT_POLICY).

O que e especifico de Windows aqui e a FRONTEIRA, nao o contrato. O comando
PowerShell e a unica coisa que precisa de Windows real; tudo depois dele —
interpretar a saida, filtrar, indexar — e determinista e roda em qualquer lugar.
E por isso que a suite inteira passa sem Certificate Store.

Onde o parsing mora
-------------------
Interpretar o formato do Windows e puro, mas NAO pertence a `domain.py`: e
conhecimento sobre um sistema externo, nao regra de negocio. Fica aqui, do lado
de dentro da integracao, e e testavel do lado de fora.

Diagnostico
-----------
Nenhuma funcao daqui imprime, e nenhuma mensagem de erro carrega CN, serial,
nome de empresa, comando ou saida bruta do PowerShell. Quem chama decide o que
mostrar.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess  # a fronteira externa desta integração é um processo, e só ela
from collections.abc import Callable

from .domain import remover_acentos

STORE = r"Cert:\CurrentUser\My"
TIMEOUT_S = 30

# O comando e CONSTANTE. Nenhum dado de planilha, usuario ou ambiente entra nele,
# e e por isso que nao ha superficie de injecao: os argumentos vao em lista, sem
# shell, e o script e literal.
#
# Filtra para certificados UTILIZAVEIS: com chave privada, nao arquivados e
# dentro da validade. Um certificado vencido no repositorio nao deve concorrer
# com um valido de mesmo nome.
COMANDO_POWERSHELL = (
    "$now=Get-Date\n"
    "$c=@(Get-ChildItem Cert:\\CurrentUser\\My|Where-Object{\n"
    "  $_.HasPrivateKey -and\n"
    "  -not $_.Archived -and\n"
    "  $_.NotBefore -le $now -and\n"
    "  $_.NotAfter  -ge $now\n"
    "})\n"
    "$c|ForEach-Object{\n"
    "  $n=if($_.Subject-match 'CN=([^,]+)'){$Matches[1]}else{$_.Subject}\n"
    "  $d=if($_.FriendlyName){[string]$_.FriendlyName}else{$n}\n"
    "  [PSCustomObject]@{\n"
    "    display=$d\n"
    "    subject_cn=$n\n"
    "    thumbprint=$_.Thumbprint\n"
    "    serial=$_.SerialNumber\n"
    "    notafter=$_.NotAfter.ToString('yyyy-MM-dd')\n"
    "  }\n"
    "}|ConvertTo-Json -Compress"
)

# CN da ICP-Brasil termina em ":" seguido do CPF (11) ou CNPJ (14) do titular.
# Certificados de sistema — "Microsoft Your Phone" e afins — tem CN em GUID e nao
# servem para o gov.br. Precisam ficar de fora: apontar a policy de auto-selecao
# para um deles faz o Chrome desistir e abrir a janela de escolha manual.
_RE_CN_ICP = re.compile(r":\s*\d{11}(?:\d{3})?\s*$")


class FalhaAoLerCertificados(Exception):
    """Nao foi possivel ler o repositorio de certificados desta maquina.

    Falha externa CONHECIDA — PowerShell ausente, tempo esgotado, saida
    ilegivel. Erro tecnico nosso e seguro: a mensagem e constante e nunca
    carrega comando, saida bruta, CN ou serial.

    O que NAO passa por aqui: bug nosso. Ele sobe.
    """


# ── A unica fronteira externa ─────────────────────────────────────────────────

def _sem_janela() -> dict:
    """Esconde o console do PowerShell — a automacao ja tem o proprio."""
    if not hasattr(subprocess, "STARTUPINFO"):
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    return {"creationflags": 0x08000000, "startupinfo": info}


def executar_powershell(comando: str) -> str:
    """Roda o comando e devolve o stdout. Levanta em falha externa conhecida.

    E a unica funcao do modulo que precisa de Windows real, e por isso ela e
    substituivel por parametro em `descobrir` — sem service, sem repository, sem
    container.
    """
    enc = base64.b64encode(comando.encode("utf-16-le")).decode("ascii")
    argv = ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc]

    try:
        resultado = subprocess.run(  # noqa: S603 — argv fixo, sem shell, sem interpolação
            argv,
            capture_output=True, text=True, timeout=TIMEOUT_S,
            encoding="utf-8", errors="replace", **_sem_janela(),
        )
    except FileNotFoundError:
        raise FalhaAoLerCertificados("PowerShell não foi encontrado nesta máquina.") from None
    except subprocess.TimeoutExpired:
        raise FalhaAoLerCertificados(
            "A leitura do repositório de certificados demorou demais."
        ) from None
    except OSError:
        raise FalhaAoLerCertificados(
            "Não foi possível executar a leitura dos certificados."
        ) from None

    # O returncode NAO e consultado, preservado do original: um erro nao-terminante
    # do PowerShell pode vir junto de um stdout perfeitamente valido.
    # Ver CERT_WINDOWS_POSSIBLE_DEFECT no relatorio da fatia 5A.
    return resultado.stdout or ""


# ── Interpretacao: pura, e testavel sem Windows ───────────────────────────────

def interpretar_saida(saida: str) -> list[dict]:
    """Converte o JSON do PowerShell em lista de certificados.

    `ConvertTo-Json` de um item so devolve OBJETO, nao array — daí o embrulho.
    """
    texto = (saida or "").strip()
    if not texto:
        return []

    try:
        dados = json.loads(texto)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise FalhaAoLerCertificados(
            "A leitura do repositório de certificados devolveu conteúdo ilegível."
        ) from None

    if isinstance(dados, dict):
        return [dados]
    if isinstance(dados, list):
        return dados
    raise FalhaAoLerCertificados(
        "A leitura do repositório de certificados devolveu um formato inesperado."
    )


def e_certificado_icp(cn: str) -> bool:
    """True se o CN tem a forma da ICP-Brasil ('NOME:CPF' ou 'NOME:CNPJ')."""
    return bool(_RE_CN_ICP.search(str(cn or "")))


def chaves_do_certificado(certificado: dict) -> list[str]:
    """Os nomes normalizados sob os quais um certificado pode ser procurado.

    Sao tres origens — FriendlyName, CN completo e CN sem o documento — porque a
    planilha pode trazer qualquer uma delas. O documento nao ajuda a casar com a
    planilha, entao o nome sem ele tambem entra.

    ATENCAO — a ordem vem de um `set` e NAO e estavel entre execucoes. A
    identidade do certificado nunca varia; qual chave aparece primeiro, sim.
    E o CERTIFICATE_MATCH_POSSIBLE_DEFECT #3 da fatia 1, preservado.
    """
    cn = str(certificado.get("subject_cn") or "").strip()
    nomes = {certificado.get("display") or "", cn, cn.split(":")[0]}
    return [
        chave
        for chave in (remover_acentos(str(nome).strip().lower()) for nome in nomes)
        if chave
    ]


def indexar(certificados: list[dict]) -> tuple[dict[str, dict], int]:
    """Monta {nome_normalizado: certificado}. Devolve tambem quantos foram ignorados.

    Em colisao de nome, o PRIMEIRO vence (`setdefault`) — preservado do original.
    """
    mapa: dict[str, dict] = {}
    ignorados = 0

    for certificado in certificados:
        if not e_certificado_icp(certificado.get("subject_cn")):
            ignorados += 1
            continue
        for chave in chaves_do_certificado(certificado):
            mapa.setdefault(chave, certificado)

    return mapa, ignorados


def identidades(certificados: dict[str, dict]) -> dict[str, str]:
    """O mapa CHAVE -> IDENTIDADE que a regra de match consome.

    A regra nao conhece o formato do Windows; ela so precisa saber quais chaves
    apontam para o mesmo certificado.
    """
    return {
        chave: (valor.get("subject_cn") or chave) if isinstance(valor, dict) else chave
        for chave, valor in certificados.items()
    }


# ── O que a aplicacao chama ───────────────────────────────────────────────────

def descobrir(
    executar: Callable[[str], str] = executar_powershell,
) -> tuple[dict[str, dict], int]:
    """Le os certificados utilizaveis desta maquina e os indexa por nome.

    `executar` e parametro para que a suite nao precise de PowerShell — e a
    fronteira externa inteira desta integracao cabe num callable.

    Levanta `FalhaAoLerCertificados` em falha externa conhecida. Um repositorio
    vazio devolve mapa vazio: sao situacoes DIFERENTES, e distingui-las e o unico
    ponto em que esta fatia se afasta do original de proposito.
    """
    return indexar(interpretar_saida(executar(COMANDO_POWERSHELL)))
