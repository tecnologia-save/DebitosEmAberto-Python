"""Ponto de entrada do harness de validacao da fatia 12E.

    python executar.py --seco                 # nao toca em nada; so exercita a estrutura
    python executar.py --grupo lease          # um grupo, na VM descartavel
    python executar.py --grupo tudo           # a bateria inteira

A execucao real exige a confirmacao dupla descrita em `comum.exigir_ambiente_descartavel`.
`--seco` dispensa a trava justamente porque nao faz nada.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cenarios import GRUPOS, PAPEIS
from comum import RAIZ, AmbienteNaoAutorizado, Caderno, exigir_ambiente_descartavel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validacao de runtime Windows (12E)")
    parser.add_argument("--grupo", default="tudo",
                        choices=("tudo", *GRUPOS), help="qual bateria rodar")
    parser.add_argument("--seco", action="store_true",
                        help="nao toca no Windows; exercita a estrutura do harness")
    parser.add_argument("--papel", choices=tuple(PAPEIS),
                        help="uso interno: este processo e um filho do cenario")
    parser.add_argument("--saida", default=None,
                        help="arquivo markdown da evidencia")
    args = parser.parse_args(argv)

    if args.papel:
        return PAPEIS[args.papel]()

    if not args.seco:
        try:
            exigir_ambiente_descartavel()
        except AmbienteNaoAutorizado as erro:
            print(f"[12E] RECUSADO: {erro}", file=sys.stderr)
            return 2

    caderno = Caderno()
    escolhidos = GRUPOS if args.grupo == "tudo" else {args.grupo: GRUPOS[args.grupo]}
    for nome, grupo in escolhidos.items():
        print(f"[12E] grupo {nome}...", flush=True)
        grupo(caderno, seco=args.seco)

    destino = Path(args.saida) if args.saida else RAIZ / "evidencias" / "rodada.md"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(caderno.markdown(), encoding="utf-8")

    contagem = caderno.contagem()
    print(f"[12E] {contagem} -> {destino}")
    if caderno.houve_unsafe():
        print("[12E] HA FAIL_UNSAFE: o veredito da fatia e NAO.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
