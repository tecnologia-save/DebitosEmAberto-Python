"""Roda a automação SEM a plataforma.

    python local.py --planilha "C:/caminho/planilha.xlsx"

Existe por um motivo prático — desenvolver e depurar sem publicar nada — e por
um motivo estrutural: chama a MESMA API pública que o `runner.py` chama, sem
passar por ele. Os dois são adapters irmãos. Se o local dependesse do adapter da
plataforma, rodar na sua máquina passaria a depender da plataforma.

O que é local de verdade, e por isso mora aqui:

    --planilha   a seleção do arquivo, que na plataforma vem do disparo;
    --log        capturar o stdout num arquivo, para uma execução de horas;
    o `.env`     uma fonte de configuração que o operador prepara na máquina.

O que NÃO mora aqui: nenhuma regra. A aplicação inteira está em
`automation/app.py`, e `tests/test_local.py` reprova quem trouxer regra para cá.

O modo `--guard` NÃO passa por este arquivo. Fora do executável congelado o
guardião é relançado como `python cert_windows.py --guard ...`, que tem o próprio
dispatch; dentro do .exe o programa relançado é o próprio .exe, e o dispatch está
no `main.py` legado. Nenhum dos dois caminhos chega aqui.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from automation import app, apresentacao_eventos, exclusividade_host
from automation.boundary import EntradaInvalida, montar_entrada
from automation.captcha import ConfigCaptcha, ConfiguracaoInvalida
from automation.exclusividade_host import (
    ExecucaoJaAtivaNoHost,
    FalhaAoVerificarExclusividade,
)
from automation.planilha import PlanilhaIndisponivel, validar_recurso

RAIZ = Path(__file__).resolve().parent


def _ler_chave_do_env(caminho: Path) -> str:
    """Lê GEMINI_API_KEY de um arquivo no formato .env. '' se não achar.

    LEITURA apenas. Este entrypoint nunca grava o segredo em disco: o operador
    preparar um `.env` é uma coisa, a automação criar um é outra.
    """
    try:
        linhas = caminho.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for linha in linhas:
        if linha.strip().startswith("GEMINI_API_KEY="):
            return linha.split("=", 1)[1].strip()
    return ""


def resolver_config() -> tuple[ConfigCaptcha, str]:
    """A chave do Gemini e de onde ela veio. A origem é para o operador ler.

    Ordem preservada do comportamento local existente:

        1. variável de ambiente — troca a chave numa máquina sem mexer em arquivo;
        2. `.env` ao lado do script.

    O que ficou de fora de propósito: a cópia embutida no executável. Ela é
    LEGACY_EMBEDDED_SECRET e continua só no entrypoint desktop legado.
    """
    valor = os.environ.get("GEMINI_API_KEY", "").strip()
    if valor:
        return ConfigCaptcha(api_key=valor), "variável de ambiente"

    valor = _ler_chave_do_env(RAIZ / ".env")
    if valor:
        return ConfigCaptcha(api_key=valor), ".env ao lado do script"

    return ConfigCaptcha(api_key=""), "nenhuma"


class _Tee:
    """Escreve nos dois streams. LOCAL_OBSERVABILITY_ADAPTER, nada além disso."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def _ligar_log(caminho: str) -> None:
    """Faz o stdout também cair num arquivo.

    A aplicação não sabe que isto existe: ela emite eventos, o apresentador os
    escreve, e o redirecionamento acontece por baixo.
    """
    logging.basicConfig(
        filename=caminho, filemode="a", level=logging.DEBUG,
        format="%(asctime)s %(message)s", datefmt="%H:%M:%S", encoding="utf-8",
    )
    arquivo = open(caminho, "a", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.stdout, arquivo)
    sys.stderr = _Tee(sys.stderr, arquivo)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Executa a automação localmente.")
    parser.add_argument("--planilha", metavar="ARQUIVO", required=True,
                        help="caminho da planilha .xlsx")
    parser.add_argument("--log", metavar="ARQUIVO",
                        help="grava o que aparece no console também num arquivo")
    args = parser.parse_args(argv)

    if args.log:
        _ligar_log(args.log)

    # A forma vem da fronteira; o recurso, da integração. Ambas ANTES de qualquer
    # navegador — descobrir que a planilha está aberta no Excel só na primeira
    # gravação custa um login e um CNPJ inteiros.
    try:
        entrada = montar_entrada({"planilha": args.planilha})
        validar_recurso(entrada.planilha)
        config, origem = resolver_config()
        config.validar()
    except (EntradaInvalida, PlanilhaIndisponivel) as erro:
        print(f"entrada inválida: {erro}", file=sys.stderr)
        return 2
    except ConfiguracaoInvalida as erro:
        print(f"configuração inválida: {erro}", file=sys.stderr)
        return 2

    print(f"Planilha:      {entrada.planilha}")
    print(f"Chave Gemini:  configurada (origem: {origem})")

    apresentador = apresentacao_eventos.Apresentador()

    # SINGLE_HOST_CONCURRENCY_CONTRACT. O mesmo lease do runner e do desktop
    # legado: um objeto só, para que os três entrypoints colidam entre si.
    try:
        controle = exclusividade_host.adquirir()
    except (ExecucaoJaAtivaNoHost, FalhaAoVerificarExclusividade) as erro:
        print(f"{erro}", file=sys.stderr)
        return 3

    try:
        app.executar(entrada, config, emitir_evento=apresentador)
    finally:
        # Fechar ESTE handle não libera o host por si: se o guardião ainda
        # mantiver o dele, o objeto continua existindo. É intencional.
        exclusividade_host.liberar(controle)

    if apresentador.abortou:
        return 1

    print("\nProcessamento concluído.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
