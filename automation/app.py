"""A aplicacao: coordena as capacidades, e nao implementa nenhuma.

O que este modulo decide
------------------------
Quando abrir e fechar a planilha, quando trocar de certificado, quando relogar,
o que gravar depois de cada desfecho, quando retentar um CNPJ e quando desistir
dele. Nada mais.

O que ele NAO sabe
------------------
Como falar com o portal, como resolver um captcha, como e uma celula de Excel,
o que e um DataFrame, qual coluna guarda o status, onde fica o perfil do Chrome,
qual e a chave do Gemini, o que e uma janela de UAC.

RESUMABILITY_CONTRACT
---------------------
A ordem nao e estilo. Para cada CNPJ: o DCTFWeb grava o detalhe e SO ENTAO marca
a coluna; os Processos comecam depois disso; e o disco e tocado uma vez, no
`finally` da unidade CNPJ. Se os Processos caem, o DCTFWeb ja esta persistido e
a proxima tentativa — inclusive uma retentativa dentro desta mesma execucao —
pula o que ja terminou. Trocar isso por "salva tudo no fim" destroi a retomada.

Por isso a retomada e RELIDA a cada tentativa, em vez de viajar em
`ItemPendente`: o valor muda durante a propria execucao.

Observabilidade
---------------
Um unico seam: `emitir_evento`. O app produz FATOS estruturados
(`EventoOperacional`); quem os transforma em frase — e decide se vao para o
console, para um arquivo ou para lugar nenhum — e o adapter de apresentacao.

`emitir_evento=None` e valido e a execucao continua funcionalmente identica:
observabilidade nao e requisito de dominio. Mas nao ha `try/except` em volta do
emissor — um bug no adapter e um bug, e sobe.
"""
from __future__ import annotations

from collections.abc import Callable

from automation import (
    certificados,
    certificados_windows,
    consulta_fiscal,
    eventos,
    maquina,
    navegador,
    planilha,
    policy_certificado,
    representacao,
    status_portal,
)
from automation.boundary import EntradaDebitosEmAberto
from automation.captcha import ConfigCaptcha
from automation.certificados import ProvedorDeCertificados
from automation.eventos import EventoOperacional
from automation.planilha import SessaoPlanilha
from automation.policy_certificado import ConfiguracaoDeHostIncompativel

# Reexportado de proposito, e nao por conveniencia: `executar` pode terminar
# assim, e os adapters precisam nomear essa falha para transforma-la em codigo
# de saida. Importa-la de `policy_certificado` obrigaria runner e local a
# conhecer POR QUE o host esta impedido — policy, registro, certificado —, e
# esse e exatamente o conhecimento que um adapter nao pode ter. Aqui eles
# aprendem so o que precisam: a aplicacao recusou comecar.
__all__ = ["ConfiguracaoDeHostIncompativel", "executar"]

MAX_TENTATIVAS_POR_ITEM = 2

Emissor = Callable[[EventoOperacional], None] | None


class _RecusaDoPortal(Exception):
    """Sinal INTERNO do app: o portal recusou este CNPJ.

    Nao e desfecho viajando como exception — o desfecho ja foi lido de
    `ResultadoDaRepresentacao` e persistido antes deste ponto. Isto so desvia o
    controle para o `finally` que grava, e nunca cruza a fronteira publica.
    """


class _RepresentacaoNaoConcluida(Exception):
    """Sinal INTERNO do app: a representacao nao confirmou. Mesma natureza."""


