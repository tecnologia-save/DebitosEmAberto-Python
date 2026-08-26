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

import re
import time
from dataclasses import dataclass

from patchright.sync_api import Error as ErroDoNavegador

from . import captcha, navegador, status_portal

URL_SERVICOS_RF = "https://servicos.receitafederal.gov.br/"
URL_PENDENCIAS = "https://servicos.receitafederal.gov.br/servico/pendencias/"

# MIGRATION_RUNTIME_STATE — global de modulo trazido do legado, preservado como
# estava para nao mudar a semantica temporal. STATEFUL_INTEGRATION_RATE_LIMIT: o
# relogio comeca no CLIQUE em "Representar", e so esta integracao sabe esse
# instante. Se o lifecycle real pedir outro dono, isso se decide depois da 9B.
_ultimo_troca_cnpj: float = 0.0
_INTERVALO_TROCA = 30   # segundos


def _fechar_tutorial(page) -> None:
    """Rede de seguranca: o modal do tutorial cobre a tela e intercepta o clique
    no avatar. Timeout 0 para nao pagar espera a cada CNPJ — quem espera pelo
    tutorial e o login, uma vez por sessao.

    Import tardio: `servicos_rf_login` e o fork, e so este caminho precisa dele.
    """
    from servicos_rf_login.login import fechar_tutorial_pos_login

    fechar_tutorial_pos_login(page, timeout_ms=0)


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


def _aguardar_intervalo_troca() -> None:
    """Aguarda o intervalo mínimo de 30s entre trocas de CNPJ no portal.

    Chamado sempre antes de clicar em 'Representar'. Se o intervalo já passou,
    retorna imediatamente sem bloqueio.
    """
    global _ultimo_troca_cnpj
    if _ultimo_troca_cnpj == 0.0:
        return
    decorrido = time.time() - _ultimo_troca_cnpj
    if decorrido < _INTERVALO_TROCA:
        espera = _INTERVALO_TROCA - decorrido
        time.sleep(espera)


