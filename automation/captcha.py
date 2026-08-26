"""A fronteira do captcha: o contrato que a automacao consome, sem o fork.

`resolvedor_captcha` e um fork vendorizado de 2.127 linhas. Esta fatia NAO o
reorganiza e NAO o migra. Ela responde outra pergunta: qual e o contrato que
DebitosEmAberto realmente usa? Resposta — UMA funcao, `solve_hcaptcha(page) ->
bool`, e uma chave de API.

O que este modulo acrescenta sobre isso:

    - um resultado NOMEADO no lugar de um bool ambiguo;
    - a chave chegando por PARAMETRO, e nunca lida do ambiente aqui;
    - falha externa conhecida virando desfecho nomeado, bug nosso subindo;
    - o retry num lugar so, explicito e testavel.

O transporte do segredo — fechado na 9B.1
-----------------------------------------
Este texto descrevia `CAPTCHA_INTEGRATION_COUPLING`: `solve_hcaptcha` lia
`os.environ["GEMINI_API_KEY"]` por conta propria e alguem PRECISAVA por a chave
no ambiente antes de chamar.

Nao e mais verdade. O seam foi aberto no fork (`api_key=None` significa "nao
informei"; qualquer outro valor e usado tal qual, inclusive `""`), e desde a
9B.1 este modulo o USA: a chave que chega em `ConfigCaptcha` desce ate o solver
sem passar pelo ambiente. O fallback do fork continua existindo para o caller
legado, e so para ele.

O objeto `Page` do navegador atravessa esta fronteira, e isso e deliberado: e
uma integracao stateful com o browser. Ele nao passa daqui — domain,
status_portal e boundary nao o conhecem.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from patchright.sync_api import Error as ErroDoNavegador

# As duas familias que uma tentativa de captcha pode levantar, confirmadas por
# sonda e nao presumidas:
#
#   RuntimeError      o fork usa para "google.genai ausente", "chave nao
#                     configurada" E "Gemini falhou em todos os modelos" — a
#                     ultima e transitoria, entao RuntimeError NAO pode significar
#                     "desista";
#   ErroDoNavegador   base unica de tudo que o patchright levanta, timeout incluso.
#
# Qualquer outra coisa e bug nosso e sobe.
_FALHAS_DA_TENTATIVA = (RuntimeError, ErroDoNavegador)

# Um resultado nomeado no lugar de True/False.
#
# ATENCAO — `RESOLVIDO_OU_AUSENTE` tem esse nome desconfortavel de proposito: o
# fork devolve `True` tanto para "resolvi o desafio" quanto para "nao havia
# desafio nenhum". A informacao para distinguir os dois NAO existe no contrato
# atual, e inventar aqui um `NAO_PRESENTE` seria fingir que existe.
RESOLVIDO_OU_AUSENTE = "resolvido ou ausente"
NAO_RESOLVIDO = "não resolvido"
FALHA_EXTERNA = "falha do serviço de resolução"

_PLACEHOLDER = "cole-"


class ConfiguracaoInvalida(Exception):
    """A automacao nao foi configurada para resolver captcha.

    Diferente de `CaptchaIndisponivel`: aqui nada foi tentado, e quem opera pode
    corrigir. A mensagem diz o que falta sem jamais mostrar o valor.
    """


@dataclass(frozen=True)
class ConfigCaptcha:
    """O que a capacidade de captcha precisa para funcionar. Um campo.

    `repr=False` na chave e DEFESA ADICIONAL, nao garantia: `asdict()`, o acesso
    explicito ao atributo e um log manual continuam expondo o valor. O que ele
    resolve e o caso comum — o objeto caindo inteiro num traceback ou num print
    de debug.
    """

    api_key: str = field(repr=False)

    def validar(self) -> None:
        chave = (self.api_key or "").strip()
        if not chave:
            raise ConfiguracaoInvalida(
                "A chave da API do Gemini não está configurada; sem ela o captcha "
                "não pode ser resolvido."
            )
        if chave.startswith(_PLACEHOLDER):
            raise ConfiguracaoInvalida(
                "A chave da API do Gemini ainda é o texto de exemplo do modelo."
            )


def resolver(
    alvo,
    config: ConfigCaptcha,
    tentativas: int = 2,
    resolver_bruto: Callable[[object], bool] | None = None,
    aguardar: Callable[[], None] | None = None,
) -> str:
    """Tenta resolver o captcha em `alvo` (uma Page ou popup do navegador).

    `resolver_bruto` e a fronteira externa inteira num callable — e o que permite
    a suite rodar sem Gemini e sem navegador. Sem service, sem factory.

    CAPTCHA_RETRY_CONTRACT: `tentativas` e o retry DESTA camada. O fork tem o
    seu proprio (6 rodadas internas, ate 5 chamadas ao Gemini por rodada), e cada
    chamada custa. Esta funcao nao acrescenta nenhum nivel novo — ela apenas
    torna explicito o que ja existia inline.

    Tres desfechos, e nenhuma exception nova: o chamador de hoje trata "nao
    resolveu" e "o servico falhou" da mesma forma, entao transformar a segunda em
    exception mudaria o fluxo sem que ninguem tivesse pedido. A distincao fica
    registrada no RESULTADO, disponivel para quem quiser usar.

    A unica coisa que levanta e configuracao ausente — ai nada foi tentado e
    repetir e desperdicio puro. Bug nosso sobe, como em todas as outras fatias.
    """
    config.validar()

    if resolver_bruto is None:
        from resolvedor_captcha import solve_hcaptcha

        # A chave viaja EXPLICITAMENTE ate o solver. Sem esta linha ele recebia
        # `api_key=None`, caia no fallback de ambiente do fork, e o caminho novo
        # so funcionava porque o adapter legado ainda populava `os.environ` —
        # dependencia invisivel, provada por sonda na fatia 9B.1.
        #
        # Fica embutida no callable para nao mudar o contrato
        # `Callable[[object], bool]` da fronteira externa.
        chave = config.api_key

        def resolver_bruto(alvo):
            return solve_hcaptcha(alvo, api_key=chave)

    houve_falha_externa = False

    for tentativa in range(1, tentativas + 1):
        try:
            if resolver_bruto(alvo):
                return RESOLVIDO_OU_AUSENTE
        except _FALHAS_DA_TENTATIVA:
            # A mensagem original NUNCA e propagada: a do fork embute o ultimo
            # erro do Gemini, e a do navegador embute a URL da pagina.
            houve_falha_externa = True

        if aguardar is not None and tentativa < tentativas:
            aguardar()

    return FALHA_EXTERNA if houve_falha_externa else NAO_RESOLVIDO