class _Execucao:
    """O estado vivo de UMA execucao.

    Existe para que o estado de sessao e certificado deixe de ser variavel solta
    num laco de 140 linhas. Nao e container, nao e contexto, nao e framework: e o
    escopo da execucao, e morre com ela.
    """

    def __init__(self, sessao_planilha: SessaoPlanilha, caminho: str,
                 config_captcha: ConfigCaptcha, emitir: Emissor,
                 provedor: ProvedorDeCertificados | None = None,
                 diretorio_da_execucao: str | None = None) -> None:
        self.planilha = sessao_planilha
        self.caminho = caminho
        self.config_captcha = config_captcha
        self._emitir = emitir
        # De ONDE vem o certificado de cada linha. No desktop, do Certificate
        # Store; na plataforma sera outro provedor. A aplicacao nao sabe qual.
        #
        # O PADRAO e o desktop, e existe para a transicao: `main.py` e `local.py`
        # continuam construindo a execucao como sempre construiram. No dia em que
        # o runner da plataforma passar o provedor dele, o padrao some.
        self.provedor = provedor or certificados_windows.CertificadosDoWindows()
        # O CHAO desta execucao, opaco para a aplicacao: ela nao abre, nao
        # deriva caminho e nao sabe o que a fiacao guarda ali. `None` = nao ha
        # chao proprio, e a fiacao decide como sempre decidiu.
        self.diretorio_da_execucao = diretorio_da_execucao
        self.certificado_atual: str | None = None
        self.policy_confiavel = True
        # O guardiao da policy que ESTA execucao mandou escrever, ou None.
        # Uma policy que ja existia (JA_ATIVA) nao tem controle nosso: ela e
        # emprestada, e nao a removemos.
        self.controle_da_policy = None
        self.sessao = None            # login.SessaoReceita | None

    # ── O seam de eventos ─────────────────────────────────────────────────────

    def emitir(self, codigo: str, **campos) -> None:
        """Emite um fato. Sem emissor, nao faz nada — e so isso."""
        if self._emitir is None:
            return
        self._emitir(EventoOperacional(codigo, **campos))

    # ── Planilha ──────────────────────────────────────────────────────────────

    def salvar(self) -> None:
        """Grava o que estiver pendente. Uma falha NAO interrompe a execucao.

        A sessao continua suja de proposito: a proxima gravacao tenta de novo. E
        por isso que o evento sai AGORA e nao no fim — o operador que libera o
        arquivo no meio da execucao salva o progresso.
        """
        if not self.planilha.precisa_gravar():
            return
        try:
            self.planilha.gravar()
        except (OSError, ValueError, KeyError) as erro:
            self.emitir(eventos.SALVAMENTO_PLANILHA_FALHOU,
                        tipo_da_falha=type(erro).__name__)
            return
        self.planilha.marcar_gravado()
        # O arquivo esta consistente AGORA, e so agora. Quem publica progresso
        # para fora depende deste instante: publicar por relogio significaria
        # ler o arquivo no meio de uma gravacao, e um arquivo lido pela metade
        # nao levanta erro — ele sai corrompido e ninguem percebe.
        self.emitir(eventos.PLANILHA_GRAVADA)

    def liberar_policy(self) -> None:
        """Remove a policy do Chrome, se esta execucao a escreveu.

        POLICY_LIFETIME_EXCEEDS_APP_EXECUTION: o guardiao so limpava quando o
        PROCESSO principal morria, e um adapter reutilizavel nao morre. Desde a
        fatia 12B.2 o pedido e explicito, e quem remove e o proprio guardiao —
        elevado, e portanto capaz de mexer em HKLM.

        SO remove o que e nosso. Uma policy que ja estava escrita quando
        chegamos (JA_ATIVA) nao tem controle: ela e emprestada, e apaga-la seria
        repetir, do outro lado, o mesmo cleanup cego que produziu
        GLOBAL_CERT_POLICY_CONCURRENCY_RISK.

        PEDIR nao e CONFIRMAR: so devolve ao estado limpo quando a policy nao
        esta mais escrita. Enquanto isso nao acontecer, o controle fica, e o
        guardiao continua sendo o fallback de crash.
        """
        if self.controle_da_policy is None:
            return

        if (maquina.estado_do_guardiao(self.controle_da_policy)
                == policy_certificado.GUARDIAO_ENCERRADO):
            # ORPHANED_PERSISTENT_POLICY (fatia 13A.4). Nao ha a quem pedir: o
            # processo elevado terminou, e este aqui nao tem privilegio para
            # remover de HKLM. Pedir mesmo assim so gastaria a espera inteira
            # contra um canal que ninguem escuta.
            #
            # E ela NAO e apagada. Continua sendo nossa, continua escrita, e
            # quem decide o que fazer com ela e o startup da proxima execucao,
            # com BORROW/CREATE/REFUSE — as mesmas tres saidas que ja tratam
            # qualquer estado preexistente. O controle FICA: enquanto ele
            # estiver ai, esta execucao sabe que instalou algo que nao saiu.
            self.emitir(eventos.POLICY_ORFA_NA_MAQUINA)
            return

        if maquina.liberar_policy_do_windows(self.controle_da_policy):
            # Confirmado: nao ha mais o que pedir nem o que observar. Os handles
            # do controle fecham aqui, e nao no fim do processo — uma execucao
            # que troca de certificado descartaria um controle por guardiao.
            maquina.encerrar_controle_do_guardiao(self.controle_da_policy)
            self.controle_da_policy = None
            return
        # A policy CONTINUA na maquina: o guardiao nao respondeu, ou tentou e
        # falhou. O controle fica de proposito — quem for liberar o host precisa
        # saber que ainda ha estado nosso instalado, e o guardiao segue como
        # fallback de crash.
        self.emitir(eventos.POLICY_NAO_REMOVIDA)

    def liberar_policy_sem_apagar_a_causa(self) -> None:
        """Libera a policy quando JA existe uma falha em curso.

        Mesmo princípio de `encerrar_sessao_sem_apagar_a_causa`, e pelo mesmo
        motivo: um bug aqui — ou no adapter que relata este bug — substituiria a
        falha que obrigou o encerramento. A causa vence; a falha do cleanup vira
        evento, e quando nem o relato funciona nao ha para onde contar.
        """
        try:
            self.liberar_policy()
        except Exception as erro:  # noqa: BLE001 — ver docstring
            try:
                self.emitir(eventos.POLICY_NAO_REMOVIDA,
                            tipo_da_falha=type(erro).__name__)
            except Exception:  # noqa: BLE001 — nem o relato pode vencer a causa
                return

    def registrar(self, codigo: str, metodo: str, posicao: int, *args) -> None:
        """Pede uma gravacao semantica e relata o que aconteceu."""
        if getattr(self.planilha, metodo)(*args):
            self.emitir(codigo, posicao=posicao)
            return
        self.emitir(eventos.LINHA_NAO_ENCONTRADA_NA_PLANILHA, posicao=posicao)

    # ── Certificado e sessao ──────────────────────────────────────────────────

    def encerrar_sessao(self) -> None:
        """Logout no portal e depois os recursos do navegador, nesta ordem.

        A ordem e a do legado e importa: depois de `encerrar()` nao ha pagina
        para clicar em 'Sair'.

        O `finally` nao e decoracao. Sem ele, um bug nosso no logout pulava
        `encerrar()` e o contexto do Chrome e o Playwright ficavam abertos —
        o legado sempre os fechava, e piorar LOGIN_RESOURCE_CLEANUP_GAP nao
        estava em questao. O bug continua subindo; o que muda e que os recursos
        vao embora antes.
        """
        if self.sessao is None:
            return
        sessao, self.sessao = self.sessao, None
        try:
            navegador.encerrar_no_portal(sessao.pagina)
        finally:
            sessao.encerrar()

    def encerrar_sessao_sem_apagar_a_causa(self) -> None:
        """Encerra a sessao quando JA existe uma falha em curso.

        CLEANUP_PRIMARY_ERROR_MASKING: um bug no teardown aqui SUBSTITUI a falha
        que obrigou o teardown. O operador passa a ver o sintoma e perde o
        diagnostico — e, dentro do laco, a execucao inteira aborta em vez de
        retentar o CNPJ.

        Isto nao e `except Exception: pass`. A falha do teardown nao desaparece:
        ela vira um EVENTO, com o nome da classe, no mesmo instante. O que ela
        deixa de fazer e apagar a causa.

        Usado SO nos dois pontos onde ha falha em voo. No caminho normal quem
        vale e `encerrar_sessao`, e la um bug de teardown sobe.

        E se o relato tambem falhar
        ---------------------------
        PRIMARY_FAILURE_EVENT_EMISSION_MASKING: um bug no adapter de eventos,
        aqui dentro, tomava o lugar da causa exatamente como o bug de teardown
        tomava — so que um nivel mais fundo. O emissor e o UNICO canal de relato
        que existe; quando ele proprio quebra, nao ha para onde contar, e
        insistir custaria a causa.

        Este `return` e o unico ponto do projeto onde uma falha do emissor e
        engolida, e ele existe so por isto. Em qualquer outro lugar — inclusive
        no `emitir` logo acima — um bug no adapter sobe como o bug que e.
        """
        try:
            self.encerrar_sessao()
        except Exception as erro:  # noqa: BLE001 — ver docstring
            try:
                self.emitir(eventos.FALHA_AO_ENCERRAR_SESSAO,
                            tipo_da_falha=type(erro).__name__)
            except Exception:  # noqa: BLE001 — ver "e se o relato tambem falhar"
                return

    def buscar_certificado(self, nome: str):
        return self.provedor.resolver(nome)

    def chave_do_certificado(self, nome: str) -> str | None:
        return self.buscar_certificado(nome).chave

    def trocar_certificado(self, item) -> bool:
        """Prepara a maquina para o certificado do item. False se ele nao existe.

        A policy tem de existir ANTES do login: a flag de auto-selecao entra na
        linha de comando do Chrome.
        """
        self.encerrar_sessao()
        self.certificado_atual = item.certificado
        self.emitir(eventos.CERTIFICADO_INICIADO, posicao=item.posicao)

        busca = self.buscar_certificado(item.certificado)
        if not busca.resolvida:
            # Nao instalado e ambiguo pedem acoes DIFERENTES do operador: instalar
            # o certificado, ou reescrever o nome na planilha. Um codigo so
            # esconderia essa diferenca.
            self.emitir(
                eventos.CERTIFICADO_AMBIGUO if busca.ambigua
                else eventos.CERTIFICADO_NAO_INSTALADO,
                posicao=item.posicao,
                quantidade=len(busca.ambiguidade) or None,
            )
            return False
        chave = busca.chave

        # A policy anterior — se for nossa — sai ANTES de a proxima entrar. A
        # sessao ja foi encerrada acima, entao nenhum navegador a esta usando, e
        # sem isto os guardioes se acumulavam: um por certificado, todos vivos
        # ate a morte do processo (MULTIPLE_POLICY_GUARDIANS_LIFETIME).
        self.liberar_policy()

        if self.controle_da_policy is not None:
            # A liberacao acima NAO confirmou: a policy do certificado anterior
            # continua instalada, e ela e nossa. Ate a fatia 13A seguiamos assim
            # mesmo, e o guardiao novo escrevia por cima — inclusive por cima da
            # nossa. Com a escrita nao destrutiva ninguem escreve por cima de
            # nada, e insistir daria uma de duas saidas ruins: metade da policy
            # trocada, ou o Chrome auto-selecionando o certificado ANTERIOR.
            #
            # Fail-closed. Nao e um item que se pula: a condicao e do host.
            raise policy_certificado.ConfiguracaoDeHostIncompativel(
                policy_certificado.POLICY_ANTERIOR_NAO_REMOVIDA
            )

        resultado = maquina.garantir_policy_do_windows(
            self.provedor.certificado(chave).subject_cn)
        self.policy_confiavel = resultado.confiavel
        if resultado.controle is not None:
            self.controle_da_policy = resultado.controle
        if not self.policy_confiavel:
            self.emitir(eventos.POLICY_NAO_CONFIAVEL)
        elif not resultado.sera_limpa:
            # POLICY_STALE_OWNERSHIP_GAP: a policy ja existia e ninguem desta
            # execucao vai remove-la.
            self.emitir(eventos.POLICY_PERMANECERA_NA_MAQUINA)
        return True

    def exigir_responsavel_pela_policy(self) -> None:
        """Nenhum navegador novo enquanto o guardiao da policy nao estiver vivo.

        Fatia 13A.4, e o fechamento de
        GUARDIAN_FAILURE_ORPHANED_POLICY_ACCEPTANCE_RISK do lado de ca. A policy
        e uma configuracao GLOBAL do Windows que faz o Chrome escolher um
        certificado sozinho; enquanto ela existe sem processo responsavel, nao ha
        quem a remova quando esta execucao acabar de qualquer maneira que nao seja
        a normal.

        SO quando a policy e NOSSA. Uma policy emprestada (JA_ATIVA) nunca teve
        guardiao desta execucao, e exigir um dela seria recusar exatamente o caso
        que a 12D decidiu aceitar.

        Aqui, e nao em `trocar_certificado`, porque este e o unico ponto do app
        que cria sessao: a primeira do certificado, e tambem a que um relogin
        abre depois de a anterior cair. Um so lugar, todos os caminhos.
        """
        if self.controle_da_policy is None:
            return
        if (maquina.estado_do_guardiao(self.controle_da_policy)
                == policy_certificado.GUARDIAO_VIVO):
            return
        # Fail-closed, e como condicao do HOST — nao como falha do CNPJ. Um
        # retry de item repetiria login e captcha contra um problema que nao esta
        # no portal.
        raise policy_certificado.ConfiguracaoDeHostIncompativel(
            policy_certificado.GUARDIAO_NAO_ESTA_VIVO
        )

    def autenticar(self, item) -> bool:
        """Garante uma sessao aberta. False se o login nao autenticou.

        O login recebe UMA coisa sobre a policy: se pode confiar na auto-selecao.
        Quem limpa a policy e de quem e o registro nao sao assunto dele.
        """
        if self.sessao is not None:
            return True

        self.exigir_responsavel_pela_policy()

        chave = self.chave_do_certificado(self.certificado_atual)
        if chave is None:
            return False

        # A chave do Gemini sai de `chave()`, e nao do campo: e aqui que ela e
        # usada pela primeira vez, e numa execucao montada pela plataforma e
        # aqui que ela e obtida. Se faltar, a falha sobe antes de o navegador
        # abrir — `autenticar` fica fora de todo `try` do laco, de proposito.
        resultado = maquina.abrir_sessao(
            self.provedor.certificado(chave),
            self.policy_confiavel,
            self.config_captcha.chave(),
            self.diretorio_da_execucao,
        )
        if not resultado.autenticado:
            self.emitir(eventos.LOGIN_FALHOU, posicao=item.posicao)
            return False

        self.sessao = resultado.sessao
        self.emitir(eventos.LOGIN_CONCLUIDO)
        return True


