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

Riscos que este modulo NOMEIA mas nao corrige
---------------------------------------------
A policy e estado GLOBAL do Windows, numa chave fixa, sem dono no payload. Ver
POLICY_STALE_OWNERSHIP_GAP e GLOBAL_CERT_POLICY_CONCURRENCY_RISK no relatorio da
fatia 7A. Todos estao presos por teste.
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
    ler_cn_atual: Callable[[], str],
    lancar_guardiao: Callable[[str], int],
    aguardar: Callable[[], None],
) -> ResultadoDaPolicy:
    """Garante que o Chrome vai auto-selecionar o certificado de `cn`.

    As tres primitivas entram por parametro porque as tres precisam de Windows
    real — e sem elas o protocolo inteiro roda em qualquer maquina.

    A espera e pelo CN PEDIDO, nunca pela mera existencia da policy. Isso e o que
    torna seguro trocar de certificado no meio da execucao: a policy do
    certificado anterior ainda esta escrita quando o novo guardiao sobe, e
    conferir so a existencia devolveria True de imediato — o Chrome subiria com o
    certificado errado, autenticando na empresa errada.
    """
    if ler_cn_atual() == cn:
        # ATENCAO — nenhum guardiao e lancado aqui. Ver POLICY_STALE_OWNERSHIP_GAP.
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
