"""Adapter de certificado da plataforma: o cofre responde o que o Store respondia.

BOUNDARY. Este arquivo NAO pertence a `automation/` de proposito: ele existe
para que a aplicacao continue sem saber que existe uma plataforma. Ela pede
"o certificado chamado X" ao seam de `automation.certificados`, e quem responde
aqui e o cofre.

POR QUE ELE NAO IMPORTA O SDK
-----------------------------
O resolvedor entra por PARAMETRO — um callable que recebe o nome e devolve algo
com `.path` e `.password`. Na plataforma quem o fornece e `ctx.secrets.cert`; na
suite e uma funcao de tres linhas. Sem isso, testar este adapter exigiria a
plataforma inteira, e o SDK acabaria importado num modulo que so precisa copiar
arquivo.

O CONTRATO DO COFRE, PROVADO E NAO PRESUMIDO
--------------------------------------------
`ctx.secrets.cert(nome)` devolve um objeto com `path` (o `.pfx` ja baixado) e
`password`. Isso esta no SDK instalado no agente, e e o mesmo contrato que outra
automacao ja exerceu numa execucao real. O que NAO esta provado — e por isso nao
e usado aqui — e como o cofre distingue "credencial inexistente" de "cofre
indisponivel": as duas chegam como excecao, e classificar por texto de mensagem
seria adivinhar.

O ALIAS E EXATO
---------------
O valor da coluna C e o alias da credencial, comparado apos `strip()` e mais
nada. O match aproximado do Windows existe porque o Store devolve subjects
verbosos; o cofre devolve o alias que alguem cadastrou. Aproximar ali
transformaria um erro de digitacao em autenticacao na empresa errada.

O ARQUIVO E COPIADO
-------------------
O SDK materializa o `.pfx` num diretorio COMPARTILHADO e sem limpeza
(`%TEMP%/autohub-certs/<nome>.pfx`). Duas execucoes no mesmo host dividem esse
chao, e nada apaga nada. Aqui o arquivo e copiado para o diretorio DESTA
execucao, que morre com ela — e o que sai deste modulo aponta para a copia.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path

from automation.domain import ResultadoDaBusca
from automation.login import Certificado

# O que o cofre devolve: qualquer objeto com estes dois atributos.
ResolvedorDoCofre = Callable[[str], object]

CRITERIO = "alias do cofre"


class CertificadosDoCofre:
    """Provedor de certificados para o caminho da plataforma.

    Satisfaz `automation.certificados.ProvedorDeCertificados` por ter os metodos
    — o protocolo e estrutural, e nao ha heranca a declarar.
    """

    def __init__(self, nomes: Iterable[str], resolver_do_cofre: ResolvedorDoCofre,
                 destino: str | Path) -> None:
        # Os nomes vem da coluna C, ja lidos por quem montou a execucao. O cofre
        # nao e enumeravel: nao da para perguntar "quais certificados existem",
        # so "me de o certificado chamado X". Entao a lista precisa chegar.
        self._nomes = tuple(dict.fromkeys(
            n.strip() for n in nomes if n and n.strip()))
        self._resolver = resolver_do_cofre
        self._destino = Path(destino)
        self._catalogo: dict[str, Certificado] = {}

    def carregar(self) -> int:
        """Pede ao cofre cada alias DISTINTO da planilha. Devolve quantos vieram.

        Um alias que nao resolve NAO derruba a execucao: ele simplesmente nao
        entra no catalogo, e as linhas que o pedem sao puladas com o mesmo evento
        de sempre — o operador ve "certificado nao instalado" e corrige a
        planilha ou cadastra a credencial.

        LIMITE CONHECIDO: se o cofre inteiro estiver fora, TODOS os aliases
        falham e a execucao relata cada linha como certificado indisponivel. O
        SDK sinaliza as duas situacoes com excecao, e distingui-las exigiria ler
        texto de mensagem. Fica registrado em vez de adivinhado.
        """
        self._destino.mkdir(parents=True, exist_ok=True)
        for indice, nome in enumerate(self._nomes):
            material = self._obter(nome, indice)
            if material is not None:
                self._catalogo[nome] = material
        return len(self._catalogo)

    def resolver(self, nome: str) -> ResultadoDaBusca:
        """Alias EXATO, depois de `strip()`. Sem aproximacao."""
        chave = (nome or "").strip()
        if chave in self._catalogo:
            return ResultadoDaBusca(chave=chave, criterio=CRITERIO)
        return ResultadoDaBusca()

    def certificado(self, chave: str) -> Certificado:
        return self._catalogo[chave]

    def itens(self) -> dict[str, Certificado]:
        """O catálogo carregado, por alias. Cópia: quem recebe não altera este."""
        return dict(self._catalogo)

    # ── o unico ponto que fala com o cofre ───────────────────────────────────

    def _obter(self, nome: str, indice: int) -> Certificado | None:
        try:
            credencial = self._resolver(nome)
        except Exception:  # noqa: BLE001 — ver docstring de `carregar`
            # A CLASSE tambem nao e registrada: ela nao muda o que a execucao
            # faz, e a mensagem do SDK carrega o nome da credencial dentro.
            return None

        origem = getattr(credencial, "path", None)
        senha = getattr(credencial, "password", None)
        if not origem or senha is None:
            return None

        # O nome do arquivo e um INDICE, e nao o alias: o alias e o nome do
        # certificado de um cliente, e um caminho vai parar em log de excecao,
        # em stack trace e em listagem de diretorio.
        copia = self._destino / f"{indice}.pfx"
        try:
            shutil.copyfile(origem, copia)
            os.chmod(copia, 0o600)
        except OSError:
            return None

        # `subject_cn` VAZIO nao e esquecimento: e o que faz o navegador usar o
        # arquivo em vez de procurar um certificado instalado na maquina.
        return Certificado(subject_cn="", serial="",
                           pfx_path=str(copia), pfx_senha=senha)
