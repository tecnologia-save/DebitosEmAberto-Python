"""Consulta fiscal: sessão já representando o contribuinte → dados.

Este módulo LÊ o portal. Ele não grava nada, não conhece planilha, coluna, aba
nem índice — e é essa ausência que prova o corte da fatia 8B1.

O corte
-------
No legado, a gravação eram as duas últimas linhas de cada extração, depois de
toda a paginação. Nada da planilha era lido durante a navegação: os `skip` já
chegavam prontos. Extrair e persistir nunca estiveram entrelaçados; estavam
apenas na mesma função.

Três operações, e não uma
-------------------------
`ler_situacao`, `consultar_dctfweb` e `consultar_processos` são separadas de
propósito. O RESUMABILITY_CONTRACT da fatia 4 depende de a coluna D poder ser
gravada assim que o DCTFWeb termina, ANTES de os Processos começarem: se os
Processos caem, o DCTFWeb não é refeito. Uma operação monolítica que devolvesse
tudo no fim destruiria isso.

Pré-condição
------------
A sessão já está representando o contribuinte correto e está na página de
pendências. Este módulo não autentica, não representa, não garante policy e não
é dono da sessão.

Diagnóstico
-----------
Nenhuma função daqui imprime. Quem chama decide o que registrar — os dados
fiscais são o RESULTADO, e resultado não é log.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# O que o portal diz no span de status.
SEM_PENDENCIA = "sem pendência"
COM_PENDENCIA = "com pendência"
NAO_RECONHECIDA = "situação não reconhecida"

# Textos exatos que o portal exibe. A comparação é por IGUALDADE, só com strip —
# preservado do original. PORTAL_STATUS_POSSIBLE_DEFECT: caixa e acento mudam o
# desfecho, ao contrário da regra de recusa da fatia 2, que normaliza.
_TEXTO_SEM_PENDENCIA = "Sem pendência"
_TEXTO_COM_PENDENCIA = "Com pendência"

XPATH_STATUS = (
    "xpath=/html/body/app-root/mf-portal-layout/portal-main-layout/div/main/"
    "ng-component/app-consultar-dividas-pendencias/div[1]/app-resultado-analise-fiscal/"
    "div/div[2]/span"
)
BOTAO_DCTFWEB = 'button[aria-label*="DCTFWeb"]'
BOTAO_PROCESSO = 'button[aria-label*="processo fiscal"]'
BOTAO_PROXIMA_PAGINA = 'button[aria-label="Página seguinte"]'
CARD_PROCESSO = 'button[aria-label="Expandir informações complementares do processo fiscal"]'

_JS_BOTOES = """
() => ({
    dctfweb:  !!document.querySelector('button[aria-label*="DCTFWeb"]'),
    processo: !!document.querySelector(
        'button[aria-label*="processo fiscal" i]'
    )
})
"""


@dataclass(frozen=True)
class SituacaoFiscal:
    """O que a página de pendências informa, e o que dá para fazer a partir dela.

    `tem_dctfweb` e `tem_processo` só são consultados quando há pendência — sem
    ela não existem botões de ação.
    """

    situacao: str
    tem_dctfweb: bool = False
    tem_processo: bool = False

    @property
    def com_pendencia(self) -> bool:
        return self.situacao == COM_PENDENCIA

    @property
    def reconhecida(self) -> bool:
        """False quando o portal exibiu um texto que esta regra não entende.

        FISCAL_UNKNOWN_SEMANTICS: isto NÃO é um estado que o portal informe. É o
        `else` de qualquer texto inesperado — inclusive um texto novo que a
        Receita venha a adotar. No legado isso resulta em nada gravado e a linha
        voltando pendente para sempre.
        """
        return self.situacao != NAO_RECONHECIDA


@dataclass(frozen=True)
class ExtracaoFiscal:
    """As linhas lidas de uma das duas consultas.

    São dicionários com as chaves que a integração de planilha já consome —
    preservado do original. Um modelo por linha não reduziria acoplamento algum
    hoje: quem lê os campos é a planilha, e ela já os conhece pelo nome.
    """

    linhas: tuple[dict, ...] = field(default_factory=tuple)
    paginas: int = 1

    def __len__(self) -> int:
        return len(self.linhas)


# ── Leitura da situação ───────────────────────────────────────────────────────

def ler_situacao(sessao, esperar_visivel_ms: int = 30_000) -> SituacaoFiscal:
    """Lê o status de pendências e quais ações o portal oferece."""
    pagina = sessao.pagina
    span = pagina.locator(XPATH_STATUS).first
    span.wait_for(state="visible", timeout=esperar_visivel_ms)
    texto = (span.text_content() or "").strip()

    if texto == _TEXTO_SEM_PENDENCIA:
        return SituacaoFiscal(SEM_PENDENCIA)
    if texto != _TEXTO_COM_PENDENCIA:
        return SituacaoFiscal(NAO_RECONHECIDA)

    # Detecta os botões via JS — retorna na hora, sem pagar timeout de espera.
    botoes = pagina.evaluate(_JS_BOTOES)
    return SituacaoFiscal(
        COM_PENDENCIA,
        tem_dctfweb=bool(botoes["dctfweb"]),
        tem_processo=bool(botoes["processo"]),
    )


# ── DCTFWeb ───────────────────────────────────────────────────────────────────

def consultar_dctfweb(sessao, cnpj: str, extrair_pagina, aguardar_rede,
                      selecionar_por_pagina, expandir_linhas) -> ExtracaoFiscal:
    """Abre a dívida DCTFWeb e extrai todas as páginas da tabela.

    Os quatro helpers de navegação entram por parâmetro porque são o que precisa
    de navegador de verdade — e é isso que torna a paginação testável.
    """
    pagina = sessao.pagina
    pagina.locator(BOTAO_DCTFWEB).first.click()

    aguardar_rede(pagina, label="DCTFWeb")
    pagina.wait_for_timeout(1_000)
    selecionar_por_pagina(pagina, 50)

    linhas: list[dict] = []
    numero = 1

    while True:
        pagina.wait_for_timeout(1_500)
        expandir_linhas(pagina)
        linhas.extend(extrair_pagina(pagina, cnpj))

        if not _ir_para_proxima(pagina, aguardar_rede, "DCTFWeb pág."):
            break
        numero += 1

    return ExtracaoFiscal(tuple(linhas), paginas=numero)


# ── Processos Fiscais ─────────────────────────────────────────────────────────

def consultar_processos(sessao, cnpj: str, extrair_card, aguardar_rede,
                        selecionar_por_pagina, ir_para_analise) -> ExtracaoFiscal:
    """Abre os processos fiscais e percorre todos os cards de todas as páginas."""
    pagina = sessao.pagina
    ir_para_analise(pagina)
    pagina.wait_for_timeout(1_000)

    botao = pagina.locator(BOTAO_PROCESSO).first
    botao.wait_for(state="visible", timeout=10_000)
    botao.click()

    aguardar_rede(pagina, label="proc.fiscal")
    pagina.wait_for_timeout(1_000)
    selecionar_por_pagina(pagina, 20)

    linhas: list[dict] = []
    numero = 1

    while True:
        pagina.wait_for_timeout(1_500)
        total = pagina.locator(CARD_PROCESSO).count()

        for i in range(total):
            # Rebusca o botão a cada iteração — evita stale reference.
            card = pagina.locator(CARD_PROCESSO).nth(i)
            card.scroll_into_view_if_needed()
            card.click()

            linhas.extend(extrair_card(pagina, cnpj))

            pagina.go_back()
            aguardar_rede(pagina, label="voltar")
            pagina.wait_for_timeout(1_000)

        if not _ir_para_proxima(pagina, aguardar_rede, "cards pág."):
            break
        numero += 1

    return ExtracaoFiscal(tuple(linhas), paginas=numero)


def _ir_para_proxima(pagina, aguardar_rede, rotulo: str) -> bool:
    """True se havia próxima página e ela foi aberta.

    O `except` largo do original está preservado: o botão pode nem existir, e a
    ausência dele é justamente o sinal de fim. Ver NAVIGATION_POSSIBLE_DEFECT.
    """
    try:
        botao = pagina.locator(BOTAO_PROXIMA_PAGINA).first
        if botao.is_disabled():
            return False
        botao.click()
    except Exception:  # noqa: BLE001 — preservado: ausência do botão = fim da paginação
        return False

    aguardar_rede(pagina, label=rotulo)
    return True
