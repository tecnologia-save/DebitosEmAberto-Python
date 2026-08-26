"""A fiacao desta maquina: o que existe fora do processo e nao e nosso.

Perfil do Chrome, `.env` que o fork le, registro do Windows, o proprio fork.
Tudo TRANSITIONAL — este modulo existe para que nem a aplicacao nem as
fronteiras precisem conhecer nada disso, e some quando o legado sair.

Por que nao dentro de `login.py` ou `policy_certificado.py`
-----------------------------------------------------------
Porque as duas sao NUCLEO: recebem o mundo por parametro e sao testaveis sem
navegador, sem registro e sem Windows. Um teste de arquitetura garante isso, e
foi ele que recusou a primeira tentativa de colocar esta fiacao la dentro.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from automation import login
from automation.login import Certificado, ConfigLogin, ResultadoDoLogin
from automation.policy_certificado import ResultadoDaPolicy


def diretorio_de_perfil() -> str:
    """RUNTIME_DETAIL: qual perfil do Chrome esta execucao usa.

    E um so para toda a execucao — BROWSER_PROFILE_CONCURRENCY_RISK, registrado
    na 7B e nao corrigido aqui. No executavel congelado o perfil fica ao lado do
    .exe; em desenvolvimento, na raiz do repositorio.
    """
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).parent)
    return str(Path(__file__).resolve().parent.parent)


def preparar_ambiente_do_certificado(cert_subject_cn: str, api_key: str) -> None:
    """TRANSITIONAL_LEGACY_ENV — grava CERT_SUBJECT_CN no .env e no processo.

    O fork le `CERT_SUBJECT_CN` do ambiente para montar a flag
    --auto-select-certificate-for-urls do Chrome. Ele proprio ja regrava a
    variavel a partir do parametro, mas o ARQUIVO continua sendo escrito porque
    e o que sobrevive entre execucoes — remover isso seria mudanca funcional
    disfarcada de limpeza.

    A chave do Gemini entra por PARAMETRO, e nao mais de `os.environ`: e o mesmo
    valor que o chamador ja resolveu, com uma leitura de ambiente a menos.

    Condicao de remocao: quando o fork deixar de ler o ambiente.
    """
    env_path = Path(diretorio_de_perfil()) / ".env"
    existentes: dict[str, str] = {}
    if env_path.exists():
        for linha in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.startswith("#"):
                chave, _, valor = linha.partition("=")
                existentes[chave.strip()] = valor.strip()
    if "GEMINI_API_KEY" not in existentes:
        existentes["GEMINI_API_KEY"] = api_key
    # Residuo do modo antigo: se sobrassem no .env, o login tentaria o .pfx.
    existentes.pop("CERT_PFX_PATH", None)
    existentes.pop("CERT_PFX_PASSPHRASE", None)
    existentes["CERT_SUBJECT_CN"] = cert_subject_cn

    env_path.write_text(
        "\n".join(f"{k}={v}" for k, v in existentes.items()) + "\n",
        encoding="utf-8",
    )
    os.environ["CERT_SUBJECT_CN"] = cert_subject_cn


def abrir_sessao(
    certificado: Certificado, auto_select_disponivel: bool, api_key: str
) -> ResultadoDoLogin:
    """Uma sessao autenticada para este certificado, com a fiacao legada dentro.

    O que esta funcao adiciona sobre `autenticar`, e SO isto: o diretorio de
    perfil, a preparacao do ambiente que o fork exige, e o proprio fork. Nenhuma
    decisao de negocio.
    """
    from servicos_rf_login import fazer_login

    preparar_ambiente_do_certificado(certificado.subject_cn, api_key)
    config = ConfigLogin(diretorio_perfil=diretorio_de_perfil(),
                         gemini_api_key=api_key)
    return login.autenticar(
        certificado, config, auto_select_disponivel, fazer_login=fazer_login
    )


def garantir_policy_do_windows(cn: str) -> ResultadoDaPolicy:
    """TRANSITIONAL — a policy desta maquina, com as primitivas ja existentes.

    `cert_windows` fica fora de `automation/` e conhece registro, UAC e o
    processo guardiao. Este atalho existe para que a aplicacao peca a policy sem
    importar nada disso.

    Os findings da fatia 7A continuam abertos e NAO sao tratados aqui:
    POLICY_STALE_OWNERSHIP_GAP, GLOBAL_CERT_POLICY_CONCURRENCY_RISK e
    PARTIAL_POLICY_STATE.
    """
    import cert_windows

    return cert_windows.iniciar_guarda_detalhado(cn)
