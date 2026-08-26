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

    Nao e segredo: e qual certificado esta execucao usa.

    `os.environ` e onde ele importa. O fork o le em dois pontos: como fallback ao
    montar a flag --auto-select-certificate-for-urls (o parametro vence, entao na
    pratica nao e usado), e na thread que resolve a janela nativa de certificado
    quando a policy nao esta ativa — esta SEM parametro, so pelo ambiente.

    O ARQUIVO `.env` recebe o mesmo valor por PRESERVACAO DO LEGADO, e nao por
    necessidade demonstrada.

    CORRECAO (fatia 12A). Ate aqui este docstring dizia que o arquivo era
    necessario porque `fazer_login` chamava `load_dotenv(..., override=True)` e
    sobrescreveria o ambiente no meio da execucao. A chamada existe, mas dentro
    de `_resolver_certificado` — que so roda no ramo `.pfx`. No modo Windows
    Store, o unico usado, ela NAO e alcancada. Eu tinha lido a chamada e nao o
    ramo em que ela vive.

    A escrita fica: remove-la seria mudanca funcional sem pedido, e o valor em
    disco alimenta o `load_dotenv()` de import do fork numa proxima execucao.
    Mas o motivo registrado agora e o certo.

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


def liberar_policy_do_windows() -> None:
    """Remove a policy do Chrome desta maquina.

    Chamada SO quando a execucao provocou a escrita — ver
    `_Execucao.liberar_policy`. A primitiva e cega: ela apaga a chave nas duas
    colmeias sem conferir de quem e. Quem confere o ownership e o chamador, e e
    por isso que esta funcao nao recebe CN nenhum: nao ha decisao aqui.

    O guardiao continua existindo como fallback de CRASH. Esta funcao e o
    caminho NORMAL — a diferenca importa porque o guardiao so age quando o
    processo inteiro morre, e um adapter reutilizavel nao morre.
    """
    import cert_windows

    cert_windows.limpar_autoselect()
