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


def preparar_ambiente_do_certificado(cert_subject_cn: str) -> None:
    """LEGACY_RUNTIME_STATE_TRANSPORT — leva CERT_SUBJECT_CN ate o fork.

    Nao e segredo: e qual certificado esta execucao usa. Vai para dois lugares
    porque o fork le dos dois, e nesta ordem:

        1. `os.environ`, que o fork consulta ao montar a flag
           --auto-select-certificate-for-urls do Chrome;
        2. o ARQUIVO `.env`, porque `fazer_login` chama
           `load_dotenv(..., override=True)` e SOBRESCREVE o ambiente do processo
           com o conteudo do arquivo. Sem a linha no arquivo, o valor que
           acabamos de por no ambiente seria apagado no meio da propria
           execucao.

    Este segundo motivo foi verificado no fork, e nao suposto: escrever so no
    ambiente nao basta.

    O SEGREDO NAO PASSA POR AQUI
    ----------------------------
    Ate a fatia 10 esta funcao tambem gravava `GEMINI_API_KEY` no arquivo, em
    texto puro, quando ela ainda nao estivesse la — SECRET_PERSISTED_TO_DISK.
    Nenhum consumidor do caminho novo lia dali: a chave desce por parametro ate
    o solver desde a 9B.1. O unico leitor era o entrypoint desktop legado, e ele
    continua podendo LER um `.env` que o operador forneceu.

    A distincao e a que importa: o operador configurar um `.env` e uma coisa; a
    automacao escrever o segredo em disco sozinha e outra. A segunda saiu.

    Uma chave que JA esteja no arquivo e preservada intacta — o `.env` do
    usuario nao e reescrito por limpeza.

    Condicao de remocao: quando o fork deixar de ler o ambiente.
    """
    env_path = Path(diretorio_de_perfil()) / ".env"
    existentes: dict[str, str] = {}
    if env_path.exists():
        for linha in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.startswith("#"):
                chave, _, valor = linha.partition("=")
                existentes[chave.strip()] = valor.strip()
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

    preparar_ambiente_do_certificado(certificado.subject_cn)
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