# ── A unidade de trabalho: um CNPJ ────────────────────────────────────────────

def _avisos_como_eventos(execucao: _Execucao, extracao) -> None:
    """Os avisos que SO a integracao observa viram fatos da aplicacao.

    A integracao nao conhece eventos: ela devolve avisos no resultado, e a
    traducao acontece aqui. `ExtracaoFiscal` ja deduplica os avisos, entao um
    aviso repetido nao vira dois eventos.
    """
    for aviso in extracao.avisos:
        codigo = eventos.AVISO_PARA_CODIGO.get(aviso)
        if codigo is not None:
            execucao.emitir(codigo)


def _extrair_debitos(execucao: _Execucao, item) -> None:
    """Consulta o DCTFWeb e grava: detalhe primeiro, coluna depois.

    A ordem e o RESUMABILITY_CONTRACT — se a gravacao do detalhe cair, a coluna
    nao e marcada e a proxima execucao refaz o DCTFWeb inteiro.
    """
    extracao = consulta_fiscal.consultar_dctfweb(execucao.sessao, item.cnpj)
    _avisos_como_eventos(execucao, extracao)
    registro = execucao.planilha.registrar_debitos(item.linha, list(extracao.linhas))
    execucao.emitir(eventos.DEBITOS_REGISTRADOS, posicao=item.posicao,
                    quantidade=len(extracao), paginas=extracao.paginas)
    if not registro.marcado:
        execucao.emitir(eventos.LINHA_NAO_ENCONTRADA_NA_PLANILHA, posicao=item.posicao)


