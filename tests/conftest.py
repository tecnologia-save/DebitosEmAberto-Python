"""Guardas da suite. Nada aqui e produto.

NENHUM TESTE PEDE UAC
---------------------
A regra e antiga e vale para toda a migracao: um teste nao abre navegador, nao
fala com o portal, nao chama o Gemini, nao mexe no registro real e NAO lanca
processo elevado. O que faltava era quem a impusesse.

A fatia 13A.4 mostrou o custo disso. O guardiao deixou de elevar por `_runas` e
passou a elevar por `_elevar`; tres testes continuaram substituindo `_runas`,
e um duble no lugar errado nao intercepta nada. O `ShellExecuteExW` REAL rodou,
com o verbo `runas`, e o Windows abriu a caixa de consentimento — uma por
teste. Nenhuma policy chegou ao registro, porque o consentimento nunca foi
dado; a suite ficou parada esperando alguem responder.

O duble ficou no lugar certo. Esta guarda existe para que o proximo caso desses
falhe como TESTE, e nao como janela.

O SDK DA PLATAFORMA E O DE VERDADE
----------------------------------
`runner.py` importa `autohub_sdk`, e o SDK nao e dependencia de `pip`: o agente
o injeta em runtime, com `PYTHONPATH` apontando para o diretorio onde o
instalador o colocou. A suite faz o mesmo, e so isso — nao ha copia do SDK, nao
ha duble com o nome dele e nao ha versao fixada aqui.

`AUTOHUB_SDK_DIR` aponta para outro diretorio quando o agente nao esta no lugar
padrao. Sem SDK nenhum nada e fingido aqui: os testes que precisam do runtime do
Save se declaram PULADOS, com motivo visivel, e o resto da suite — dominio,
desktop e as guardas de arquitetura — continua rodando.
"""
import os
import sys
from pathlib import Path

import pytest


def _diretorio_do_sdk() -> Path | None:
    explicito = os.environ.get("AUTOHUB_SDK_DIR")
    if explicito:
        return Path(explicito)
    # O `$Dir` padrao do instalador do agente.
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "autohub-edge" if base else None


_SDK = _diretorio_do_sdk()
if _SDK is not None and (_SDK / "autohub_sdk" / "__init__.py").is_file():
    # No FIM do caminho: nada do diretorio do agente encobre um modulo do
    # projeto ou da biblioteca padrao.
    sys.path.append(str(_SDK))


class ElevacaoRealNaSuite(AssertionError):
    """Um teste chegou ao `ShellExecuteExW` de verdade.

    Quase sempre significa que o duble esta na funcao errada: procure quem
    substitui a elevacao neste teste e confira se ainda e ela que o codigo
    chama.
    """


@pytest.fixture(autouse=True)
def _sem_elevacao_real(monkeypatch):
    """Fecha a unica porta da suite para um processo elevado."""
    import cert_windows

    def recusar(*_a, **_k):
        raise ElevacaoRealNaSuite(
            "ShellExecuteExW real durante a suite — nenhum teste pode pedir UAC"
        )

    monkeypatch.setattr(cert_windows._shell32, "ShellExecuteExW", recusar,
                        raising=False)


@pytest.fixture(autouse=True)
def _sem_diretorio_persistente_real(monkeypatch, tmp_path):
    """O perfil do navegador e o marcador de certificados que o runner guarda
    entre execucoes vao para o tmp do teste, nunca para o %LOCALAPPDATA% real."""
    monkeypatch.setenv("DEBITOS_DIRETORIO_PERSISTENTE", str(tmp_path / "persistente"))


@pytest.fixture(autouse=True)
def _sem_certificado_real_no_windows(monkeypatch):
    """Nenhum teste instala, inspeciona ou remove certificado do Windows."""
    import certificados_instalados

    def recusar(*_a, **_k):
        raise AssertionError("PowerShell de certificado real durante a suite")

    monkeypatch.setattr(certificados_instalados, "_powershell", recusar)
