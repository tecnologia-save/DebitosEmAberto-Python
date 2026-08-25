"""A fronteira de entrada da automacao: parametros externos -> input tipado.

A pergunta que este modulo responde: QUAL E O INPUT DE UMA EXECUCAO de
DebitosEmAberto? Resposta: um caminho de planilha. So isso.

O que deliberadamente NAO esta aqui, apesar de existir no processo:

    --log            opcao do adapter local (observabilidade), nao input;
    GEMINI_API_KEY   segredo de ambiente, resolvido pelo runtime;
    URLs do portal   constante da automacao;
    intervalo de 30s regra do portal, nao parametro;
    certificados     descobertos na maquina, nao recebidos;
    CNPJ / empresa   conteudo da planilha, nao parametro da execucao;
    --guard          modo interno de processo elevado, nao execucao do operador.

Nada aqui abre arquivo, le ambiente ou importa pandas, openpyxl, tkinter ou
navegador: a fronteira valida FORMA, nunca conteudo. Se o arquivo existe, se tem
aba 'Empresas', se as colunas estao certas — tudo isso e das proximas fatias, e
so pode ser respondido lendo o disco.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath

# Unica extensao que a automacao consegue processar de ponta a ponta.
#
# A UI desktop oferece .xlsx, .xls e .csv, e `ler_e_ordenar` le os tres. Mas TODA
# escrita passa por openpyxl.load_workbook, que suporta apenas .xlsx/.xlsm — e a
# leitura de .xlsm cai no ramo de CSV e falha. Uma execucao com .xls ou .csv le a
# planilha, comeca, e morre com InvalidFileException na primeira gravacao de
# status. Ver LEGACY_DESKTOP_ONLY no relatorio da fatia 3.
EXTENSAO_ACEITA = ".xlsx"

CAMPOS_CONHECIDOS = frozenset({"planilha"})

_LIMITE_NOME_ECOAVEL = 40


class EntradaInvalida(Exception):
    """A automacao foi chamada com parametros que ela nao consegue aceitar.

    Erro NOSSO, e a mensagem atravessa a fronteira externa — entao ela nunca
    carrega o valor recebido. Um caminho de planilha contem nome de cliente; um
    campo desconhecido contem texto arbitrario de quem chamou.
    """


@dataclass(frozen=True)
class EntradaDebitosEmAberto:
    """O input de uma execucao.

    Um campo so. Nao ha outros parametros a inventar para "parecer completo" —
    tudo o mais que a execucao usa e descoberto, constante ou conteudo.

    NOTA — INOUT_RESOURCE_CONTRACT: a planilha e input E output mutavel; a
    automacao grava status nela durante a execucao. Este tipo carrega apenas a
    REFERENCIA recebida. Quando salvar, se sobrescrever, se copiar, de quem e o
    arquivo e quem limpa — nada disso e decidido aqui.
    """

    planilha: str


def _nome_ecoavel(nome: object) -> str:
    """Um nome de campo so volta na mensagem se for estrutural, nunca arbitrario.

    Sem isto, uma chave desconhecida poderia ser o proprio caminho do cliente.
    """
    texto = str(nome)
    if texto.isidentifier() and len(texto) <= _LIMITE_NOME_ECOAVEL:
        return texto
    return "<campo não nomeável>"


def montar_entrada(parametros: Mapping[str, object]) -> EntradaDebitosEmAberto:
    """Traduz os parametros recebidos para o input tipado da execucao.

    Recebe SOMENTE parametros que pertencem a esta automacao — nunca o payload
    bruto de quem a executa. Extrair os parametros relevantes de um payload e
    responsabilidade do runner, que ainda nao existe.

    Campo desconhecido e RECUSADO, nao ignorado: como a fronteira nao recebe
    metadata externa, um campo a mais so pode ser erro de quem chamou, e um typo
    silenciosamente ignorado vira uma execucao que roda com o parametro errado.
    """
    if not isinstance(parametros, Mapping):
        raise EntradaInvalida("Os parâmetros da execução precisam vir em um mapa.")

    desconhecidos = sorted(set(parametros) - CAMPOS_CONHECIDOS, key=str)
    if desconhecidos:
        nomes = ", ".join(_nome_ecoavel(c) for c in desconhecidos)
        raise EntradaInvalida(f"Parâmetro(s) não reconhecido(s): {nomes}.")

    if "planilha" not in parametros:
        raise EntradaInvalida("O parâmetro 'planilha' é obrigatório.")

    planilha = parametros["planilha"]
    if not isinstance(planilha, str):
        raise EntradaInvalida("O parâmetro 'planilha' precisa ser um caminho em texto.")

    planilha = planilha.strip()
    if not planilha:
        raise EntradaInvalida("O parâmetro 'planilha' está vazio.")

    if PurePath(planilha).suffix.lower() != EXTENSAO_ACEITA:
        raise EntradaInvalida(
            f"A planilha precisa ser um arquivo {EXTENSAO_ACEITA}."
        )

    return EntradaDebitosEmAberto(planilha=planilha)