def _extrair_processos(execucao: _Execucao, item) -> None:
    """Consulta os Processos Fiscais e grava, na mesma ordem e pelo mesmo motivo."""
    extracao = consulta_fiscal.consultar_processos(execucao.sessao, item.cnpj)
    _avisos_como_eventos(execucao, extracao)
    registro = execucao.planilha.registrar_processos(item.linha, list(extracao.linhas))
    execucao.emitir(eventos.PROCESSOS_REGISTRADOS, posicao=item.posicao,
                    quantidade=len(extracao), paginas=extracao.paginas)
    if not registro.marcado:
        execucao.emitir(eventos.LINHA_NAO_ENCONTRADA_NA_PLANILHA, posicao=item.posicao)


def _consultar_situacao(execucao: _Execucao, item, retomada) -> None:
    """Le a situacao fiscal e grava o que ela determina.

    FISCAL_UNKNOWN_SEMANTICS preservado: situacao nao reconhecida nao grava
    NADA, entao a linha volta pendente na proxima execucao — e voltara sempre,
    enquanto o texto nao for reconhecido. Caracterizado, nao corrigido.
    """
    posicao = item.posicao
    situacao = consulta_fiscal.ler_situacao(execucao.sessao)

    if not situacao.reconhecida:
        execucao.emitir(eventos.SITUACAO_FISCAL_NAO_RECONHECIDA, posicao=posicao)
        return

    if not situacao.com_pendencia:
        if not retomada.dctfweb_feito:
            execucao.registrar(eventos.SEM_DEBITOS_REGISTRADO,
                               "registrar_sem_debitos", posicao, item.linha)
        if not retomada.processos_feitos:
            execucao.registrar(eventos.SEM_PROCESSOS_REGISTRADO,
                               "registrar_sem_processos", posicao, item.linha)
        return

    # Nenhum botao de acao encontrado.
    if not situacao.tem_dctfweb and not situacao.tem_processo:
        if not retomada.dctfweb_feito:
            execucao.registrar(eventos.DEBITOS_NAO_COMPENSAVEIS_REGISTRADO,
                               "registrar_debitos_nao_compensaveis", posicao,
                               item.linha)
        if not retomada.processos_feitos:
            execucao.registrar(eventos.SEM_PROCESSOS_REGISTRADO,
                               "registrar_sem_processos", posicao, item.linha)
        return

    # ── Divida DCTFWeb ────────────────────────────────────────────────────────
    if situacao.tem_dctfweb and not retomada.dctfweb_feito:
        _extrair_debitos(execucao, item)
    elif retomada.dctfweb_feito:
        execucao.emitir(eventos.RETOMADA_PULA_DCTFWEB, posicao=posicao)

    # ── Processo Fiscal ───────────────────────────────────────────────────────
    if situacao.tem_processo and not retomada.processos_feitos:
        _extrair_processos(execucao, item)
        if not situacao.tem_dctfweb and not retomada.dctfweb_feito:
            # So havia Processo Fiscal: o portal nunca ofereceu a divida DCTFWeb,
            # e a linha nao pode ficar pendente para sempre por causa disso.
            execucao.registrar(eventos.DEBITOS_REGISTRADOS,
                               "registrar_debitos_concluidos", posicao, item.linha)
    elif situacao.tem_processo and retomada.processos_feitos:
        execucao.emitir(eventos.RETOMADA_PULA_PROCESSOS, posicao=posicao)
    elif not retomada.processos_feitos:
        # Nao existe botao de Processo Fiscal.
        execucao.registrar(eventos.SEM_PROCESSOS_REGISTRADO,
                           "registrar_sem_processos", posicao, item.linha)


