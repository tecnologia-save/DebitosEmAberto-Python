"""De onde vem o certificado de cada linha — e so isso.

A pergunta que este modulo responde: a aplicacao precisa saber que os
certificados moram no Windows Certificate Store? Resposta: nao. Ela precisa
saber tres coisas — que existe um catalogo, que um nome da coluna C resolve (ou
nao) para um certificado, e qual certificado usar no login.

Por que o seam existe
---------------------
No desktop o catalogo e o Certificate Store, lido por PowerShell, e o "usar o
certificado" acontece por uma policy de registro que faz o Chrome escolher
sozinho. Na plataforma nao havera Store, nem registro, nem UAC: havera um `.pfx`
vindo do cofre, apresentado ao navegador como `client_certificates`. As duas
respostas sao legitimas para a MESMA pergunta, e e a pergunta que mora aqui.

O que este modulo NAO faz
-------------------------
Nao descobre, nao escolhe, nao instala policy e nao abre navegador. Ele tambem
nao inventa tipo: quem diz como um certificado resolvido se parece e
`login.Certificado`, que ja existia e ja e o que o login consome. Um tipo novo
aqui so criaria uma traducao a mais no meio do caminho.

Nada aqui importa Windows, navegador ou plataforma — e por isso um provedor de
teste roda em qualquer sistema operacional.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from automation.domain import ResultadoDaBusca
from automation.login import Certificado


class FalhaAoLerCertificados(Exception):
    """Nao deu para LER o catalogo — diferente de nao haver certificado nenhum.

    A distincao foi feita na fatia 5A e continua valendo para qualquer provedor:
    antes as duas situacoes produziam a mesma saida, e o operador era mandado
    instalar um certificado que ja estava instalado.

    Vive aqui, e nao no adapter do Windows, porque e vocabulario do SEAM: quem
    quer que passe a fornecer certificados precisa poder dizer isto.
    """


@runtime_checkable
class ProvedorDeCertificados(Protocol):
    """O contrato minimo que a aplicacao consome.

    `runtime_checkable` para que um adapter possa PROVAR conformidade num teste.
    Ele confere a presenca dos metodos, e nao as assinaturas — e isso basta: o
    que quebraria de verdade e um provedor sem `resolver`.

    Tres metodos porque sao tres perguntas distintas, e nenhuma delas cabe nas
    outras: preparar o catalogo custa (PowerShell, ou cofre) e acontece uma vez;
    resolver um nome acontece por LINHA; e obter o certificado so faz sentido
    depois de resolvido.
    """

    def carregar(self) -> int:
        """Prepara o catalogo e devolve quantos certificados ha nele.

        Levanta `FalhaAoLerCertificados` quando nao foi possivel ler. Zero e
        resposta valida: significa catalogo vazio, e nao falha.
        """
        ...

    def resolver(self, nome: str) -> ResultadoDaBusca:
        """Procura pelo nome que veio da coluna C daquela linha.

        Os tres desfechos de `ResultadoDaBusca` continuam sendo os mesmos:
        resolvida, ambigua, sem correspondencia. Ambiguidade NAO e erro do
        provedor — e informacao que a aplicacao registra e usa para pular a
        linha, porque escolher no empate seria autenticar na empresa errada.
        """
        ...

    def certificado(self, chave: str) -> Certificado:
        """O certificado ja resolvido, como o login precisa dele.

        `chave` e opaca para a aplicacao: ela so a recebe de `resolver` e a
        devolve aqui. O que ela significa e assunto do provedor.
        """
        ...