def _executar_representacao(page, cnpj: str,
                            config_captcha) -> ResultadoDaRepresentacao:
    """Aguarda o intervalo de 30s, depois representa o CNPJ como Procurador
    no portal e navega para a página de pendências.

    Pode ser chamado tanto para o primeiro CNPJ (logo após entrar no portal)
    quanto para os seguintes (sem precisar voltar ao eCAC).
    """
    global _ultimo_troca_cnpj

    # ── Garante intervalo mínimo de 30s entre trocas ──────────────────────────
    _aguardar_intervalo_troca()

    _MAX_TENTATIVAS_REPR = 3

    for _tentativa_repr in range(1, _MAX_TENTATIVAS_REPR + 1):

        if _tentativa_repr > 1:
            pass
            # Fecha dropdown se ainda estiver aberto
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            except ErroDoNavegador:
                pass
            page.wait_for_timeout(2_000)

        # ── Tutorial pós-login ────────────────────────────────────────────────
        # Rede de segurança: se estiver aberto, cobre a tela e intercepta o
        # clique no avatar. Timeout 0 para não pagar espera a cada CNPJ — quem
        # espera pelo tutorial é fazer_login(), uma vez por sessão.
        _fechar_tutorial(page)

        # ── Abre menu do avatar ───────────────────────────────────────────────
        avatar = page.locator('xpath=//*[@id="avatar-dropdown-trigger"]').first
        avatar.wait_for(state="visible", timeout=15_000)
        avatar.click()
        page.wait_for_timeout(600)

        # ── Preenche CNPJ ─────────────────────────────────────────────────────
        campo_cnpj = page.locator('xpath=//*[@id="input-representar-cpfcnpj"]').first
        campo_cnpj.wait_for(state="visible", timeout=10_000)
        campo_cnpj.fill(cnpj)
        page.wait_for_timeout(400)

        # ── Seleciona Procurador ──────────────────────────────────────────────
        ng_select = page.locator(
            'xpath=//*[@id="formularioRepresentacao"]/form/div/div[2]/br-select/div/div/div[1]/ng-select'
        ).first
        ng_select.wait_for(state="visible", timeout=10_000)
        ng_select.click()
        page.wait_for_timeout(400)

        opcao = page.get_by_role("option", name="Procurador").first
        opcao.wait_for(state="visible", timeout=5_000)
        opcao.click()
        page.wait_for_timeout(400)

        # ── Clica Representar ─────────────────────────────────────────────────
        btn_representar = page.locator(
            'xpath=//*[@id="formularioRepresentacao"]/form/div/button'
        ).first
        btn_representar.wait_for(state="visible", timeout=10_000)

        # Listener de popup configurado ANTES do clique
        _popups: list = []

        # `_popups` e recriado a cada tentativa e o listener so vive enquanto ela
        # dura, entao nao ha o vazamento de variavel de laco que o B023 procura.
        def _on_new_page(p):
            _popups.append(p)  # noqa: B023

        page.context.on("page", _on_new_page)
        btn_representar.click()

        # Inicia cronômetro imediatamente após clicar em Representar
        _ultimo_troca_cnpj = time.time()

        # ── Aguarda "Carregando" APARECER antes de checar captcha ────────────
        # O captcha pode aparecer POR CIMA do spinner "Carregando".
        # Basta aguardar o spinner aparecer para saber que o servidor recebeu
        # o clique; não esperamos ele sumir — isso ocorre após resolver captcha.
        _carregando = page.locator(
            'xpath=/html/body/app-root/mf-portal-layout/portal-main-layout'
            '/br-loading/div/div/a/div[2]'
        ).first
        _carregando_apareceu = False
        try:
            _carregando.wait_for(state="visible", timeout=8_000)
            _carregando_apareceu = True
        except ErroDoNavegador:
            pass  # spinner não apareceu (resposta muito rápida) — segue

        # ── Espera ativa (até 25 s): erro / popup / captcha / confirmação ─────
        # Prioridade de verificação a cada ~800 ms:
        #   0. Erro "acesso automatizado" (span.mensagemErro) → retentar
        #   1. Popup (nova janela) → captcha em popup
        #   2. iframes hcaptcha VISIVEIS (challenge / checkbox) → captcha inline
        #   3. CNPJ mudou no cabeçalho → representação sem captcha
        _LIMITE_ESPERA_S  = 60
        _deadline_captcha = time.time() + _LIMITE_ESPERA_S
        captcha_tipo      = None    # "popup" | "inline" | None
        _srcs_logados     = False
        _erro_bloqueado   = False

        while time.time() < _deadline_captcha:

            # 0. Mensagem de erro do portal (anti-bot ou falha permanente)
            _err_msg = page.evaluate(
                "() => { const e = document.querySelector('span.mensagemErro'); "
                "return e ? e.textContent.trim() : ''; }"
            )
            if _err_msg:
                _classe = status_portal.classificar_mensagem(_err_msg)
                if _classe == status_portal.ANTIBOT:
                    pass
                    _erro_bloqueado = True
                    break
                if _classe == status_portal.RECUSA_DO_CNPJ:
                    return ResultadoDaRepresentacao(
                        RECUSA_DO_CNPJ,
                        status_coluna_d=status_portal.status_da_recusa(_err_msg),
                    )

            # 1. Popup nova janela
            if _popups:
                captcha_tipo = "popup"
                break

            # 2. Captcha challenge ATIVO — verificado por múltiplos seletores internos
            # ─────────────────────────────────────────────────────────────────────
            # Iframes frame=challenge ficam pré-carregados no DOM mesmo sem captcha
            # ativo. Para evitar falsos positivos, executamos JavaScript DENTRO do
            # iframe (cross-origin acessível pelo Playwright via frame.evaluate) e
            # exigimos que TODOS os critérios abaixo sejam satisfeitos ao mesmo tempo:
            #
            #   1. .challenge-container   → existe E tem dimensões ≥ 100x100 px
            #   2. .prompt-text           → existe E tem texto não-vazio
            #                               (ex: "Toque em todos os seres vivos")
            #   3. .task-grid             → existe (grade de imagens do desafio)
            #   4. .button-submit         → existe E aria-disabled ≠ "true"
            #
            # Se qualquer critério falhar → captcha não está ativo.
            _hc_frames = [
                f for f in page.frames
                if "hcaptcha.com" in (f.url or "")
                and "frame=challenge" in (f.url or "")
            ]

            # Diagnóstico: loga srcs uma vez quando frames aparecerem no DOM
            if _hc_frames and not _srcs_logados:
                _srcs_logados = True
                for _hf in _hc_frames[:3]:
                    pass

            _captcha_texto = None   # instrução do desafio, se ativo
            for _hf in _hc_frames:
                try:
                    _captcha_texto = _hf.evaluate("""() => {
                        // 1. challenge-container com dimensões reais
                        const container = document.querySelector('.challenge-container');
                        if (!container) return null;
                        const r = container.getBoundingClientRect();
                        if (r.width < 100 || r.height < 100) return null;

                        // 2. prompt-text com instrução preenchida
                        const prompt = document.querySelector('.prompt-text');
                        if (!prompt || !prompt.textContent.trim()) return null;

                        // 3. botão submit habilitado
                        // Nota: NÃO verificamos .task-grid pois só existe no tipo grade 3x3.
                        //       O tipo "imagem única" não tem .task-grid mas é igualmente válido.
                        const btn = document.querySelector('.button-submit');
                        if (!btn || btn.getAttribute('aria-disabled') === 'true') return null;

                        return prompt.textContent.trim();
                    }""")
                    if _captcha_texto:
                        break
                except ErroDoNavegador:
                    pass

            if _captcha_texto:
                pass
                captcha_tipo = "inline"
                break

            # 3. CNPJ já mudou no cabeçalho? (botão avatar, sempre visível)
            _cnpj_pag = page.evaluate(
                "() => {"
                "  const h = document.querySelector('.ni-pessoa:not(.ni-representante)');"
                "  if (h) return h.textContent.trim();"
                "  const r = document.querySelector('.ni-representacao');"
                "  return r ? r.textContent.trim() : '';"
                "}"
            )
            if re.sub(r"\D", "", _cnpj_pag).zfill(14) == cnpj:
                pass
                break

            # 4. "Carregando" sumiu = servidor respondeu sem captcha aparecer
            if _carregando_apareceu and not _carregando.is_visible():
                pass
                break

            page.wait_for_timeout(800)

        try:
            page.context.remove_listener("page", _on_new_page)
        except ErroDoNavegador:
            pass

        # ── Erro anti-bot → retentar ou desistir ─────────────────────────────
        if _erro_bloqueado:
            if _tentativa_repr < _MAX_TENTATIVAS_REPR:
                continue  # abre menu, preenche, clica de novo
            return ResultadoDaRepresentacao(ANTI_BOT_ESGOTADO)

        # ── Resolve captcha conforme tipo ─────────────────────────────────────
        _captcha_repr_ok = True   # False se 2 tentativas falharem → retry repr
        if captcha_tipo in ("popup", "inline"):
            _alvo = _popups[0] if captcha_tipo == "popup" else page
            if captcha_tipo == "popup":
                pass

            _desfecho = captcha.resolver(_alvo, config_captcha, tentativas=2,
                                        aguardar=lambda: page.wait_for_timeout(2_000))
            if _desfecho == captcha.RESOLVIDO_OU_AUSENTE:
                pass
            else:
                pass
                _captcha_repr_ok = False

            if captcha_tipo == "popup":
                try:
                    _popups[0].wait_for_close(timeout=15_000)
                except ErroDoNavegador:
                    # A popup pode ja ter fechado sozinha; nao ha o que fazer.
                    pass

        # Captcha não resolvido em 2 tentativas → reinicia o fluxo de representação
        if not _captcha_repr_ok:
            pass
            if _tentativa_repr < _MAX_TENTATIVAS_REPR:
                continue
            return ResultadoDaRepresentacao(NAO_CONFIRMADO)

        # ── Aguarda "Carregando" DESAPARECER após captcha ────────────────────
        # Quando captcha é resolvido, o "Carregando" ainda está visível por baixo.
        # Esperamos ele sumir para saber que o servidor processou a representação.
        # Se não houve captcha, o "Carregando" já sumiu no loop acima — esta espera
        # retorna imediatamente.
        if captcha_tipo:
            pass
        try:
            _carregando.wait_for(state="hidden", timeout=30_000)
            if captcha_tipo:
                pass
        except ErroDoNavegador:
            if captcha_tipo:
                # Página travou no spinner — recarrega e verifica a situação.
                # Se a representação já foi aceita pelo servidor, o CNPJ estará
                # confirmado no cabeçalho após o reload e a automação continua.
                # Se não, a verificação de CNPJ abaixo detecta e retenta.
                try:
                    page.reload(wait_until="domcontentloaded", timeout=60_000)
                    page.wait_for_timeout(1_500)
                except ErroDoNavegador:
                    pass

        page.wait_for_timeout(800)

        # ── Verifica erro anti-bot após captcha ───────────────────────────────
        # O portal pode exibir "acesso bloqueado" também DEPOIS de resolver o
        # captcha, quando o servidor processa a representação e rejeita o acesso.
        _err_pos_captcha = page.evaluate(
            "() => { const e = document.querySelector('span.mensagemErro'); "
            "return e ? e.textContent.trim() : ''; }"
        )
        if _err_pos_captcha:
            _classe_pos_captcha = status_portal.classificar_mensagem(_err_pos_captcha)
            if _classe_pos_captcha == status_portal.ANTIBOT:
                pass
                if _tentativa_repr < _MAX_TENTATIVAS_REPR:
                    continue
                return ResultadoDaRepresentacao(ANTI_BOT_ESGOTADO)
            if _classe_pos_captcha == status_portal.RECUSA_DO_CNPJ:
                return ResultadoDaRepresentacao(
                    RECUSA_DO_CNPJ,
                    status_coluna_d=status_portal.status_da_recusa(_err_pos_captcha),
                )

        # ── Verifica que o CNPJ representado realmente mudou ──────────────────
        _cnpj_confirmado = False
        _cnpj_pag_final  = ""
        for _ in range(10):
            _cnpj_pag_final = page.evaluate(
                "() => {"
                "  const h = document.querySelector('.ni-pessoa:not(.ni-representante)');"
                "  if (h) return h.textContent.trim();"
                "  const r = document.querySelector('.ni-representacao');"
                "  return r ? r.textContent.trim() : '';"
                "}"
            )
            if re.sub(r"\D", "", _cnpj_pag_final).zfill(14) == cnpj:
                _cnpj_confirmado = True
                break
            page.wait_for_timeout(500)

        if _cnpj_confirmado:
            break  # ← sucesso, sai do loop de retry

        # CNPJ não confirmado → retentar se ainda houver tentativas
        if _tentativa_repr < _MAX_TENTATIVAS_REPR:
            pass
            continue

        return ResultadoDaRepresentacao(NAO_CONFIRMADO)


    navegador.navegar(page, URL_PENDENCIAS)
    page.wait_for_timeout(500)
    return ResultadoDaRepresentacao(REPRESENTADO)


