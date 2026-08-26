"""Infraestrutura do harness de validacao: trava, evidencia, sanitizacao.

Este pacote NAO faz parte do runtime de producao. Ele nao e importado por
`app`, `runner`, `local`, `main` nem por nenhum modulo de `automation/`, e o
pytest nao o coleta (`testpaths = ["tests"]`).

O que ele faz e chamar as MESMAS primitivas do produto num Windows real, e
registrar o que aconteceu. Nenhum algoritmo e reimplementado aqui.
"""
from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# ── Classificacao ─────────────────────────────────────────────────────────────
PASS = "PASS"        # noqa: S105 — classificacao de teste, nao senha
FAIL_SAFE = "FAIL_SAFE"
FAIL_UNSAFE = "FAIL_UNSAFE"
NOT_TESTED = "NOT_TESTED"

CLASSES = (PASS, FAIL_SAFE, FAIL_UNSAFE, NOT_TESTED)

# ── Sentinelas ficticias ──────────────────────────────────────────────────────
# Nenhum CN, CNPJ ou nome de empresa real entra neste harness. Estes valores
# existem so para ocupar o campo CN da policy de teste.
CN_A = "ALFA FICTICIA LTDA:11111111000191"
CN_B = "BETA FICTICIA SA:22222222000172"


# ── Trava de ambiente ─────────────────────────────────────────────────────────
# O harness escreve no registro do Windows e lanca processo elevado. Rodar isso
# por engano numa maquina de trabalho e exatamente o que o §0 da fatia proibe,
# entao a porta e dupla e as duas metades sao deliberadas: uma variavel de
# ambiente E um arquivo. Nenhuma das duas acontece sem alguem querer.

VARIAVEL = "DEBITOS_VALIDACAO_VM"
FRASE = "confirmo-que-esta-vm-e-descartavel"
MARCADOR = RAIZ / "CONFIRMO_VM_DESCARTAVEL"


class AmbienteNaoAutorizado(Exception):
    """A maquina nao foi declarada descartavel. Nada e executado."""


def exigir_ambiente_descartavel() -> None:
    """Recusa a execucao se a VM descartavel nao tiver sido confirmada.

    Nao ha como detectar "e uma VM com snapshot" de forma confiavel — um
    Manufacturer nao prova rollback. Entao o harness nao adivinha: ele exige
    uma afirmacao explicita de quem tem o snapshot na mao.
    """
    faltando = []
    if os.environ.get(VARIAVEL, "").strip() != FRASE:
        faltando.append(f"variavel {VARIAVEL}={FRASE}")
    if not MARCADOR.exists():
        faltando.append(f"arquivo {MARCADOR.name} em {MARCADOR.parent.name}/")
    if faltando:
        raise AmbienteNaoAutorizado(
            "Este harness escreve no registro e lanca processo elevado. "
            "Ele so roda em VM descartavel com snapshot anterior. Falta: "
            + "; ".join(faltando)
        )


# ── Sanitizacao ───────────────────────────────────────────────────────────────

def _substituicoes() -> list[tuple[str, str]]:
    pares = []
    usuario = os.environ.get("USERNAME", "")
    if usuario:
        pares.append((usuario, "<usuario>"))
    maquina = platform.node()
    if maquina:
        pares.append((maquina, "<host>"))
    for chave in ("USERDOMAIN", "COMPUTERNAME"):
        valor = os.environ.get(chave, "")
        if valor:
            pares.append((valor, "<host>"))
    casa = str(Path.home())
    if casa:
        pares.append((casa, "<home>"))
    # Do mais longo para o mais curto: senao "<home>" seria quebrado ao meio
    # pela substituicao do usuario que esta dentro dele.
    return sorted(pares, key=lambda par: len(par[0]), reverse=True)


_SID = re.compile(r"S-1-5-21(?:-\d+){3,}")
_CNPJ = re.compile(r"\b\d{14}\b")


