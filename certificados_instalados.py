"""Certificados do cofre instalados no Windows DURANTE a execução — e só nela.

POR QUE
-------
Entregue como arquivo (`client_certificates`), o certificado faz o Playwright
pôr um proxy entre o Chrome e o gov.br: quem negocia o TLS com o portal passa a
ser o Node, e não o navegador. O portal vê uma conexão que não é de navegador,
desconfia, e o hCaptcha aparece — o que o executável desktop, que usa o
certificado INSTALADO, não vive. Instalado no repositório do usuário
(`Cert:\\CurrentUser\\My`), o certificado é apresentado pelo próprio Chrome.

O QUE ESTE MÓDULO FAZ
---------------------
Envolve o provedor do cofre. Em `carregar`, cada `.pfx` que o cofre entregou é
instalado no repositório do usuário (não exige administrador nem UAC), e o que
sai para a aplicação passa a ser `Certificado(subject_cn, serial)` — o modo
Windows Store do login. Em `encerrar`, o que ESTA execução instalou é removido,
com a chave privada.

- Certificado que JÁ estava instalado (o do operador do desktop, por exemplo)
  é usado e NUNCA removido: não fomos nós que o pusemos lá.
- Antes de instalar, a impressão digital vai para um marcador em disco. Se a
  execução morrer no meio, a próxima remove o que ficou (`carregar` começa por
  aí). O marcador guarda só impressões digitais — nenhum nome, CNPJ ou senha.
- A chave é instalada NÃO exportável.
- Falhar ao instalar um certificado não derruba nada: aquele alias continua no
  modo arquivo, como antes.

SEM POLICY DO WINDOWS
---------------------
A policy de auto-seleção exige administrador, e no agente ninguém responde ao
UAC. `pede_policy_do_windows = False` diz isso à aplicação: o Chrome abre a
janela "Selecione um certificado" e o login a resolve pelo serial
(`servicos_rf_login.cert_dialog`), escopada ao processo do Chrome desta run.

A SENHA do `.pfx` vai ao PowerShell pela entrada padrão, nunca pela linha de
comando, e nada aqui escreve alias, CN, serial ou caminho em log.
"""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from automation.login import Certificado


@dataclass(frozen=True)
class Instalado:
    impressao: str
    subject_cn: str
    serial: str
    ja_existia: bool


