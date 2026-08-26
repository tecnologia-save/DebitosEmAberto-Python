"""O protocolo da policy que faz o Chrome escolher o certificado sozinho.

O que esta fatia separa
-----------------------
`cert_windows.py` continua sendo as PRIMITIVAS do Windows: winreg, ctypes,
ShellExecuteExW, o processo guardiao. Nada disso se move — reescrever 320 linhas
de ctypes que ja funcionam nao traria ganho nenhum.

O que vem para ca e o PROTOCOLO: a decisao de quando lancar o guardiao, quando
confiar numa policy que ja existe, quanto esperar e o que reportar. Era isso que
estava preso dentro de `iniciar_guarda`, entrelacado com as chamadas ao sistema e
portanto intestavel sem Windows real.

As primitivas entram por parametro. Nao ha service, repository nem factory: sao
tres callables.

Dois donos, dois ciclos de vida
-------------------------------
OWNER LOGICO — o processo principal (hoje `main`, no futuro o app). Decide QUANDO
pedir a policy e para qual certificado. Nao a remove: ele pode morrer de formas
que nao executam `finally`.

OWNER OPERACIONAL — o processo guardiao, elevado e separado. Escreve a policy,
vigia o PID do processo principal e a remove quando ele morre, por qualquer
motivo. E o unico rollback que sobrevive a Ctrl+C, crash e kill.

Os dois nao sao o mesmo objeto e nao devem ser fundidos: o segundo existe
justamente porque o primeiro nao e confiavel para limpar.

O que a fatia 12B.2 acrescentou
-------------------------------
O owner logico passou a poder PEDIR a limpeza, em vez de so morrer. Antes, a
unica forma de a policy sair era o processo principal terminar — o que num
adapter reutilizavel podia nunca acontecer, e deixava estado global instalado
depois de a execucao acabar (POLICY_LIFETIME_EXCEEDS_APP_EXECUTION).

O pedido nao substitui o guardiao: ele continua sendo o rollback de crash. O que
mudou e que agora existe um caminho NORMAL, e ele confirma.

O que a fatia 12D acrescentou
-----------------------------
A decisao de STARTUP: o que fazer com a configuracao que ja estava no host antes
desta execucao. Ate aqui a resposta era implicita e vinha de uma leitura parcial
— o CN da primeira colmeia nao vazia. Se batia, adotava-se; se nao, sobrescrevia
-se. As duas saidas eram inseguras por motivos opostos.

Agora sao tres e explicitas: CRIAR, EMPRESTAR, RECUSAR. Nenhuma configuracao que
esta execucao nao instalou e modificada ou apagada.

Riscos que este modulo NOMEIA mas nao corrige
---------------------------------------------
A policy e estado GLOBAL do Windows, numa chave fixa, sem dono no payload. Ver
POLICY_STALE_OWNERSHIP_GAP e GLOBAL_CERT_POLICY_CONCURRENCY_RISK no relatorio da
fatia 7A. Todos estao presos por teste.

A OWNERSHIP continua epistemicamente indecidivel entre execucoes: sem marcador
no payload, estado preexistente identico ao nosso e indistinguivel do nosso. A
12D nao resolve isso — ela retira a necessidade de resolver, porque nenhuma das
tres saidas precisa saber quem escreveu.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# Quanto tempo esperar a policy SUMIR depois de pedir a limpeza ao guardiao.
# Mais curto que a ativacao: o guardiao ja esta vivo e so precisa remover uma
# chave. POLICY_POSSIBLE_DEFECT: e um numero magico, como o de cima.
SONDAGENS_LIBERACAO = 20
INTERVALO_LIBERACAO_S = 0.25

# Quanto tempo esperar a policy do guardiao aparecer. Preservado do original:
# 60 sondagens de meio segundo. POLICY_POSSIBLE_DEFECT — e um numero magico, sem
# condicao associada alem de "ainda nao apareceu".
SONDAGENS = 60
INTERVALO_SONDAGEM_S = 0.5

# Os desfechos. Sao strings e nao enum porque o consumidor de hoje so precisa de
# `.confiavel`; os nomes existem para o diagnostico e para a fatia 7B.
JA_ATIVA = "já ativa (escrita por outra execução)"
ATIVADA = "ativada por guardião desta execução"
ELEVACAO_RECUSADA = "elevação recusada"
NAO_APARECEU = "guardião lançado, policy não apareceu"


# ── Estado preexistente da policy (fatia 12D) ─────────────────────────────────
#
# O lease de host da fatia 12C prova que nenhuma outra instancia protegida de
# DebitosEmAberto esta concorrendo. Ele NAO prova quem escreveu a policy que ja
# estava no registro quando esta execucao comecou: o registro e persistente e o
# lease e efemero, entao um crash duplo, um reboot, um administrador ou outra
# ferramenta deixam estado que nos sobrevive.
#
# Sem marcador de dono no payload — e nao ha nenhum — "parece exatamente o que
# escreveriamos" nao e prova de autoria. Por isso a decisao abaixo nunca chama
# estado preexistente de nosso.

CRIAR = "criar"
EMPRESTAR = "emprestar"
RECUSAR = "recusar"

# Motivos de recusa. Vocabulario FECHADO e sem nenhum dado interpolado: nao
# carregam CN, caminho de registro, usuario nem PID. Servem para o operador
# saber o que sanear, e nao para identificar ninguem.
OUTRO_CERTIFICADO = "aponta para outro certificado"
COLMEIAS_DIVERGENTES = "as duas colmeias discordam entre si"
CONTEUDO_NAO_RECONHECIDO = "conteúdo em formato não reconhecido"
REGRAS_ADICIONAIS = "há regras além das que a automação escreve"
COLMEIA_ILEGIVEL = "uma das colmeias não pôde ser lida"


class ConfiguracaoDeHostIncompativel(Exception):
    """Havia configuração de auto-seleção neste host, e não é seguro usá-la.

    FAIL-CLOSED, e a razão é concreta: uma policy preexistente apontando para
    outro certificado faria o Chrome autenticar como outra empresa, sem
    perguntar nada a ninguém. Seguir em frente seria pior do que parar.

    A mensagem é deliberadamente pobre — ver o vocabulário fechado acima.
    """

    def __init__(self, motivo: str) -> None:
        super().__init__(
            "Existe configuração de seleção automática de certificado "
            f"incompatível ou ambígua neste host ({motivo}). A automação não "
            "altera configuração que não instalou; é preciso saneá-la antes."
        )
        self.motivo = motivo


@dataclass(frozen=True)
class RegraDaPolicy:
    """Uma entrada da policy, já interpretada por quem sabe ler o registro.

    `reconhecida=False` quando o payload não tem a forma que sabemos ler — e aí
    `padrao` e `cn` ficam vazios, em vez de virarem um palpite.

    `cn` sai do `repr` porque um CN identifica a empresa e não pode cair em log.
    Isso é DEFESA ADICIONAL, não garantia: `asdict`, acesso direto ao atributo e
    log manual continuam expondo.
    """

    nome: str
    padrao: str = ""
    cn: str = field(default="", repr=False)
    reconhecida: bool = True


@dataclass(frozen=True)
class ColmeiaDaPolicy:
    """O que uma colmeia tem — sem resumo, sem primeira-que-serve.

    `legivel=False` é diferente de `existe=False`: a primeira é ignorância, a
    segunda é ausência. Confundi-las seria escrever por cima do que não vimos.
    """

    rotulo: str
    existe: bool = False
    legivel: bool = True
    regras: tuple[RegraDaPolicy, ...] = ()


@dataclass(frozen=True)
class DecisaoDeStartup:
    """CRIAR, EMPRESTAR ou RECUSAR — e, quando recusa, o que o operador sanear."""

    decisao: str
    motivo: str = ""


def _verdicto(colmeia: ColmeiaDaPolicy, cn: str, padroes: tuple[str, ...]) -> str:
    """"" se esta colmeia é exatamente o que escreveríamos; senão, o motivo.

    "Exatamente" é literal: os mesmos nomes de valor, os mesmos padrões, na
    mesma ordem, com o mesmo CN e nada mais. Qualquer folga aqui vira licença
    para sobrescrever configuração alheia.
    """
    esperado = {str(i): padrao for i, padrao in enumerate(padroes, 1)}
    nomes = {regra.nome for regra in colmeia.regras}
    if nomes > set(esperado):
        # Tem tudo o que escreveríamos, e mais alguma coisa.
        return REGRAS_ADICIONAIS
    if nomes != set(esperado):
        # Faltam regras nossas, ou os nomes são outros. Seja o que for, não é
        # uma configuração que saibamos ler inteira.
        return CONTEUDO_NAO_RECONHECIDO
    for regra in colmeia.regras:
        if not regra.reconhecida or regra.padrao != esperado[regra.nome]:
            return CONTEUDO_NAO_RECONHECIDO
    fora = {regra.cn for regra in colmeia.regras} - {cn}
    if fora:
        return OUTRO_CERTIFICADO
    return ""


def avaliar_estado_inicial(
    colmeias: tuple[ColmeiaDaPolicy, ...], cn: str, padroes: tuple[str, ...]
) -> DecisaoDeStartup:
    """O que esta execução pode fazer com a policy que já estava no host.

    Três saídas, e só a primeira escreve no registro:

    CRIAR      — não há configuração nenhuma. A automação instala a sua, é dona
                 dela, e o guardião a remove no fim.
    EMPRESTAR  — já há exatamente a configuração de que precisamos. Usa-se sem
                 tocar em nada: sem escrita, sem guardião dono, sem limpeza no
                 fim. Continua sendo de quem quer que a tenha escrito.
    RECUSAR    — qualquer outra coisa. Divergência entre colmeias, outro
                 certificado, formato desconhecido, regras a mais, colmeia
                 ilegível.

    Por que recusar em vez de sobrescrever e restaurar depois: restaurar exige
    guardar payload arbitrário do registro, devolvê-lo com privilégio (HKLM) e
    sobreviver a um crash no meio — três responsabilidades novas para preservar
    algo que não é nosso. Recusar não tem nenhuma delas, e erra para o lado
    seguro.

    Por que recusar em vez de marcar posse: um marcador só distingue o que for
    escrito DEPOIS de ele existir. O estado que já está no host hoje continuaria
    indistinguível, que é justamente o caso que precisa de resposta.
    """
    if any(not colmeia.legivel for colmeia in colmeias):
        return DecisaoDeStartup(RECUSAR, COLMEIA_ILEGIVEL)

    com_conteudo = [c for c in colmeias if c.existe and c.regras]
    if not com_conteudo:
        # Inclui a chave que existe e está vazia: sem regras, o Chrome não
        # seleciona nada, e a nossa própria escrita cria a chave antes de
        # preenchê-la.
        return DecisaoDeStartup(CRIAR)

    verdictos = [_verdicto(colmeia, cn, padroes) for colmeia in com_conteudo]
    if all(verdicto == "" for verdicto in verdictos):
        return DecisaoDeStartup(EMPRESTAR)
    if len(set(verdictos)) > 1:
        # Uma colmeia serve e a outra não, ou as duas falham por motivos
        # diferentes. O Chrome lê uma delas; qual, não é assunto desta decisão,
        # porque divergência já basta para recusar.
        return DecisaoDeStartup(RECUSAR, COLMEIAS_DIVERGENTES)
    return DecisaoDeStartup(RECUSAR, verdictos[0])


@dataclass(frozen=True)
class ResultadoDaPolicy:
    """Como terminou o pedido de policy — e, principalmente, quem limpa depois.

    O acoplamento atual com o login e um `bool`, e `confiavel` continua servindo
    exatamente para isso. O resto existe porque as duas formas de chegar em
    "confiável" tem consequencias de LIMPEZA opostas, e um bool nao consegue
    dizer isso.
    """

    situacao: str
    tem_guardiao: bool = False
    # O que permite COMANDAR o guardiao desta policy — e nao apenas saber que
    # ele existe. Opaco de proposito: o protocolo nunca o inspeciona, so o
    # devolve a quem sabe usa-lo. `repr=False` para que um handle do Windows
    # nunca caia num log.
    controle: object | None = field(default=None, repr=False)

    @property
    def confiavel(self) -> bool:
        """True se o Chrome vai auto-selecionar — o antigo `policy_ok`."""
        return self.situacao in (JA_ATIVA, ATIVADA)

    @property
    def sera_limpa(self) -> bool:
        """False quando ninguem desta execucao vai remover a policy.

        E o POLICY_STALE_OWNERSHIP_GAP em forma de propriedade: `confiavel` e
        True e mesmo assim nao ha guardiao associado a esta execucao.
        """
        return self.tem_guardiao


def garantir_policy(
    cn: str,
    avaliar_inicio: Callable[[], DecisaoDeStartup],
    ler_cn_atual: Callable[[], str],
    lancar_guardiao: Callable[[str], int],
    aguardar: Callable[[], None],
    policy_ja_e_nossa: bool = False,
) -> ResultadoDaPolicy:
    """Garante que o Chrome vai auto-selecionar o certificado de `cn`.

    As primitivas entram por parametro porque todas precisam de Windows real — e
    sem elas o protocolo inteiro roda em qualquer maquina.

    A espera e pelo CN PEDIDO, nunca pela mera existencia da policy. Isso e o que
    torna seguro trocar de certificado no meio da execucao: a policy do
    certificado anterior ainda esta escrita quando o novo guardiao sobe, e
    conferir so a existencia devolveria True de imediato — o Chrome subiria com o
    certificado errado, autenticando na empresa errada.

    A FRONTEIRA DE MUTACAO (fatia 12D)
    ----------------------------------
    `avaliar_inicio` roda ANTES de `lancar_guardiao`, que e a unica coisa aqui
    que escreve no registro. Nao ha caminho que escreva sem passar por ela.

    `policy_ja_e_nossa` e a unica forma de pular essa avaliacao, e existe porque
    dentro de UMA execucao a posse e demonstravel: quem chama detem o controle
    do guardiao que escreveu a policy anterior. E o caso da troca de certificado
    quando a liberacao nao confirmou — a policy que ainda esta la e nossa, com
    handle e tudo, e recusa-la seria a automacao se barrando a si mesma. Entre
    execucoes distintas nao ha nada equivalente, e por isso o padrao e False.
    """
    if not policy_ja_e_nossa:
        decisao = avaliar_inicio()
        if decisao.decisao == RECUSAR:
            raise ConfiguracaoDeHostIncompativel(decisao.motivo)
        if decisao.decisao == EMPRESTAR:
            # EMPRESTADA, e nao nossa: nenhum guardiao e lancado, nada e escrito
            # e nada sera removido no fim. Ver POLICY_STALE_OWNERSHIP_GAP — so
            # que agora e uma escolha, e nao um acidente de leitura.
            return ResultadoDaPolicy(JA_ATIVA, tem_guardiao=False)

    controle = lancar_guardiao(cn)
    if controle is None:
        return ResultadoDaPolicy(ELEVACAO_RECUSADA, tem_guardiao=False)

    for _ in range(SONDAGENS):
        if ler_cn_atual() == cn:
            return ResultadoDaPolicy(ATIVADA, tem_guardiao=True, controle=controle)
        aguardar()

    # O guardiao subiu mas a policy nao ficou visivel DESTE processo — e o Chrome,
    # que roda no mesmo contexto, tambem nao a veria. Elevacao em outra conta.
    return ResultadoDaPolicy(NAO_APARECEU, tem_guardiao=True, controle=controle)


def liberar_policy(
    controle: object,
    pedir_limpeza: Callable[[object], None],
    policy_ainda_existe: Callable[[], bool],
    aguardar: Callable[[], None],
) -> bool:
    """Pede ao guardiao que remova a policy e CONFIRMA que ela saiu.

    O guardiao roda ELEVADO; o processo principal, nao. Quem escreveu a policy em
    HKLM foi ele, e so ele tem privilegio para remove-la —
    NORMAL_POLICY_CLEANUP_PRIVILEGE_GAP. Ate a fatia 12B.2 o processo comum
    tentava remover sozinho, e a falha em HKLM sumia dentro da primitiva.

    A distincao que da nome a esta funcao: PEDIR nao e CONFIRMAR. O retorno
    `True` significa que a policy nao esta mais escrita — verificado por leitura,
    e nao por o guardiao ter acusado recebimento. Se ele receber o pedido e
    falhar, isto devolve `False`, e a policy continua sendo do chamador.

    A espera e finita: um guardiao que nao responde nao pode travar a execucao.
    Ele continua vivo como fallback de crash.
    """
    pedir_limpeza(controle)
    for _ in range(SONDAGENS_LIBERACAO):
        if not policy_ainda_existe():
            return True
        aguardar()
    return False
