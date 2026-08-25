"""Classificacao das recusas que o portal devolve em texto.

A pergunta desta regra: dado somente o que o portal escreveu, esta linha ENCERRA
ou deve ser TENTADA DE NOVO?

Nada aqui abre navegador, faz login, le planilha ou toca a rede — entra texto,
sai classificacao. E por isso que a decisao pode ser testada sem portal.

Tres desfechos possiveis:

    recusa com status proprio  -> permanente, e o motivo vai para a coluna D;
    recusa por palavra         -> permanente nesta execucao, nada e gravado;
    nao reconhecida            -> retentavel.

A assimetria e deliberada: so uma recusa reconhecida por FRASE ESPECIFICA grava
status e encerra a linha de vez. Uma palavra solta nunca encerra nada
permanentemente — na pior hipotese o CNPJ e pulado nesta execucao e volta na
proxima.
"""
from __future__ import annotations

from .domain import remover_acentos


class FalhaPermanente(Exception):
    """Recusa que impede processar este CNPJ — nao retentar.

    Exemplos: procuracao expirada/invalida, CNPJ nao autorizado pelo portal.

    `status_coluna_d`, quando informado, e gravado na coluna D da aba 'Empresas'
    para registrar o motivo na planilha em vez de deixar a linha em branco.

    NOTA — ver o relatorio da fatia 2: isto se comporta menos como falha tecnica
    e mais como DESFECHO ESPERADO do portal. O consumidor a captura para seguir
    normalmente para o proximo CNPJ, sem contar retentativa e sem fechar a
    sessao. Nao alterado nesta fatia: hoje ela atravessa tres frames de
    navegacao, e transformar isso em valor de retorno e mudanca da camada de
    navegacao, nao desta regra.
    """

    def __init__(self, mensagem: str, status_coluna_d: str | None = None):
        super().__init__(mensagem)
        self.status_coluna_d = status_coluna_d


# Palavras que o portal exibe em span.mensagemErro para indicar que o CNPJ nao
# pode ser representado (procuracao inexistente, vencida, CNPJ invalido...).
# Recusas com essas palavras nao devem ser retentadas nesta execucao.
PALAVRAS_RECUSA_PERMANENTE = (
    "procuração", "procuracao", "vencid", "expirad",
    "não possui", "sem procuração", "não encontrad",
    "cnpj inválid", "não autorizado",
)

# "Sua autorizacao como procurador nao permite acesso a este servico" nao casava
# com nenhuma palavra da tupla acima — ela tem "procuracao" e "autorizado", e a
# mensagem traz "procurador" e "autorizacao". O resultado era o loop de espera
# rodar ate estourar os 60s sem classificar a recusa.
STATUS_SEM_AUTORIZACAO = "Procuração sem autorização"

# Recusas com status proprio na coluna D: {trecho da mensagem: status}.
# A comparacao e feita sem acento e em minusculas.
RECUSAS_COM_STATUS = {
    "nao permite acesso a este servico": STATUS_SEM_AUTORIZACAO,
}

# Status da coluna D que encerram a linha sozinhos, sem depender da coluna E: a
# procuracao foi recusada, entao nao ha o que buscar nem em Debitos nem em
# Processos Fiscais. Sem isso a linha voltaria em toda execucao para ser recusada
# de novo, ja que o criterio padrao exige D e E preenchidas.
STATUS_D_TERMINAIS = (STATUS_SEM_AUTORIZACAO,)


def status_da_recusa(mensagem: str) -> str | None:
    """Status para a coluna D quando a recusa e definitiva e conhecida."""
    msg = remover_acentos(mensagem.lower())
    for trecho, status in RECUSAS_COM_STATUS.items():
        if trecho in msg:
            return status
    return None


def recusa_permanente(mensagem: str) -> bool:
    """True se a mensagem do portal caracteriza recusa que nao deve ser retentada.

    ATENCAO — duas palavras da lista alcancam mais do que pretendiam, e o
    comportamento esta PRESERVADO do original (PORTAL_STATUS_POSSIBLE_DEFECT):

    - "não encontrad" foi escrita para "procuracao nao encontrada", mas casa com
      "pagina nao encontrada", um 404 comum de navegacao, que e transitorio;
    - "vencid" casa com "certificado digital vencido", que e problema da SESSAO
      inteira e nao deste CNPJ.

    Nos dois casos nao ha status proprio, entao nada e gravado na coluna D e o
    CNPJ volta a ser pendente na proxima execucao.
    """
    if status_da_recusa(mensagem):
        return True
    msg = remover_acentos(mensagem.lower())
    return any(remover_acentos(p) in msg for p in PALAVRAS_RECUSA_PERMANENTE)


def status_encerra_linha(val_d) -> bool:
    """True se o status ja na coluna D dispensa qualquer processamento da linha.

    Comparacao por IGUALDADE apos normalizar — conter o status nao basta, tem de
    ser o status. E o que impede um texto qualquer escrito a mao na planilha de
    silenciar uma linha por acidente.
    """
    alvo = remover_acentos(str(val_d or "").strip().lower())
    if not alvo:
        return False
    return any(remover_acentos(s.lower()) == alvo for s in STATUS_D_TERMINAIS)
