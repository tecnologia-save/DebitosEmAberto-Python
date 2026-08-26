"""LEGACY_DESKTOP_ENTRYPOINT — Automação Débitos em Aberto.

NÃO é mais o entrypoint arquitetural. A aplicação vive em `automation/app.py`, e
os dois adapters definitivos são:

    runner.py    a plataforma
    local.py     `python local.py --planilha ...`  ← o fluxo local recomendado

Este arquivo continua existindo por três coisas que só ele faz, e que quebrariam
se ele virasse um alias do `local.py`:

    1. a janela do Tkinter que escolhe a planilha (ui_upload);
    2. o dispatch de `--guard`, que no executável congelado é o próprio .exe
       relançado elevado pelo guardião de policy;
    3. LEGACY_SECRET_LOADING, inclusive a chave embutida no binário pelo .spec —
       fallback que os entrypoints novos deliberadamente NÃO têm.

O que ele NÃO tem mais: laço, retry, sessão, certificado, planilha. Tudo isso é
do app, e ele o chama pela mesma fronteira pública que os outros dois.
"""

import argparse
import base64
import logging
import os
import sys
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# servicos_rf_login / resolvedor_captcha: pacotes na mesma pasta (repo)
# ou em pastas irmãs para layout de desenvolvimento legado
if getattr(sys, 'frozen', False):
    LOGIN_ECAC_DIR = Path(sys.executable).parent
else:
    LOGIN_ECAC_DIR = Path(__file__).parent
    _cap_here    = Path(__file__).parent / "resolvedor_captcha"
    _cap_sibling = Path(__file__).parent.parent / "ResolvedorCaptcha"
    if not _cap_here.exists() and _cap_sibling.exists():
        sys.path.insert(0, str(_cap_sibling))

import cert_windows                                                        # noqa: E402
from servicos_rf_login import fazer_login                                  # noqa: E402
from servicos_rf_login.log_manager import registrar_erro                   # noqa: E402
from servicos_rf_login.login import fechar_tutorial_pos_login              # noqa: E402
from ui_upload import main as selecionar_planilha                         # noqa: E402

from automation.domain import buscar_certificado                            # noqa: E402
from automation.domain import remover_acentos as _remover_acentos          # noqa: E402

# Os certificados vêm do Windows Certificate Store (Cert:\CurrentUser\My), não
# mais de uma pasta com .pfx e um senhas.json ao lado.

# Chave Gemini — configure via .env (GEMINI_API_KEY=sua_chave) ou variável de ambiente
_GEMINI_API_KEY_PADRAO = os.environ.get("GEMINI_API_KEY", "")

# Nome do arquivo com a chave embutida no exe pelo debitos_em_aberto.spec
_CHAVE_EMBUTIDA = "chave_gemini.env"


def _ler_chave_de(caminho: Path) -> str:
    """Lê GEMINI_API_KEY de um arquivo no formato .env. '' se não achar."""
    try:
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            if linha.strip().startswith("GEMINI_API_KEY="):
                return linha.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def _resolver_gemini_key() -> tuple[str, str]:
    """Resolve a chave do Gemini. Retorna (chave, origem) para o log.

    Ordem de precedência:
        1. Variável de ambiente já definida — permite trocar a chave numa máquina
           específica sem rebuild.
        2. .env ao lado do executável (ou do script, em desenvolvimento).
        3. Cópia embutida no binário durante o build.

    A chave nunca está no repositório: o .env é gitignored e a cópia embutida é
    gerada pelo .spec a partir dele no momento do build.
    """
    valor = os.environ.get("GEMINI_API_KEY", "").strip()
    if valor:
        return valor, "variável de ambiente"

    env_local = LOGIN_ECAC_DIR / ".env"
    valor = _ler_chave_de(env_local)
    if valor:
        return valor, f"{env_local.name} ao lado do executável"

    base_embutida = getattr(sys, "_MEIPASS", None)
    if base_embutida:
        valor = _ler_chave_de(Path(base_embutida) / _CHAVE_EMBUTIDA)
        if valor:
            return valor, "chave embutida no executável"

    return "", "nenhuma"

# ── Fronteiras da aplicação ───────────────────────────────────────────────────
# As reexportações de `status_portal` saíram com a orquestração: quem afirmava
# sobre elas aqui era a caracterização do laço, e o laço mudou de casa.
from automation import app, apresentacao_eventos, captcha, eventos        # noqa: E402
from automation.boundary import (                                           # noqa: E402
    EntradaDebitosEmAberto,
    EntradaInvalida,
    montar_entrada,
)
from automation.planilha import PlanilhaIndisponivel, validar_recurso       # noqa: E402


# ── Segredo legado ────────────────────────────────────────────────────────────

def _config_captcha() -> captcha.ConfigCaptcha:
    """TRANSITIONAL — a chave do Gemini ainda é lida de `os.environ` AQUI.

    Daqui para baixo ela viaja explicitamente. O `os.environ` sobrevive porque o
    modo desktop/executável legado continua populando-o (LEGACY_SECRET_LOADING).

    Condição de remoção: quando o runner construir a configuração a partir do
    input da execução.
    """
    return captcha.ConfigCaptcha(api_key=os.environ.get("GEMINI_API_KEY", ""))


# ── Apresentação dos eventos ──────────────────────────────────────────────────
# As frases vivem em automation/apresentacao_eventos.py, compartilhadas com
# runner.py e local.py: três implementações das mesmas frases seriam três
# oportunidades de vazar algo diferente.


