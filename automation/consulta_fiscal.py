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

from patchright.sync_api import Error as ErroDoNavegador

from . import navegador
from .navegador import PAGINACAO_NAO_ALTERADA, aguardar_rede, navegar

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
URL_ANALISE_PENDENCIAS = (
    "https://servicos.receitafederal.gov.br/servico/pendencias/#/analise-pendencias"
)
CARD_PROCESSO = 'button[aria-label="Expandir informações complementares do processo fiscal"]'

_JS_PROCESSO_CREDITO = (
    "() => {"
    "  const d = document.querySelector('div.processo-credito');"
    "  return d ? d.textContent.trim() : '';"
    "}"
)

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
    avisos: tuple[str, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return len(self.linhas)


def _anotar(avisos: list, aviso: str | None) -> None:
    """Guarda o aviso, sem repetir: o operador precisa saber que aconteceu, e
    não quantas vezes."""
    if aviso is not None and aviso not in avisos:
        avisos.append(aviso)



# ── Leitura da situação ───────────────────────────────────────────────────────

def ler_situacao(sessao, esperar_visivel_ms: int = 30_000) -> SituacaoFiscal:
    """Lê o status de pendências e quais ações o portal oferece.

    Falha do navegador atravessa como `FalhaDoNavegador`: a aplicação decide
    sobre a sessão sem conhecer o tipo do fornecedor.
    """
    with navegador.falhas_traduzidas():
        return _ler_situacao(sessao, esperar_visivel_ms)


def _ler_situacao(sessao, esperar_visivel_ms: int = 30_000) -> SituacaoFiscal:
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


# ── Leitores do conteúdo do portal ──────────────────────────────────────────

def selecionar_itens_por_pagina(page, n: int) -> str | None:
    """Seleciona N itens por página no ng-select de paginação (div.pagination-per-page).

    O ng-dropdown-panel é renderizado fora do container (appendTo body), por isso a
    opção é buscada diretamente pela span.ng-option-label com texto exato.
    """
    try:
        ng_sel = page.locator('div.pagination-per-page ng-select').first
        ng_sel.wait_for(state="visible", timeout=10_000)
        ng_sel.click()
        page.wait_for_timeout(600)

        opcao = page.locator(f'span.ng-option-label:text-is("{n}")').first
        opcao.wait_for(state="visible", timeout=5_000)
        opcao.click()
        page.wait_for_timeout(1_500)
    except ErroDoNavegador:
        # Best-effort, preservado: não conseguir mudar a paginação não interrompe
        # a extração — só muda quantas páginas serão percorridas. Mas o fato
        # precisa chegar a alguém: no legado virava um print e morria ali.
        return PAGINACAO_NAO_ALTERADA
    return None


def expandir_linhas(page) -> None:
    """Expande todas as linhas da tabela de uma só vez via JavaScript.

    Um único evaluate clica todos os botões chevron-down simultaneamente,
    eliminando os N roundtrips Playwright + N vezes 350 ms da abordagem anterior.
    Aguarda 1 s para o Angular processar todas as mudanças de estado.
    """
    contagem = page.evaluate(
        """
        () => {
            const btns = Array.from(
                document.querySelectorAll('button.br-button.circle.small')
            ).filter(b => b.querySelector('i.fa-chevron-down'));
            btns.forEach(b => b.click());
            return btns.length;
        }
        """
    )
    if contagem:
        page.wait_for_timeout(1_000)   # aguarda Angular renderizar tudo


def _linhas_da_tabela_dctfweb(page, cnpj: str) -> list[dict]:
    """Extrai as linhas de dados visíveis na tabela DCTFWeb (JavaScript evaluate)."""
    return page.evaluate(
        """
        (cnpj) => {
            const resultado = [];

            /* Linhas principais: <tr> que contêm o botão de expandir */
            const linhas = document.querySelectorAll(
                'tbody tr:has(button.br-button.circle.small)'
            );

            for (const tr of linhas) {
                const tds = Array.from(tr.querySelectorAll('td'));

                /* Receita: única <td class="text-nowrap"> */
                const tdReceita = tds.find(td => td.classList.contains('text-nowrap'));
                const receita   = tdReceita?.textContent?.trim() ?? '';

                /* Saldo devedor consolidado: última <td class="text-right"> */
                const tdsSaldoDir = tds.filter(td => td.classList.contains('text-right'));
                const saldo = tdsSaldoDir[tdsSaldoDir.length - 1]
                                ?.textContent?.trim() ?? '';

                /* Tipo (Situação do débito): <td> que contém <span class="text-nowrap"> */
                const tdTipo = tds.find(td => td.querySelector('span.text-nowrap'));
                const tipo   = tdTipo?.querySelector('span.text-nowrap')
                                      ?.textContent?.trim() ?? '';

                /* <td> sem classe especial, sem botão e sem input (checkbox)
                   Ordem esperada no DOM: PA/Ex., Dt.Vcto., Saldo devedor (R$) */
                const tdsSimples = tds.filter(td =>
                    !td.classList.contains('text-nowrap') &&
                    !td.classList.contains('text-right') &&
                    !td.querySelector('span.text-nowrap') &&
                    !td.querySelector('button.br-button') &&
                    !td.querySelector('input')
                );
                const pa_ex   = tdsSimples[0]?.textContent?.trim() ?? '';
                const dt_vcto = tdsSimples[1]?.textContent?.trim() ?? '';

                /* Detalhe expandido: próxima <tr> irmã, revelada ao clicar na seta */
                let tributo       = '';
                let valor_original = '';
                const proxTr = tr.nextElementSibling;
                if (proxTr && proxTr.tagName === 'TR') {
                    const labels = proxTr.querySelectorAll('p.label');
                    for (const labelEl of labels) {
                        const labelText = labelEl.textContent.trim();
                        const divPai    = labelEl.closest('div');
                        const ps        = divPai
                            ? Array.from(divPai.querySelectorAll('p'))
                            : [];
                        /* Valor é o primeiro <p> que não tem class="label" */
                        const valorEl = ps.find(p => !p.classList.contains('label'));
                        const valor   = valorEl?.textContent?.trim() ?? '';
                        if (labelText === 'Tributo')              tributo        = valor;
                        if (labelText === 'Valor original (R$)')  valor_original = valor;
                    }
                }

                resultado.push({
                    cnpj, tipo, tributo, receita,
                    pa_ex, dt_vcto, valor_original, saldo,
                });
            }

            return resultado;
        }
        """,
        cnpj,
    )



def _linhas_da_tabela_do_card(page, cnpj: str,
                                   processo_credito: str) -> list[dict]:
    """Extrai linhas da tabela de débitos de um card de processo fiscal."""
    return page.evaluate(
        """
        ([cnpj, processo_credito]) => {
            const resultado = [];
            const linhas = document.querySelectorAll(
                'tbody tr:has(button.br-button.circle.small)'
            );

            for (const tr of linhas) {
                const tds = Array.from(tr.querySelectorAll('td'));

                /* Receita: td.text-nowrap */
                const tdReceita = tds.find(td => td.classList.contains('text-nowrap'));
                const receita   = tdReceita?.textContent?.trim() ?? '';

                /* Saldo devedor: td.text-right */
                const tdSaldo = tds.find(td => td.classList.contains('text-right'));
                const saldo   = tdSaldo?.textContent?.trim() ?? '';

                /* Tipo: td contendo span.text-nowrap */
                const tdTipo = tds.find(td => td.querySelector('span.text-nowrap'));
                const tipo   = tdTipo?.querySelector('span.text-nowrap')
                                      ?.textContent?.trim() ?? '';

                /* TDs simples (sem classe, sem botão, sem input) → PA/Ex., Dt.Vcto. */
                const tdsSimples = tds.filter(td =>
                    !td.classList.contains('text-nowrap') &&
                    !td.classList.contains('text-right') &&
                    !td.querySelector('span.text-nowrap') &&
                    !td.querySelector('button.br-button') &&
                    !td.querySelector('input')
                );
                const pa_ex   = tdsSimples[0]?.textContent?.trim() ?? '';
                const dt_vcto = tdsSimples[1]?.textContent?.trim() ?? '';

                /* Valor original: seção expandida (próxima <tr> irmã) */
                let valor_original = '';
                const proxTr = tr.nextElementSibling;
                if (proxTr && proxTr.tagName === 'TR') {
                    const labels = proxTr.querySelectorAll('p.label');
                    for (const labelEl of labels) {
                        const labelText = labelEl.textContent.trim();
                        const divPai    = labelEl.closest('div');
                        const ps        = divPai
                            ? Array.from(divPai.querySelectorAll('p'))
                            : [];
                        const valorEl = ps.find(p => !p.classList.contains('label'));
                        const valor   = valorEl?.textContent?.trim() ?? '';
                        if (labelText === 'Valor original (R$)') valor_original = valor;
                    }
                }

                resultado.push({
                    cnpj, tipo, receita, pa_ex, dt_vcto,
                    valor_original, saldo, processo_credito,
                });
            }

            return resultado;
        }
        """,
        [cnpj, processo_credito],
    )



def _linhas_do_card(page, cnpj: str, avisos: list) -> list[dict]:
    """Extrai dados de um card de processo fiscal já aberto (nova página).

    Retorna lista de dicts com as linhas da tabela de débitos do card.
    """
    _anotar(avisos, aguardar_rede(page))
    page.wait_for_timeout(1_000)

    # ── "Processo de crédito" (expande se existir) ────────────────────────────
    processo_credito = ""
    btn_cred = page.locator(
        'button[aria-label="Expandir processo de crédito"]'
    ).first
    try:
        if btn_cred.is_visible(timeout=3_000):
            btn_cred.click()
            page.wait_for_timeout(800)
            # Após o clique o Angular muda o aria-label do botão (Expandir → Recolher),
            # por isso NÃO buscamos o botão novamente no JS.
            # Buscamos diretamente o div revelado: div.processo-credito
            try:
                div_proc = page.locator('div.processo-credito').first
                div_proc.wait_for(state="visible", timeout=3_000)
                processo_credito = (div_proc.text_content() or "").strip()
            except ErroDoNavegador:
                # Fallback via JS — cobre o caso de o div já existir mas sem estado "visible"
                processo_credito = page.evaluate(_JS_PROCESSO_CREDITO)
    except ErroDoNavegador:
        # O botão pode simplesmente não existir: card sem processo de crédito é
        # estado normal do portal. Estreitado do `except Exception` original —
        # um bug nosso aqui produzia `processo_credito` vazio em silêncio.
        pass

    # ── Tabela de débitos do card ─────────────────────────────────────────────
    _anotar(avisos, selecionar_itens_por_pagina(page, 50))

    todos_dados: list[dict] = []
    pagina = 1

    while True:
        page.wait_for_timeout(1_500)
        expandir_linhas(page)

        todos_dados.extend(_linhas_da_tabela_do_card(page, cnpj, processo_credito))

        if not _ir_para_proxima(page, avisos):
            break
        pagina += 1

    return todos_dados


# ── DCTFWeb ───────────────────────────────────────────────────────────────────

def consultar_dctfweb(sessao, cnpj: str) -> ExtracaoFiscal:
    """Consulta a dívida DCTFWeb. Falha do navegador vira `FalhaDoNavegador`."""
    with navegador.falhas_traduzidas():
        return _consultar_dctfweb(sessao, cnpj)


def _consultar_dctfweb(sessao, cnpj: str) -> ExtracaoFiscal:
    """Abre a dívida DCTFWeb e extrai todas as páginas da tabela.

    Nenhum parâmetro externo: a navegação é detalhe desta integração, e não
    algo que o chamador precise fornecer.
    """
    pagina = sessao.pagina
    pagina.locator(BOTAO_DCTFWEB).first.click()

    avisos: list[str] = []
    _anotar(avisos, aguardar_rede(pagina))
    pagina.wait_for_timeout(1_000)
    _anotar(avisos, selecionar_itens_por_pagina(pagina, 50))

    linhas: list[dict] = []
    numero = 1

    while True:
        pagina.wait_for_timeout(1_500)
        expandir_linhas(pagina)
        linhas.extend(_linhas_da_tabela_dctfweb(pagina, cnpj))

        if not _ir_para_proxima(pagina, avisos):
            break
        numero += 1

    return ExtracaoFiscal(tuple(linhas), paginas=numero, avisos=tuple(avisos))


# ── Processos Fiscais ─────────────────────────────────────────────────────────

def consultar_processos(sessao, cnpj: str) -> ExtracaoFiscal:
    """Consulta os Processos Fiscais. Mesma tradução do DCTFWeb."""
    with navegador.falhas_traduzidas():
        return _consultar_processos(sessao, cnpj)


def _consultar_processos(sessao, cnpj: str) -> ExtracaoFiscal:
    """Abre os processos fiscais e percorre todos os cards de todas as páginas."""
    pagina = sessao.pagina
    avisos: list[str] = []
    navegar(pagina, URL_ANALISE_PENDENCIAS)
    pagina.wait_for_timeout(1_000)

    botao = pagina.locator(BOTAO_PROCESSO).first
    botao.wait_for(state="visible", timeout=10_000)
    botao.click()

    _anotar(avisos, aguardar_rede(pagina))
    pagina.wait_for_timeout(1_000)
    _anotar(avisos, selecionar_itens_por_pagina(pagina, 20))

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

            linhas.extend(_linhas_do_card(pagina, cnpj, avisos))

            pagina.go_back()
            _anotar(avisos, aguardar_rede(pagina))
            pagina.wait_for_timeout(1_000)

        if not _ir_para_proxima(pagina, avisos):
            break
        numero += 1

    return ExtracaoFiscal(tuple(linhas), paginas=numero, avisos=tuple(avisos))


def _ir_para_proxima(pagina, avisos: list) -> bool:
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

    _anotar(avisos, aguardar_rede(pagina))
    return True