def _processar_item(execucao: _Execucao, item) -> None:
    """Um CNPJ, do inicio ao `finally` que toca o disco.

    A retomada e lida AQUI, a cada tentativa. Numa retentativa do mesmo item ela
    ja reflete o que a tentativa anterior gravou — e e isso que faz o DCTFWeb
    concluido nao ser refeito.
    """
    retomada = execucao.planilha.retomada(
        execucao.caminho, item.linha, status_portal.status_encerra_linha
    )

    if retomada.encerrada:
        execucao.emitir(eventos.ITEM_JA_ENCERRADO, posicao=item.posicao)
        return
    if retomada.concluida:
        execucao.emitir(eventos.ITEM_JA_CONCLUIDO, posicao=item.posicao)
        return

    if retomada.dctfweb_feito:
        execucao.emitir(eventos.RETOMADA_PULA_DCTFWEB, posicao=item.posicao)
    if retomada.processos_feitos:
        execucao.emitir(eventos.RETOMADA_PULA_PROCESSOS, posicao=item.posicao)

    try:
        resultado = representacao.representar(
            execucao.sessao, item.cnpj, execucao.config_captcha
        )

        if resultado.encerra_a_linha:
            # O motivo fica na planilha antes de a linha ser abandonada, senao
            # ela volta em branco na proxima execucao.
            if resultado.status_coluna_d:
                execucao.registrar(eventos.RECUSA_REGISTRADA,
                                   "registrar_recusa_do_portal", item.posicao,
                                   item.linha, resultado.status_coluna_d)
            raise _RecusaDoPortal

        if not resultado.representado:
            # Anti-bot esgotado e nao-confirmado sao da SESSAO, nao do CNPJ: a
            # linha volta, e quem decide isso e o laco.
            raise _RepresentacaoNaoConcluida

        _consultar_situacao(execucao, item, retomada)
    finally:
        # Unico toque no disco por CNPJ, e ele acontece mesmo em falha.
        execucao.salvar()