def recuperar_apos_recusa(page) -> bool:
    """Devolve o portal a um estado utilizável depois de uma recusa de representação.

    A recusa é do CNPJ, não da sessão: o certificado segue autenticado e o portal
    aberto. Fechar o navegador aqui obrigaria um login novo para o próximo CNPJ
    do mesmo certificado — justamente o custo que se quer evitar.

    Fecha o formulário de representação (que fica aberto exibindo o erro) e volta
    para o portal, confirmando que o avatar reaparece.

    Returns:
        True se a sessão continua utilizável para representar o próximo CNPJ.
    """
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except ErroDoNavegador:
        pass

    try:
        navegador.navegar(page, URL_SERVICOS_RF, timeout=30_000)
        page.locator('xpath=//*[@id="avatar-dropdown-trigger"]').first.wait_for(
            state="visible", timeout=15_000
        )
        return True
    except ErroDoNavegador:
        pass
        return False


def representar(sessao, cnpj: str, config_captcha) -> ResultadoDaRepresentacao:
    """Representa `cnpj` na sessão e devolve o desfecho.

    Esta é a capacidade inteira: navegação, captcha, classificação e retry
    interno. Não há callback de implementação, e nenhum desfecho esperado viaja
    como exception — os quatro saem por `ResultadoDaRepresentacao`.

    Falha externa conhecida do navegador sobe; bug nosso também.
    """
    return _executar_representacao(sessao.pagina, cnpj, config_captcha)
