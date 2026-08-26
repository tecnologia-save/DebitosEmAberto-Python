"""Navegação de browser compartilhada pelas integrações que falam com o portal.

Estes dois helpers viviam em `main.py` e eram INJETADOS nas integrações — o que
obrigava o chamador a ensinar a integração a navegar. Eram
TRANSITIONAL_NAVIGATION_CALLBACK; aqui deixam de ser.

Diagnóstico
-----------
No legado os dois imprimiam. O que era impresso se divide em duas coisas bem
diferentes:

- `navegar` imprimia URL de origem, URL de destino, URL atual e título da página
  em caso de falha. Isso é SENSITIVE_OUTPUT — uma URL de sessão autenticada
  carrega identificadores — e além disso a falha JÁ VIAJA: `navegar` repropaga a
  exceção. Nada se perde ao não imprimir.

- `aguardar_rede` engolia o timeout e seguia. Essa informação NÃO viajava por
  lugar nenhum, e é um ESSENTIAL_OPERATIONAL_EVENT: "a página não estabilizou e
  seguimos assim mesmo" explica extrações incompletas. Por isso, e só por isso,
  existe um aviso estruturado.

Os avisos são CONSTANTES de um conjunto fechado. Nenhum carrega URL, título,
CNPJ, empresa ou valor.
"""
from __future__ import annotations

from patchright.sync_api import Error as ErroDoNavegador

REDE_NAO_ESTABILIZOU = "rede não estabilizou dentro do tempo"
PAGINACAO_NAO_ALTERADA = "não foi possível alterar os itens por página"

TIMEOUT_NAVEGACAO_MS = 60_000
TIMEOUT_REDE_MS = 60_000


def navegar(page, url: str, timeout: int = TIMEOUT_NAVEGACAO_MS) -> None:
    """Navega para `url`. Levanta se não conseguir.

    Usa `domcontentloaded` em vez de `networkidle`: portais Angular mantêm
    conexões abertas e nunca atingem networkidle dentro do timeout, causando
    erro mesmo com a página pronta. O conteúdo real é verificado pelos
    `wait_for` das etapas seguintes.

    A falha sobe como veio — quem chama decide o que fazer, e não há mensagem
    nossa para carregar URL nenhuma.
    """
    page.goto(url, wait_until="domcontentloaded", timeout=timeout)


def aguardar_rede(page, timeout: int = TIMEOUT_REDE_MS) -> str | None:
    """Espera a rede estabilizar. Best-effort: NÃO levanta.

    SPAs Angular podem manter conexões abertas indefinidamente, e o elemento-alvo
    é verificado pelo `wait_for` da etapa seguinte. Preservado do original.

    Devolve `REDE_NAO_ESTABILIZOU` quando desistiu, para que quem chama possa
    reportar — no legado essa informação virava um print e morria ali.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except ErroDoNavegador:
        return REDE_NAO_ESTABILIZOU
    return None