class Renderer(apresentacao_eventos.Apresentador):
    """Adapter de apresentação do entrypoint desktop legado.

    Acrescenta ao apresentador comum o registro PERSISTENTE da falha de
    gravação, que já existia aqui: só o tipo do erro, porque a mensagem do
    openpyxl carrega o caminho completo do arquivo.
    """

    def __call__(self, evento) -> None:
        super().__call__(evento)
        if evento.codigo == eventos.SALVAMENTO_PLANILHA_FALHOU:
            registrar_erro(f"Planilha: falha ao salvar. {evento.tipo_da_falha}")


# ── Entrada ───────────────────────────────────────────────────────────────────

def _entrada_da_execucao(planilha: str) -> EntradaDebitosEmAberto:
    """TRANSITIONAL — ponte entre os entrypoints atuais e a fronteira tipada.

    Hoje `main()` DESCOBRE a planilha (argumento da CLI ou janela do Tkinter) e
    só depois pede à fronteira que a valide. O destino é o inverso: um runner
    constrói `EntradaDebitosEmAberto` e a entrega pronta.

    Condição de remoção: quando existir o runner/app, esta função e o argparse
    local saem juntos e `main()` passa a receber a entrada como parâmetro.
    Enquanto isso, ela é o único ponto do legado que conhece a fronteira.
    """
    return montar_entrada({"planilha": planilha})


def main() -> None:
    # ── Argumentos de linha de comando ────────────────────────────────────────
    parser = argparse.ArgumentParser(description="Automação Débitos em Aberto")
    parser.add_argument(
        "--planilha", metavar="ARQUIVO",
        help="Caminho da planilha .xlsx (pula o seletor gráfico)"
    )
    parser.add_argument(
        "--log", metavar="ARQUIVO",
        help="Grava os logs também em arquivo (ex.: execucao.log)"
    )
    args = parser.parse_args()

    # ── Logging opcional em arquivo ───────────────────────────────────────────
    if args.log:
        logging.basicConfig(
            filename=args.log,
            filemode="a",
            level=logging.DEBUG,
            format="%(asctime)s %(message)s",
            datefmt="%H:%M:%S",
            encoding="utf-8",
        )
        # Redireciona print para também escrever no log
        _log_file = open(args.log, "a", encoding="utf-8", buffering=1)
        class _Tee:
            def __init__(self, *streams): self.streams = streams
            def write(self, data):
                for s in self.streams:
                    s.write(data)
            def flush(self):
                for s in self.streams:
                    s.flush()
        sys.stdout = _Tee(sys.stdout, _log_file)
        sys.stderr = _Tee(sys.stderr, _log_file)

    # Passo 1: seleção da planilha (UI ou argumento)
    if args.planilha:
        planilha = args.planilha
    else:
        planilha = selecionar_planilha()
    if not planilha:
        print("Nenhuma planilha selecionada. Encerrando.")
        sys.exit(0)

    # A forma vem da fronteira; o recurso, da integração. Ambas antes de qualquer
    # navegador — descobrir que a planilha está aberta no Excel só na primeira
    # gravação custa um login e um CNPJ inteiros.
    try:
        entrada = _entrada_da_execucao(planilha)
        validar_recurso(entrada.planilha)
    except (EntradaInvalida, PlanilhaIndisponivel) as erro_de_entrada:
        print(f"  [!] {erro_de_entrada}")
        sys.exit(2)
    planilha = entrada.planilha

    print(f"\nPlanilha: {planilha}")

    # Carrega o .env local e resolve a GEMINI_API_KEY antes de qualquer chamada
    # ao captcha solver
    _env_path = LOGIN_ECAC_DIR / ".env"
    if _env_path.exists():
        load_dotenv(dotenv_path=_env_path, override=True)

    _chave, _origem = _resolver_gemini_key()
    os.environ["GEMINI_API_KEY"] = _chave
    if _chave:
        # Só a origem. O prefixo e o sufixo da chave saíam daqui para o console
        # e, com `--log`, para um arquivo — SENSITIVE_OUTPUT sem contrapartida.
        print(f"Chave Gemini:  configurada (origem: {_origem})")
    else:
        print("  [!] GEMINI_API_KEY não encontrada — o captcha não será resolvido.")

    # Passo 2: a execução inteira. Descoberta de certificados, leitura da
    # planilha, login, representação, consulta e persistência são do app; o que
    # sobra aqui é traduzir os fatos que ele emite.
    renderer = Renderer()
    app.executar(entrada, _config_captcha(), emitir_evento=renderer)

    if renderer.abortou:
        sys.exit(1)

    print("\nProcessamento concluído.")


if __name__ == "__main__":
    # Processo guardião: relançado ELEVADO por cert_windows.iniciar_guarda, escreve
    # a policy de auto-seleção do Chrome e a remove quando o PID da automação
    # morrer — por qualquer motivo. Precisa vir antes de main() porque no exe
    # congelado o guardião é o próprio executável, com estes argumentos.
    if len(sys.argv) >= 4 and sys.argv[1] == "--guard":
        try:
            cert_windows.guardiao(
                int(sys.argv[2]),
                base64.b64decode(sys.argv[3]).decode("utf-8"),
            )
        except Exception:
            cert_windows.limpar_autoselect()
        sys.exit(0)

    main()
