"""Ponto de entrada — Automação Débitos em Aberto.

Fluxo:
    1. Abre janela para seleção da planilha (ui_upload)
    2. Lê a aba 'Empresas' da planilha:
         Coluna A = CNPJ
         Coluna B = EMPRESA
         Coluna C = CERTIFICADO
         Coluna D = resultado (preenchida pela automação)
    3. Ordena pela coluna C (certificado) para minimizar logins no eCAC
    4. Para o primeiro CNPJ de cada grupo de certificado:
         - Login no eCAC via LoginEcac
         - Navega para servicos.receitafederal.gov.br e autentica
    5. Para cada CNPJ (incluindo os subsequentes do mesmo certificado):
         - Aguarda intervalo mínimo de 30s entre trocas de CNPJ no portal
         - Representa o CNPJ como Procurador no portal
         - Navega para a página de pendências
         - Verifica status e escreve resultado na coluna D
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
from automation import app, captcha, eventos                                # noqa: E402
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

def _frase(e) -> str | None:
    """Um fato da aplicação vira a linha que o operador lê. `None` = não mostrar.

    Aqui, e só aqui, o vocabulário volta a ser humano. O app emite CÓDIGOS: ele
    não sabe que existe console, nem `--log`, nem português.

    Nenhuma frase tem CNPJ, empresa ou CN — o app não os envia. Quando o
    operador precisa achar a linha, ele recebe a POSIÇÃO e abre a planilha nela.
    """
    E = eventos
    onde = "" if e.posicao is None else f"linha {e.posicao + 1}"
    de = "" if e.total is None else f"/{e.total}"

    if e.codigo == E.ITEM_INICIADO:
        return f"\n  [{onde}{de}] Processando..."
    if e.codigo == E.ITEM_IGNORADO_SEM_CNPJ:
        return f"  [{onde}{de}] CNPJ inválido/vazio. Ignorando linha."
    if e.codigo == E.ITEM_JA_ENCERRADO:
        return f"    → [{onde}] Status terminal na coluna D. Nada a processar."
    if e.codigo == E.ITEM_JA_CONCLUIDO:
        return f"    → [{onde}] Já totalmente processada. Pulando."
    if e.codigo == E.RETOMADA_PULA_DCTFWEB:
        return "    → Débitos DCTFWeb já concluídos. Fará apenas Processos Fiscais."
    if e.codigo == E.RETOMADA_PULA_PROCESSOS:
        return "    → Processos Fiscais já concluídos. Fará apenas Débitos DCTFWeb."

    if e.codigo == E.CERTIFICADO_INICIADO:
        return f"\n{'═' * 60}\n  Certificado da {onde} em diante\n{'═' * 60}"
    if e.codigo == E.CERTIFICADO_NAO_INSTALADO:
        return (f"  [!] O certificado pedido na {onde} não está instalado nesta "
                "máquina. Abra a planilha nessa linha para ver qual é.")
    if e.codigo == E.CERTIFICADO_AMBIGUO:
        return (f"  [!] O nome de certificado da {onde} corresponde a "
                f"{e.quantidade} certificados instalados.\n"
                "       Escreva na planilha um nome que identifique só um deles.")
    if e.codigo == E.LEITURA_DE_CERTIFICADOS_FALHOU:
        return ("  [!] Não foi possível ler o repositório de certificados desta "
                "máquina.\n"
                "       Isso NÃO significa que não há certificado "
                "instalado — a leitura em si falhou.")
    if e.codigo == E.CERTIFICADOS_INDISPONIVEIS:
        return ("  [!] Nenhum certificado com chave privada, válido e não arquivado,\n"
                "      foi encontrado em Cert:\\CurrentUser\\My.\n"
                "      Instale o certificado no Windows antes de rodar a automação.")
    if e.codigo == E.POLICY_NAO_CONFIAVEL:
        return ("    [!] Policy de auto-seleção não ficou ativa (UAC negado?). "
                "A janela de certificado será resolvida por UI.")
    if e.codigo == E.POLICY_PERMANECERA_NA_MAQUINA:
        return ("    [!] A policy do Chrome já existia e continuará na máquina "
                "depois desta execução.")
    if e.codigo == E.LOGIN_CONCLUIDO:
        return "    [✓] Login no portal concluído."
    if e.codigo == E.LOGIN_FALHOU:
        return f"    [!] Login não autenticou. Pulando a {onde}."

    if e.codigo == E.CNPJ_RECUSADO_PELO_PORTAL:
        return f"    [!] O portal recusou o CNPJ da {onde}. Linha encerrada."
    if e.codigo == E.SESSAO_RECUPERADA_APOS_RECUSA:
        return "    [✓] Sessão mantida — seguindo para o próximo CNPJ deste certificado."
    if e.codigo == E.SESSAO_NAO_RECUPERADA_APOS_RECUSA:
        return "    [!] Sessão não recuperada após a recusa. Fechando o navegador."
    if e.codigo == E.ITEM_FALHOU:
        resta = "Reabrindo sessão e retentando" if e.tentativa < e.maximo else "Pulando"
        return (f"    [!] Falha ao processar a {onde} "
                f"(tentativa {e.tentativa}/{e.maximo}). {resta}...")
    if e.codigo == E.ITEM_ESGOTOU_RETENTATIVAS:
        return f"    [!] Máximo de tentativas atingido na {onde}."
    if e.codigo == E.SITUACAO_FISCAL_NAO_RECONHECIDA:
        return (f"    [!] Status de pendências não reconhecido na {onde}. "
                "Nada foi gravado — a linha voltará pendente.")

    if e.codigo == E.DEBITOS_REGISTRADOS:
        if e.quantidade is None:
            return "    [✓] Coluna D → concluída pelo Processo Fiscal."
        return (f"    [✓] {e.quantidade} linha(s) de débito DCTFWeb em "
                f"{e.paginas} página(s) — gravadas, coluna D concluída.")
    if e.codigo == E.PROCESSOS_REGISTRADOS:
        return (f"    [✓] {e.quantidade} linha(s) de processo fiscal em "
                f"{e.paginas} página(s) — gravadas, coluna E concluída.")
    if e.codigo == E.SEM_DEBITOS_REGISTRADO:
        return "    [✓] Sem débitos."
    if e.codigo == E.DEBITOS_NAO_COMPENSAVEIS_REGISTRADO:
        return "    [✓] Débitos não compensáveis."
    if e.codigo == E.SEM_PROCESSOS_REGISTRADO:
        return "    [✓] Sem processos."
    if e.codigo == E.RECUSA_REGISTRADA:
        return "    [✓] Motivo da recusa gravado na planilha."
    if e.codigo == E.LINHA_NAO_ENCONTRADA_NA_PLANILHA:
        return (f"    [!] A {onde} não foi encontrada na planilha para escrita. "
                "O status foi descartado.")
    if e.codigo == E.SALVAMENTO_PLANILHA_FALHOU:
        acao = (" — feche o arquivo no Excel e a próxima gravação recupera."
                if e.tipo_da_falha == "PermissionError" else "")
        return f"    [!] Falha ao salvar a planilha ({e.tipo_da_falha}){acao}"

    if e.codigo == E.REDE_NAO_ESTABILIZOU:
        return "    [!] A página não estabilizou no tempo; a extração seguiu assim mesmo."
    if e.codigo == E.PAGINACAO_NAO_ALTERADA:
        return "    [!] Não foi possível alterar os itens por página."
    return None


class Renderer:
    """Adapter de apresentação: EventoOperacional → console (e `--log` via Tee).

    É o único lugar que imprime, e o app não sabe que ele existe. Guarda uma só
    coisa: se os certificados faltaram — o processo precisa terminar com código
    1 nesse caso, e a fronteira do app devolve `None` de propósito.
    """

    def __init__(self) -> None:
        self.abortou = False

    def __call__(self, evento) -> None:
        if evento.codigo in (eventos.CERTIFICADOS_INDISPONIVEIS,
                             eventos.LEITURA_DE_CERTIFICADOS_FALHOU):
            self.abortou = True
        if evento.codigo == eventos.SALVAMENTO_PLANILHA_FALHOU:
            # Registro PERSISTENTE da falha, preservado do adapter antigo. Só o
            # tipo: a mensagem do openpyxl carrega o caminho completo do arquivo,
            # e isto aqui vai para um log em disco.
            registrar_erro(f"Planilha: falha ao salvar. {evento.tipo_da_falha}")
        frase = _frase(evento)
        if frase is not None:
            print(frase)


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