# ── O laco ────────────────────────────────────────────────────────────────────

def _percorrer(execucao: _Execucao, itens: list) -> None:
    total = len(itens)
    tentativas: dict[str, int] = {}
    i = 0

    while i < len(itens):
        item = itens[i]

        if not item.utilizavel:
            execucao.emitir(eventos.ITEM_IGNORADO_SEM_CNPJ,
                            posicao=item.posicao, total=total)
            i += 1
            continue

        if item.certificado != execucao.certificado_atual:
            execucao.trocar_certificado(item)
        if execucao.chave_do_certificado(execucao.certificado_atual) is None:
            i += 1
            continue

        execucao.emitir(eventos.ITEM_INICIADO, posicao=item.posicao, total=total)

        if not execucao.autenticar(item):
            i += 1
            continue

        avancar = True
        try:
            _processar_item(execucao, item)

        except _RecusaDoPortal:
            # A recusa e do CNPJ, nao da sessao: o certificado segue autenticado
            # e o proximo CNPJ do grupo reaproveita o navegador. So se a sessao
            # nao voltar a um estado utilizavel e que ela e fechada. A recusa NAO
            # consome retentativa.
            execucao.emitir(eventos.CNPJ_RECUSADO_PELO_PORTAL, posicao=item.posicao)
            if representacao.recuperar_apos_recusa(execucao.sessao.pagina):
                execucao.emitir(eventos.SESSAO_RECUPERADA_APOS_RECUSA)
            else:
                execucao.emitir(eventos.SESSAO_NAO_RECUPERADA_APOS_RECUSA)
                execucao.encerrar_sessao()

        except (_RepresentacaoNaoConcluida, navegador.FalhaDoNavegador):
            # RETRY_SEMANTIC_CHANGE (fatia 11). Ate aqui isto era
            # `except Exception`: um TypeError nosso era retentado como se fosse
            # o portal fora do ar, e nunca chegava a ninguem. O comportamento
            # antigo esta caracterizado em tests/test_caracterizacao_retry.py.
            #
            # Duas familias, e so elas, tem motivo para uma segunda tentativa:
            #
            #   _RepresentacaoNaoConcluida  anti-bot esgotado ou representacao
            #                               nao confirmada — condicao da SESSAO,
            #                               que um login novo pode resolver;
            #   FalhaDoNavegador            falha tecnica do navegador, ja
            #                               traduzida na fronteira da integracao.
            #
            # O que NAO retenta mais, e por que: bug nosso (nao melhora na
            # segunda vez, e some), falha do adapter de eventos (custaria login,
            # representacao e captcha por um erro de apresentacao) e configuracao
            # invalida (uma chave ausente continua ausente).
            tentativas[item.cnpj] = tentativas.get(item.cnpj, 0) + 1
            n = tentativas[item.cnpj]
            execucao.emitir(eventos.ITEM_FALHOU, posicao=item.posicao,
                            tentativa=n, maximo=MAX_TENTATIVAS_POR_ITEM)
            execucao.encerrar_sessao_sem_apagar_a_causa()
            if n < MAX_TENTATIVAS_POR_ITEM:
                avancar = False   # mesma linha de novo, com sessao nova
            else:
                execucao.emitir(eventos.ITEM_ESGOTOU_RETENTATIVAS, posicao=item.posicao)

        if avancar:
            i += 1

    execucao.encerrar_sessao()


