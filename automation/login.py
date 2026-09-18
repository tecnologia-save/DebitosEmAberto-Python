"""A fronteira do login: certificado + configuração → sessão autenticada.

O que esta fronteira acrescenta sobre `fazer_login`
---------------------------------------------------
- os três recursos do navegador viram UM objeto com nomes, em vez de uma tupla
  posicional que todo chamador precisa desempacotar na ordem certa;
- `None` vira um desfecho nomeado;
- a chave do Gemini chega por CONFIGURAÇÃO e é repassada explicitamente, sem
  passar por `os.environ`;
- o fork entra por parâmetro, então a suíte roda sem navegador.

O que ela deliberadamente NÃO faz
---------------------------------
Nada de policy, registro, UAC ou guardião. A fatia 7A provou que o ciclo de vida
da policy é independente do da sessão: quem orquestra os dois é o app, não o
login. Daqui o login só precisa saber UMA coisa — `auto_select_disponivel` — e
essa é a razão de `ResultadoDaPolicy` não aparecer neste módulo.

LOGIN_OUTCOME_INFORMATION_LOSS
------------------------------
São SETE pontos de saída em `fazer_login` e todos devolvem o mesmo `None`.
Navegador que não subiu, certificado recusado, gov.br bloqueado e captcha
esgotado são indistinguíveis do lado de fora. Por isso os desfechos aqui são
dois, e não seis: inventar `CERTIFICADO_REJEITADO` seria fabricar precisão que o
contrato real não tem.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

AUTENTICADO = "autenticado"
NAO_AUTENTICADO = "não autenticado"


class ConfiguracaoInvalida(Exception):
    """Falta configuração para sequer tentar o login.

    Mensagem constante: nunca carrega chave, CN, serial, CNPJ ou caminho.
    """


@dataclass(frozen=True)
class ConfigLogin:
    """O mínimo que o login precisa saber, e nada além.

    `gemini_api_key` é `repr=False` — DEFESA ADICIONAL, não garantia: `asdict()`,
    o acesso direto ao atributo e um log manual continuam expondo o valor.

    `diretorio_perfil` está aqui porque decide QUAL perfil do Chrome é usado, e
    hoje ele é um só para toda execução — ver BROWSER_PROFILE_CONCURRENCY_RISK.
    """

    diretorio_perfil: str
    gemini_api_key: str = field(repr=False, default="")

    def validar(self) -> None:
        if not str(self.diretorio_perfil or "").strip():
            raise ConfiguracaoInvalida("O diretório de perfil do navegador não foi informado.")
        if not (self.gemini_api_key or "").strip():
            raise ConfiguracaoInvalida(
                "A chave da API do Gemini não está configurada; sem ela o login "
                "não consegue passar pelo captcha."
            )


@dataclass(frozen=True)
class Certificado:
    """Qual certificado usar — dado OPERACIONAL da chamada, não configuração.

    Muda a cada troca de certificado dentro da mesma execução, então não pertence
    a `ConfigLogin`.

    DUAS PROCEDÊNCIAS, E ELAS SE EXCLUEM
    ------------------------------------
    `subject_cn` identifica um certificado INSTALADO na máquina: o Chrome o
    escolhe sozinho, via policy, e o material nunca passa por nós. É o caminho
    do desktop.

    `pfx_path`/`pfx_senha` apontam para um arquivo que alguém nos entregou — o
    cofre da plataforma, por exemplo — e que o navegador recebe como
    `client_certificates`. É o caminho da plataforma, onde não há Certificate
    Store para consultar.

    Um certificado do cofre precisa de `subject_cn` VAZIO: o fork decide usar o
    Windows Store por `bool(cert_subject_cn)`, e preencher os dois faria a
    máquina escolher um certificado instalado em vez do que veio no arquivo.

    A senha não entra no `repr`. Isso é DEFESA ADICIONAL, e não garantia: quem
    imprimir o campo direto continua imprimindo. A garantia é não haver ponto
    que o imprima.
    """

    subject_cn: str
    serial: str = ""
    pfx_path: str = ""
    pfx_senha: str = field(default="", repr=False)

    @property
    def do_windows_store(self) -> bool:
        """A mesma regra do fork: `subject_cn` preenchido e certificado INSTALADO."""
        return bool(self.subject_cn and self.subject_cn.strip())


@dataclass
class SessaoReceita:
    """Os três recursos do navegador, com nome.

    O `repr` é seguro por construção: nenhum dos campos é impresso, porque
    `page.url` de uma sessão autenticada carrega identificadores.

    OWNERSHIP — em sucesso, quem chama passa a ser o dono e é quem chama
    `encerrar()`. A fronteira não fecha o que devolveu; ela só limpa o que criou
    e NÃO devolveu.
    """

    playwright: object = field(repr=False)
    contexto: object = field(repr=False)
    pagina: object = field(repr=False)
    _encerrada: bool = field(default=False, repr=False)

    def __repr__(self) -> str:
        return f"SessaoReceita(encerrada={self._encerrada})"

    @property
    def encerrada(self) -> bool:
        return self._encerrada

    def encerrar(self) -> None:
        """Fecha contexto e para o Playwright, nesta ordem. Idempotente.

        Idempotente porque o dono pode encerrar num `finally` que roda depois de
        um caminho que já encerrou — e um double-close silencioso mascarado por
        `except` seria pior do que a checagem explícita.
        """
        if self._encerrada:
            return
        self._encerrada = True
        for recurso, metodo in ((self.contexto, "close"), (self.playwright, "stop")):
            fechar = getattr(recurso, metodo, None)
            if fechar is not None:
                fechar()


@dataclass(frozen=True)
class ResultadoDoLogin:
    """Autenticou ou não. Dois desfechos, porque só dois são observáveis."""

    situacao: str
    sessao: SessaoReceita | None = None

    @property
    def autenticado(self) -> bool:
        return self.situacao == AUTENTICADO


def autenticar(
    certificado: Certificado,
    config: ConfigLogin,
    auto_select_disponivel: bool,
    fazer_login: Callable[..., object | None],
) -> ResultadoDoLogin:
    """Autentica no portal da Receita com o certificado indicado.

    `auto_select_disponivel` é TUDO o que o login precisa saber sobre a policy:
    posso confiar que o Chrome escolhe o certificado sozinho, ou o fallback de
    janela nativa vai ser necessário? Quem limpa a policy, se há guardião e de
    quem é o registro não são assunto daqui.

    `fazer_login` entra por parâmetro — é a fronteira externa inteira, e é o que
    permite testar este contrato sem navegador.

    Bug nosso sobe. Não há tradução de exception aqui: `fazer_login` já engole as
    falhas externas internamente e sinaliza por `None`, e envolver isso num
    `except` largo só esconderia os nossos.
    """
    config.validar()

    recursos = fazer_login(
        cert_subject_cn=certificado.subject_cn,
        cert_serial=certificado.serial,
        # `None`, e não string vazia: o fork trata ausência por falsidade, e
        # mandar `""` significa a mesma coisa — mas `None` é o que ele declara.
        # Com `subject_cn` preenchido (desktop) estes dois chegam vazios e o
        # ramo do Windows Store é o mesmo de sempre.
        cert_pfx_path=certificado.pfx_path or None,
        cert_pfx_passphrase=certificado.pfx_senha or None,
        policy_ok=auto_select_disponivel,
        project_dir=config.diretorio_perfil,
        gemini_api_key=config.gemini_api_key,
    )

    if recursos is None:
        return ResultadoDoLogin(NAO_AUTENTICADO)

    playwright, contexto, pagina = recursos
    return ResultadoDoLogin(
        AUTENTICADO, SessaoReceita(playwright=playwright, contexto=contexto, pagina=pagina)
    )
