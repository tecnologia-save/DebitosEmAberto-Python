"""O chao exclusivo de uma execucao: a planilha recebida vira uma copia nossa.

Por que a copia existe
----------------------
A aplicacao GRAVA na planilha durante a execucao — e assim que ela retoma de
onde parou (INOUT_RESOURCE_CONTRACT). Quando quem dispara e a plataforma, o
arquivo que chega e um anexo montado para a run: ele nao e nosso para
sobrescrever. Copiar antes separa as duas coisas sem mexer numa linha da regra —
a aplicacao continua mutando in-place, so que um arquivo dela.

Por que o diretorio e unico
---------------------------
Duas execucoes no mesmo host nao podem dividir chao. Um caminho fixo dentro de
`%TEMP%` continua sendo um caminho fixo: `%TEMP%\\alguma-coisa` nao e isolamento,
e a automacao irma ja pagou por isso — uma run comecou com os arquivos da
anterior ainda no lugar. Aqui cada execucao recebe um diretorio que o proprio
sistema garante inedito, e que some no fim, inclusive quando a execucao morre
por excecao.

Por que o nome do arquivo de trabalho e NOSSO
---------------------------------------------
`planilha.xlsx`, sempre. O caminho que a plataforma entrega pode nao ter
extensao nenhuma — anexo materializado tem nome interno —, e o `openpyxl` recusa
de saida um caminho cuja extensao ele nao reconhece. Quem sabe o formato e quem
deve nomear, e isso e aqui. A extensao do caminho de ORIGEM nao e evidencia de
nada: quem responde o que o arquivo e sao os bytes, e quem os interroga e
`planilha.validar_recurso`.
"""
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from automation.planilha import PlanilhaIndisponivel, validar_recurso

# O nome que a aplicacao vai ver. Estavel de proposito: nada do lado de fora
# atravessa para dentro do espaco, nem o nome do anexo.
NOME_DA_PLANILHA = "planilha.xlsx"

_PREFIXO = "debitos-em-aberto-"


@dataclass(frozen=True)
class EspacoDeTrabalho:
    """O chao desta execucao, e a copia de trabalho da planilha."""

    raiz: Path
    planilha: Path


@contextmanager
def abrir(origem: str | Path) -> Iterator[EspacoDeTrabalho]:
    """Entrega uma copia de trabalho utilizavel da planilha recebida.

    A ORIGEM NAO E TOCADA — nem para gravar, nem para renomear, nem no fim. O
    que a aplicacao recebe e um arquivo em diretorio proprio, e o que acontece
    com ele nao chega ao anexo da plataforma.

    Valida a COPIA, e nao a origem: e a copia que a aplicacao vai abrir e
    gravar, e `validar_recurso` checa justamente a gravacao. Validar o anexo
    diria pouco — ele pode ser somente-leitura sem que isso seja problema.

    Copia invalida faz o espaco ser descartado e a excecao subir: nao existe
    meio-termo em que a execucao comeca com uma planilha que nao abre.
    """
    origem = Path(origem)
    with tempfile.TemporaryDirectory(prefix=_PREFIXO) as raiz:
        destino = Path(raiz) / NOME_DA_PLANILHA
        try:
            shutil.copyfile(origem, destino)
        except OSError:
            # `from None`: a mensagem do OSError carrega o caminho recebido, e
            # ele tem nome de cliente dentro.
            raise PlanilhaIndisponivel(
                "Não foi possível ler a planilha recebida."
            ) from None

        validar_recurso(str(destino))
        yield EspacoDeTrabalho(raiz=Path(raiz), planilha=destino)
