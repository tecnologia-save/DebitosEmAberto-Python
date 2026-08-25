"""Regras puras da automacao. Comeca pela selecao de certificado.

Nada aqui abre navegador, le o registro do Windows, chama PowerShell ou toca disco.
A lista de certificados chega PRONTA — quem a obtem da maquina e outra camada, e
por isso esta regra roda em qualquer sistema operacional.

Por que isso importa mais aqui do que numa regra comum: escolher o certificado
errado significa autenticar na conta de outra empresa. E a unica regra do projeto
cujo defeito nao produz erro — produz acesso indevido.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath

# Cutoff do difflib, preservado do original: a fatia 1.1 mexeu em quantos
# candidatos o criterio aproximado enxerga, nunca em quao parecido algo precisa ser.
_CUTOFF_APROXIMACAO = 0.75

CRITERIO_EXATO = "igualdade exata"
CRITERIO_INICIO = "início do nome"
CRITERIO_PALAVRAS = "palavras inteiras"
CRITERIO_INICIO_COMPACTO = "início do nome sem separadores"
CRITERIO_PARCIAL = "correspondência parcial"
CRITERIO_APROXIMACAO = "aproximação"


@dataclass(frozen=True)
class ResultadoDaBusca:
    """Como terminou a procura pelo certificado.

    Tres desfechos, e os tres precisam ser distinguiveis por quem chama:

    - RESOLVIDA — `chave` e `criterio` preenchidos;
    - AMBIGUA   — `chave` vazia e `ambiguidade` com os candidatos que empataram;
    - SEM MATCH — tudo vazio.

    O tipo existe por uma razao concreta, e nao por simetria com outros modulos:
    a regra antiga imprimia o criterio usado e a lista de candidatos ambiguos.
    Devolver so `str | None` obrigaria a perder essas duas mensagens ou a manter
    `print` dentro de uma regra pura. Assim quem chama decide o que registrar.
    """

    chave: str | None = None
    criterio: str | None = None
    ambiguidade: tuple[str, ...] = ()

    @property
    def resolvida(self) -> bool:
        return self.chave is not None

    @property
    def ambigua(self) -> bool:
        return bool(self.ambiguidade)


def remover_acentos(texto: str) -> str:
    """Remove acentos e diacriticos (NFD -> ASCII)."""
    return unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode("ascii")


def normalizar_nome_certificado(nome: str) -> str:
    """Normaliza o nome escrito na planilha para comparacao.

    ATENCAO — `PurePath(...).stem` esta preservado do original e CORTA tudo depois
    do ultimo ponto: "D.S.R. ASSESSORIA" vira "D.S.R", perdendo justamente a palavra
    que distinguiria dois certificados de assessoria. Ver
    CERTIFICATE_MATCH_POSSIBLE_DEFECT no relatorio da fatia.

    `PurePath` — e nao `Path` — deixa explicito que aqui so ha manipulacao de
    string: `PurePath` nao tem `open`, `exists` nem qualquer acesso a disco.
    """
    return remover_acentos(PurePath(nome).stem.lower())


def tokens_certificado(texto: str) -> list[str]:
    """Palavras normalizadas, na ordem, sem acento nem pontuacao."""
    bruto = re.split(r"[^a-z0-9]+", remover_acentos(str(texto).lower()))
    return [p for p in bruto if p]


def palavras_certificado(texto: str) -> set[str]:
    """Palavras normalizadas de um nome de certificado, sem ordem."""
    return set(tokens_certificado(texto))


def _criterios(chave: str, disponiveis: Mapping[str, str]) -> list[tuple[str, list[str]]]:
    """Os cinco criterios apos a igualdade exata, do mais preciso ao mais frouxo."""
    tokens = tokens_certificado(chave)
    palavras = set(tokens)
    compacto = "".join(tokens)

    criterios: list[tuple[str, list[str]]] = []
    if tokens:
        criterios.append((
            CRITERIO_INICIO,
            [k for k in disponiveis if tokens_certificado(k)[: len(tokens)] == tokens],
        ))
        criterios.append((
            CRITERIO_PALAVRAS,
            [k for k in disponiveis if palavras <= palavras_certificado(k)],
        ))
        criterios.append((
            CRITERIO_INICIO_COMPACTO,
            [k for k in disponiveis if "".join(tokens_certificado(k)).startswith(compacto)],
        ))
    if chave:
        criterios.append((
            CRITERIO_PARCIAL,
            [k for k in disponiveis if chave in k or k in chave],
        ))
        # `n` = todos os nomes disponiveis, e nao 1. Com n=1 o difflib devolve no
        # maximo um resultado, entao o criterio jamais conseguia OBSERVAR um empate:
        # ele sempre "resolvia". O cutoff continua o mesmo — muda quantos candidatos
        # o criterio enxerga, nao quao parecido algo precisa ser.
        criterios.append((
            CRITERIO_APROXIMACAO,
            difflib.get_close_matches(
                chave,
                list(disponiveis.keys()),
                n=max(1, len(disponiveis)),
                cutoff=_CUTOFF_APROXIMACAO,
            ),
        ))
    return criterios


def buscar_certificado(nome: str, disponiveis: Mapping[str, str]) -> ResultadoDaBusca:
    """Resolve o nome escrito na planilha para uma chave de `disponiveis`.

    `disponiveis` mapeia CHAVE NORMALIZADA -> IDENTIDADE do certificado. O mesmo
    certificado costuma aparecer sob varias chaves (nome amigavel, CN, CN sem o
    documento); a identidade e o que diz que sao o mesmo. Sem ela, um nome parcial
    casaria em tres chaves do MESMO certificado e viraria empate sem motivo.

    Ordem de tentativa, da mais precisa para a mais frouxa:
        1. igualdade exata do nome normalizado;
        2. inicio do nome por palavras;
        3. palavras inteiras em qualquer posicao;
        4. inicio do nome ignorando separadores;
        5. substring nos dois sentidos;
        6. aproximacao por difflib.

    Um criterio que empata NAO encerra a busca — o seguinte pode separar os
    candidatos. O empate mais preciso e guardado para ser reportado caso nenhum
    criterio isole um unico certificado.

    A excecao e o criterio 6, o mais frouxo: se algum criterio anterior ja empatou,
    ele nao resolve — a duvida prevalece. E se ele proprio alcanca dois
    certificados distintos, isso tambem e empate. Diante de duvida a regra prefere
    NAO autenticar, porque o certificado errado autentica na conta errada.
    Ver CERTIFICATE_MATCH_BEHAVIOR_CHANGE (fatia 1.1).
    """
    chave = normalizar_nome_certificado(nome)
    if chave in disponiveis:
        return ResultadoDaBusca(chave=chave, criterio=CRITERIO_EXATO)

    empate: tuple[str, tuple[str, ...]] | None = None

    for criterio, candidatos in _criterios(chave, disponiveis):
        if not candidatos:
            continue

        # Uma chave por identidade: chaves diferentes do mesmo certificado nao sao
        # candidatos concorrentes.
        distintos: dict[str, str] = {}
        for k in candidatos:
            distintos.setdefault(disponiveis.get(k) or k, k)

        # O criterio aproximado e o mais frouxo de todos: ele nao tem autoridade
        # para desfazer uma duvida que um criterio mais preciso ja levantou. Sem
        # isto, "ASSESSORIA" — palavra inteira em dois certificados — empatava no
        # criterio 3 e era escolhido em silencio no 6.
        if criterio == CRITERIO_APROXIMACAO and empate is not None:
            break

        if len(distintos) == 1:
            return ResultadoDaBusca(chave=next(iter(distintos.values())), criterio=criterio)

        if empate is None:
            empate = (criterio, tuple(sorted(distintos.values())))

    if empate is not None:
        criterio, candidatos_empatados = empate
        return ResultadoDaBusca(criterio=criterio, ambiguidade=candidatos_empatados)

    return ResultadoDaBusca()
