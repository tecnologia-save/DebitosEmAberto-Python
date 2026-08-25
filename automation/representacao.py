"""A fronteira da representação: sessão autenticada + CNPJ → desfecho nomeado.

O que esta fronteira acrescenta
-------------------------------
`trocar_perfil_procurador` tem 348 linhas de seletor e espera, e comunica seu
resultado de três formas diferentes: `None` para sucesso, `FalhaPermanente` para
recusa do CNPJ, e `Exception` genérica para anti-bot e para representação não
confirmada. Um chamador precisa de dois `except` e de leitura de mensagem para
saber o que aconteceu.

Aqui isso vira UM resultado com quatro situações, e cada uma diz o que a camada
de cima pode fazer a respeito.

FalhaPermanente para aqui
-------------------------
Ela era TRANSITIONAL_CONTROL_FLOW: um desfecho esperado viajando como exception
por três frames. Esta é a primeira fronteira em que dá para convertê-la em
resultado atravessando UM frame só — o adapter no legado converte de volta
enquanto o laço antigo existir. É por isso que a conversão acontece aqui e não
na camada de app.

O que fica de fora
------------------
Login, policy, registro, UAC, guardião. A representação recebe uma
`SessaoReceita` PRONTA e não é dona dela: não fecha contexto, não para o
Playwright, não faz logout. Quem decide recuperação e encerramento é a futura
camada de orquestração.

Política de retry também fica de fora: a fronteira REPORTA que o anti-bot
esgotou as tentativas do portal; decidir entre relogar, esperar ou desistir é da
orquestração, não daqui.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


class AntiBotEsgotado(Exception):
    """O portal acusou acesso automatizado e as tentativas dele acabaram.

    Existe para que o desfecho seja distinguível por TIPO. Herda de `Exception`,
    então o `except Exception` do laço legado continua capturando — a mudança é
    retrocompatível.
    """


class RepresentacaoNaoConfirmada(Exception):
    """O portal não confirmou o contribuinte representado, ou o captcha não foi
    resolvido, depois de todas as tentativas. Mesma razão de existir."""


REPRESENTADO = "representado"
RECUSA_DO_CNPJ = "recusa deste CNPJ"
ANTI_BOT_ESGOTADO = "bloqueio anti-bot após as tentativas do portal"
NAO_CONFIRMADO = "representação não confirmada"


@dataclass(frozen=True)
class ResultadoDaRepresentacao:
    """Como terminou a tentativa de representar o contribuinte.

    `status_coluna_d` só vem preenchido quando o portal deu um motivo conhecido —
    é o mesmo status da fatia 2, e é o que encerra a linha na planilha.

    Nenhum campo carrega CNPJ, empresa ou texto bruto do portal.
    """

    situacao: str
    status_coluna_d: str | None = None

    @property
    def representado(self) -> bool:
        return self.situacao == REPRESENTADO

    @property
    def encerra_a_linha(self) -> bool:
        """True quando insistir neste CNPJ não adianta nesta execução.

        Distingue-se de `not representado`: anti-bot e não-confirmado são
        problemas da SESSÃO ou do momento, e a linha volta na próxima execução.
        """
        return self.situacao == RECUSA_DO_CNPJ


def representar(
    sessao,
    cnpj: str,
    executar: Callable[[object, str], None],
    recusa_do_portal: type[BaseException],
) -> ResultadoDaRepresentacao:
    """Representa `cnpj` na sessão e devolve o desfecho.

    `executar` é a navegação legada inteira, injetada — é o que permite testar
    este contrato sem navegador. `recusa_do_portal` é a classe que o legado usa
    para sinalizar recusa conhecida; ela entra por parâmetro em vez de import
    para que este módulo não dependa de `main`.

    Os desfechos são distinguidos por TIPO, nunca por texto de mensagem. Foi
    para isso que `AntiBotEsgotado` e `RepresentacaoNaoConfirmada` existem: o
    legado levantava `Exception` nua nos dois casos, e ler a frase para separá-los
    seria heurística frágil sobre texto que ninguém garante.

    Bug nosso sobe: só as três famílias que o legado usa para comunicar desfecho
    são traduzidas.
    """
    try:
        executar(sessao.pagina, cnpj)
    except recusa_do_portal as recusa:
        return ResultadoDaRepresentacao(
            RECUSA_DO_CNPJ, status_coluna_d=getattr(recusa, "status_coluna_d", None)
        )
    except AntiBotEsgotado:
        return ResultadoDaRepresentacao(ANTI_BOT_ESGOTADO)
    except RepresentacaoNaoConfirmada:
        return ResultadoDaRepresentacao(NAO_CONFIRMADO)

    return ResultadoDaRepresentacao(REPRESENTADO)