def sanitizar(texto: str) -> str:
    """Tira da evidencia o que identifica maquina, conta ou empresa.

    DEFESA ADICIONAL, nao garantia: quem escrever o cenario continua podendo
    despejar um valor bruto que este filtro nao conhece. A regra que vale e a
    de sempre — nao coletar o que nao pode ser publicado.

    Os CNs ficticios do harness sao preservados de proposito: sao sentinelas, e
    esconde-los tornaria a evidencia ilegivel.
    """
    if not texto:
        return texto
    for real, mascara in _substituicoes():
        texto = texto.replace(real, mascara)
    texto = _SID.sub("<sid>", texto)

    # Os sentinelas saem de cena antes do filtro de documento e voltam depois.
    # Marcar em volta deles nao bastaria: `\b\d{14}\b` casa do mesmo jeito, e o
    # CN ficticio virava "ALFA FICTICIA LTDA:<documento>" — evidencia ilegivel.
    guardados = {f"\x00{indice}\x00": cn for indice, cn in enumerate((CN_A, CN_B))}
    for ficha, cn in guardados.items():
        texto = texto.replace(cn, ficha)
    texto = _CNPJ.sub("<documento>", texto)
    for ficha, cn in guardados.items():
        texto = texto.replace(ficha, cn)
    return texto


# ── Evidencia ─────────────────────────────────────────────────────────────────

@dataclass
class Evidencia:
    """Um cenario medido. Os campos sao os que a fatia 12E §4 exige."""

    item: str                 # a letra da entrega: C, D, G, ...
    cenario: str
    precondicao: str = ""
    acao: str = ""
    esperado: str = ""
    observado: str = ""
    classificacao: str = NOT_TESTED
    finding: str = ""
    secao: str = ""           # a secao da fatia que pediu o cenario

    def __post_init__(self) -> None:
        if self.classificacao not in CLASSES:
            raise ValueError(f"classificacao desconhecida: {self.classificacao}")

    def sanitizada(self) -> Evidencia:
        campos = {
            nome: sanitizar(valor) if isinstance(valor, str) else valor
            for nome, valor in vars(self).items()
        }
        return Evidencia(**campos)


@dataclass
class Caderno:
    """As evidencias de uma rodada, e o veredito que elas sustentam."""

    evidencias: list[Evidencia] = field(default_factory=list)

    def anotar(self, evidencia: Evidencia) -> Evidencia:
        registrada = evidencia.sanitizada()
        self.evidencias.append(registrada)
        return registrada

    def contagem(self) -> dict[str, int]:
        return {
            classe: sum(1 for e in self.evidencias if e.classificacao == classe)
            for classe in CLASSES
        }

    def houve_unsafe(self) -> bool:
        return any(e.classificacao == FAIL_UNSAFE for e in self.evidencias)

    def markdown(self) -> str:
        linhas = [
            "# Evidencias — fatia 12E",
            "",
            "Gerado pelo harness. Valores sanitizados: sem usuario, host, "
            "caminho pessoal, SID ou documento. Todos os CNs sao ficticios.",
            "",
            "| Item | Secao | Cenario | Esperado | Observado | Classificacao |",
            "|---|---|---|---|---|---|",
        ]
        for e in self.evidencias:
            linhas.append(
                f"| {e.item} | {e.secao} | {_celula(e.cenario)} "
                f"| {_celula(e.esperado)} | {_celula(e.observado)} "
                f"| **{e.classificacao}** |"
            )
        linhas += ["", "## Contagem", ""]
        for classe, quantos in self.contagem().items():
            linhas.append(f"- {classe}: {quantos}")
        linhas += [
            "",
            "## Detalhe",
            "",
        ]
        for e in self.evidencias:
            linhas += [
                f"### {e.item} — {e.cenario}",
                "",
                f"- **Secao:** {e.secao}",
                f"- **Pre-condicao:** {e.precondicao}",
                f"- **Acao:** {e.acao}",
                f"- **Esperado:** {e.esperado}",
                f"- **Observado:** {e.observado}",
                f"- **Classificacao:** {e.classificacao}",
                f"- **Finding:** {e.finding or '—'}",
                "",
            ]
        return "\n".join(linhas)


def _celula(texto: str) -> str:
    """Uma linha so, e sem quebrar a tabela."""
    limpo = " ".join((texto or "—").split()).replace("|", "\\|")
    return limpo if len(limpo) <= 90 else limpo[:87] + "..."