# ── A fronteira publica ───────────────────────────────────────────────────────

def aliases_necessarios(caminho: str) -> tuple[str, ...]:
    """Os certificados que UMA execucao desta planilha vai pedir.

    Existe para quem monta a execucao de fora: na plataforma os certificados
    precisam ser buscados no cofre ANTES de comecar, porque o cofre nao e
    enumeravel — ele so responde "me de o chamado X".

    Mora aqui, e nao na borda, por um motivo so: a resposta depende de QUAIS
    LINHAS serao processadas, e quem sabe isso e a aplicacao. Se a borda
    escolhesse a regra de status por conta propria, existiriam duas respostas
    para a mesma pergunta — e no dia em que divergissem o cofre entregaria o
    conjunto errado, sem ninguem perceber.
    """
    return planilha.aliases_de_certificado(caminho, status_portal.status_encerra_linha)


def executar(
    entrada: EntradaDebitosEmAberto,
    config_captcha: ConfigCaptcha,
    emitir_evento: Emissor = None,
    provedor_de_certificados: ProvedorDeCertificados | None = None,
    diretorio_da_execucao: str | None = None,
) -> None:
    """Processa a planilha inteira.

    Devolve `None`, como o fluxo legado sempre devolveu: sucesso e a ausencia de
    exception, e uma falha fatal sobe. Nao ha resumo final porque nenhum
    consumidor precisa de um — a necessidade operacional e em TEMPO REAL, e quem
    a atende e `emitir_evento`.

    Levanta `ConfiguracaoDeHostIncompativel` quando o host ja tinha configuracao
    de auto-selecao de certificado que esta execucao nao instalou e nao pode usar
    com seguranca (fatia 12D). E FATAL e nao evento: acontece antes de qualquer
    processamento util, e prosseguir faria o Chrome autenticar com o certificado
    de outra pessoa. Nada e escrito no registro antes dessa decisao, e nada e
    removido depois dela.
    """
    sessao_planilha = SessaoPlanilha()
    execucao = _Execucao(sessao_planilha, entrada.planilha, config_captcha,
                         emitir_evento, provedor_de_certificados,
                         diretorio_da_execucao)

    try:
        sessao_planilha.abrir(entrada.planilha)
        df, _ = planilha.ler_e_ordenar(entrada.planilha)
        df, _ = planilha.linhas_pendentes(
            df,
            sessao_planilha.mapa_status(entrada.planilha),
            status_portal.status_encerra_linha,
        )
        itens = planilha.itens_pendentes(df)
        if not itens:
            # NADA A FAZER NAO E FALHA, e quem sabe disso e a planilha — por isso
            # ela vem antes do catalogo. Sem item pendente nenhum certificado e
            # necessario: pedi-los assim mesmo custa um PowerShell no desktop e um
            # arquivo baixado por alias no cofre, para entao nao processar linha
            # nenhuma. E um catalogo vazio ainda seria relatado como certificado
            # indisponivel — um erro de operador que nao existe.
            return

        try:
            quantos = execucao.provedor.carregar()
        except certificados.FalhaAoLerCertificados:
            # Falha externa CONHECIDA. A fatia 5A separou de proposito "nao deu
            # para ler" de "nao ha certificado instalado": antes as duas
            # produziam a mesma saida, e o operador era mandado instalar um
            # certificado que ja estava la.
            execucao.emitir(eventos.LEITURA_DE_CERTIFICADOS_FALHOU)
            return

        if not quantos:
            execucao.emitir(eventos.CERTIFICADOS_INDISPONIVEIS)
            return

        _percorrer(execucao, itens)
    except BaseException:
        # Ha falha em voo — inclusive Ctrl+C. O teardown acontece, e um bug nele
        # nao pode tomar o lugar da causa.
        execucao.encerrar_sessao_sem_apagar_a_causa()
        execucao.liberar_policy_sem_apagar_a_causa()
        raise
    else:
        # Caminho normal: se o teardown tiver um bug nosso, ele aparece como o
        # que e. Nao ha causa para proteger.
        execucao.encerrar_sessao()
        # Depois da sessao: enquanto houver navegador vivo, a policy esta em uso.
        execucao.liberar_policy()
    finally:
        execucao.salvar()
        sessao_planilha.descartar()