_PREAMBULO = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Security
$X = [System.Security.Cryptography.X509Certificates.X509Certificate2]
$F = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]
"""

# Inspeciona com chave EFÊMERA (nada persiste), decide, e só então instala.
_INSPECIONAR = _PREAMBULO + r"""
$senha = [Console]::In.ReadLine()
$c = New-Object $X($env:DEBITOS_PFX, $senha, $F::EphemeralKeySet)
$ja = Test-Path ("Cert:\CurrentUser\My\" + $c.Thumbprint)
@{ impressao = $c.Thumbprint; cn = $c.GetNameInfo('SimpleName', $false);
   serial = $c.SerialNumber; ja = $ja; chave = $c.HasPrivateKey } | ConvertTo-Json -Compress
"""

_INSTALAR = _PREAMBULO + r"""
$senha = [Console]::In.ReadLine()
$c = New-Object $X($env:DEBITOS_PFX, $senha, ($F::UserKeySet -bor $F::PersistKeySet))
$s = New-Object System.Security.Cryptography.X509Certificates.X509Store('My', 'CurrentUser')
$s.Open('ReadWrite'); try { $s.Add($c) } finally { $s.Close() }
"""

_REMOVER = _PREAMBULO + r"""
$p = "Cert:\CurrentUser\My\" + $env:DEBITOS_IMPRESSAO
if (Test-Path $p) { Remove-Item -Path $p -DeleteKey }
"""


def _powershell(script: str, env_extra: dict, entrada: str = "") -> str:
    env = {**os.environ, **env_extra}
    processo = subprocess.run(  # noqa: S603 — argv fixo, sem shell; senha pela stdin
        ["powershell", "-NoProfile", "-NonInteractive",  # noqa: S607 — o do sistema
         "-ExecutionPolicy", "Bypass", "-Command", script],
        input=entrada, capture_output=True, text=True, encoding="utf-8",
        env=env, timeout=60, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if processo.returncode != 0:
        # Só a CLASSE do problema: o stderr do PowerShell pode ecoar o caminho.
        raise RuntimeError(f"PowerShell terminou com código {processo.returncode}")
    return processo.stdout


def inspecionar_no_windows(pfx_path: str, senha: str) -> Instalado:
    """Le o `.pfx` com chave efemera: impressao, CN, serial, e se ja esta instalado."""
    dados = json.loads(_powershell(_INSPECIONAR, {"DEBITOS_PFX": pfx_path}, senha + "\n"))
    if not dados.get("chave"):
        raise RuntimeError("o .pfx não trouxe chave privada")
    return Instalado(dados["impressao"], dados.get("cn") or "", dados.get("serial") or "",
                     bool(dados.get("ja")))


def gravar_no_windows(pfx_path: str, senha: str) -> None:
    _powershell(_INSTALAR, {"DEBITOS_PFX": pfx_path}, senha + "\n")


def remover_do_windows(impressao: str) -> None:
    _powershell(_REMOVER, {"DEBITOS_IMPRESSAO": impressao})


class CertificadosInstalados:
    """Provedor que instala os certificados do cofre no Windows e os remove ao fim."""

    pede_policy_do_windows = False

    def __init__(self, do_cofre, marcador: str | Path, *,
                 inspecionar: Callable[[str, str], Instalado] = inspecionar_no_windows,
                 gravar: Callable[[str, str], None] = gravar_no_windows,
                 remover: Callable[[str], None] = remover_do_windows) -> None:
        self._cofre = do_cofre
        self._marcador = Path(marcador)
        self._inspecionar = inspecionar
        self._gravar = gravar
        self._remover = remover
        self._catalogo: dict[str, Certificado] = {}
        self.instalados_agora = 0

    # ── o protocolo que a aplicação conhece ──────────────────────────────────

    def carregar(self) -> int:
        self._limpar_residuos()
        quantos = self._cofre.carregar()
        for nome, do_arquivo in self._cofre.itens().items():
            self._catalogo[nome] = self._instalar(do_arquivo)
        if self.instalados_agora:
            print(f"[cert] {self.instalados_agora} certificado(s) do cofre instalado(s) "
                  "no Windows para esta execução (removidos ao final).")
        return quantos

    def resolver(self, nome: str):
        return self._cofre.resolver(nome)

    def certificado(self, chave: str) -> Certificado:
        return self._catalogo.get(chave) or self._cofre.certificado(chave)

    def encerrar(self) -> None:
        """Remove o que ESTA execução instalou. Idempotente."""
        restantes = []
        for impressao in self._ler_marcador():
            try:
                self._remover(impressao)
            except Exception:  # noqa: BLE001 — fica no marcador; a próxima tenta
                restantes.append(impressao)
        self._escrever_marcador(restantes)
        if restantes:
            print(f"[cert] {len(restantes)} certificado(s) não puderam ser removidos "
                  "agora; a próxima execução tenta de novo.")

    # ── por dentro ───────────────────────────────────────────────────────────

    def _instalar(self, do_arquivo: Certificado) -> Certificado:
        try:
            info = self._inspecionar(do_arquivo.pfx_path, do_arquivo.pfx_senha)
            if not info.subject_cn:
                return do_arquivo
            if not info.ja_existia:
                # O marcador ANTES da instalação: morrer entre as duas deixa uma
                # remoção sem efeito, e não um certificado esquecido.
                self._escrever_marcador([*self._ler_marcador(), info.impressao])
                self._gravar(do_arquivo.pfx_path, do_arquivo.pfx_senha)
                self.instalados_agora += 1
        except Exception:  # noqa: BLE001 — este alias segue no modo arquivo
            return do_arquivo
        return Certificado(subject_cn=info.subject_cn, serial=info.serial)

    def _limpar_residuos(self) -> None:
        if self._ler_marcador():
            print("[cert] Removendo certificados que uma execução anterior deixou instalados.")
            self.encerrar()

    def _ler_marcador(self) -> list[str]:
        try:
            return list(json.loads(self._marcador.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return []

    def _escrever_marcador(self, impressoes: list[str]) -> None:
        unicas = list(dict.fromkeys(impressoes))
        if not unicas:
            self._marcador.unlink(missing_ok=True)
            return
        self._marcador.parent.mkdir(parents=True, exist_ok=True)
        self._marcador.write_text(json.dumps(unicas), encoding="utf-8")
