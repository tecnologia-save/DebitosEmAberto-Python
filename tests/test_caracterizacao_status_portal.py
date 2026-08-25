"""Caracterizacao da classificacao de recusas do portal COMO ELA E HOJE.

Escrito ANTES de extrair. Nenhum teste abre navegador, faz login, toca planilha
ou acessa o portal: as funcoes recebem texto e devolvem classificacao.

Todas as mensagens sao ficticias ou sanitizadas — nenhum CNPJ, nome de empresa
ou texto capturado de um cliente real.

A pergunta que a fatia precisa responder: "dado somente o texto que o portal
devolveu, sei se a linha encerra ou deve ser tentada de novo?"
"""
import pytest

import main

STATUS_SEM_AUTORIZACAO = "Procuração sem autorização"


# ── Recusa COM status proprio: a unica que encerra a linha de vez ─────────────

@pytest.mark.parametrize(
    "mensagem",
    [
        "Sua autorização como procurador não permite acesso a este serviço",
        "Sua autorizacao como procurador nao permite acesso a este servico",
        "SUA AUTORIZAÇÃO COMO PROCURADOR NÃO PERMITE ACESSO A ESTE SERVIÇO",
        "  não permite acesso a este serviço  ",
        "Erro: não permite acesso a este serviço (cod. 0000)",
    ],
)
def test_recusa_com_status_proprio(mensagem):
    """Caixa, acento e espaco em volta nao mudam a classificacao."""
    assert main._erro_permanente(mensagem) is True
    assert main._status_erro_permanente(mensagem) == STATUS_SEM_AUTORIZACAO


def test_o_status_proprio_existe_porque_as_palavras_nao_bastavam():
    """A mensagem diz "procurador" e "autorizacao"; a lista de palavras tem
    "procuracao" e "autorizado". Nenhuma casa. Sem a entrada explicita, a recusa
    ficava sem classificacao e o loop de espera estourava."""
    assert main._erro_permanente("procurador") is False
    assert main._erro_permanente("autorização") is False


# ── Recusas permanentes SEM status proprio ───────────────────────────────────

@pytest.mark.parametrize(
    ("mensagem", "palavra"),
    [
        ("Procuração vencida", "vencid"),
        ("procuracao expirada", "expirad"),
        ("O CNPJ não possui procuração eletrônica", "não possui"),
        ("Contribuinte sem procuração para este serviço", "sem procuração"),
        ("Documento não encontrado", "não encontrad"),
        ("CNPJ inválido para representação", "cnpj inválid"),
        ("Contribuinte não autorizado", "não autorizado"),
        ("PROCURAÇÃO INEXISTENTE", "procuração"),
        ("procuracao inexistente", "procuracao"),
    ],
)
def test_recusa_permanente_por_palavra(mensagem, palavra):
    assert main._erro_permanente(mensagem) is True
    assert main._status_erro_permanente(mensagem) is None, "nada e gravado na coluna D"
    assert palavra in main._PALAVRAS_ERRO_PERMANENTE


# ── Retentavel: tudo que a regra NAO reconhece ───────────────────────────────

@pytest.mark.parametrize(
    "mensagem",
    [
        "Erro interno do servidor. Tente novamente.",
        "Serviço temporariamente indisponível",
        "Tempo de sessão esgotado",
        "",
        "   ",
        "procurador",
        "autorização",
        "procura",
        "texto totalmente desconhecido",
    ],
)
def test_texto_desconhecido_ou_parcial_e_retentavel(mensagem):
    """Nao reconhecido = retentavel. A regra nao chuta permanencia."""
    assert main._erro_permanente(mensagem) is False
    assert main._status_erro_permanente(mensagem) is None


@pytest.mark.parametrize(
    "mensagem",
    ["Você está usando um sistema automatizado", "Acesso bloqueado"],
)
def test_anti_bot_nao_e_classificado_como_permanente(mensagem):
    """O anti-bot e tratado ANTES, inline na navegacao, e e retentavel. Aqui so
    se registra que esta regra nao o reivindica."""
    assert main._erro_permanente(mensagem) is False


# ── Status ja gravado na coluna D encerra a linha ────────────────────────────

@pytest.mark.parametrize(
    "valor_d",
    [
        "Procuração sem autorização",
        "procuracao sem autorizacao",
        "  PROCURAÇÃO SEM AUTORIZAÇÃO  ",
        "Procuracao Sem Autorizacao",
    ],
)
def test_status_terminal_encerra_a_linha(valor_d):
    assert main._status_encerra_linha(valor_d) is True


@pytest.mark.parametrize("valor_d", ["", "   ", None, 0, "Sem débitos", "Concluído"])
def test_status_nao_terminal_nao_encerra(valor_d):
    """Vazio, ausente ou qualquer outro status: a linha segue o criterio normal
    (D e E preenchidas)."""
    assert main._status_encerra_linha(valor_d) is False


def test_status_terminal_e_comparado_por_igualdade_e_nao_por_trecho():
    """Conter o status nao basta — tem de SER o status."""
    assert main._status_encerra_linha("Procuração sem autorização extra") is False
    assert main._status_encerra_linha("sem autorização") is False


def test_o_status_gravado_e_o_status_que_encerra():
    """O elo que fecha o ciclo: o valor escrito na coluna D por uma recusa e
    exatamente o valor que faz a linha ser pulada na proxima execucao."""
    status = main._status_erro_permanente("não permite acesso a este serviço")
    assert status in main._STATUS_D_TERMINAIS
    assert main._status_encerra_linha(status) is True


# ── FalhaPermanente: o que ela carrega hoje ──────────────────────────────────

def test_falha_permanente_carrega_status_opcional():
    sem = main.FalhaPermanente("recusa qualquer")
    com = main.FalhaPermanente("recusa conhecida", status_coluna_d=STATUS_SEM_AUTORIZACAO)

    assert sem.status_coluna_d is None
    assert com.status_coluna_d == STATUS_SEM_AUTORIZACAO
    assert str(com) == "recusa conhecida"


def test_falha_permanente_e_capturavel_separadamente_de_erro_tecnico():
    """O consumidor distingue as duas coisas: FalhaPermanente pula o CNPJ sem
    contar retentativa; qualquer outra excecao entra no contador."""
    assert issubclass(main.FalhaPermanente, Exception)
    assert not issubclass(main.FalhaPermanente, (ValueError, RuntimeError, OSError))


# ── PORTAL_STATUS_POSSIBLE_DEFECT ────────────────────────────────────────────

@pytest.mark.parametrize(
    "mensagem",
    ["Página não encontrada", "Serviço não encontrado no momento"],
)
def test_defeito_erro_de_navegacao_vira_recusa_permanente(mensagem):
    """PORTAL_STATUS_POSSIBLE_DEFECT: "não encontrad" foi escrito para
    "procuração não encontrada", mas casa com um 404 comum de navegacao — que e
    transitorio. Caracterizado, nao corrigido.

    O dano e limitado: sem status proprio, nada e gravado na coluna D, entao a
    linha volta a ser pendente na proxima execucao.
    """
    assert main._erro_permanente(mensagem) is True
    assert main._status_erro_permanente(mensagem) is None, "por isso e recuperavel"


def test_defeito_problema_de_sessao_vira_recusa_deste_cnpj():
    """PORTAL_STATUS_POSSIBLE_DEFECT: "vencid" tambem casa com certificado
    vencido — um problema da SESSAO INTEIRA, nao deste CNPJ. A automacao seguiria
    para o proximo CNPJ como se so este tivesse sido recusado."""
    assert main._erro_permanente("Certificado digital vencido") is True
    assert main._status_erro_permanente("Certificado digital vencido") is None


def test_a_unica_classificacao_que_encerra_de_vez_e_por_frase_exata():
    """Contrapeso aos dois defeitos acima: so `_ERROS_COM_STATUS` grava na coluna
    D, e ele exige uma frase inteira e especifica. Nenhuma palavra solta consegue
    encerrar uma linha permanentemente."""
    assert len(main._ERROS_COM_STATUS) == 1
    trecho = next(iter(main._ERROS_COM_STATUS))
    assert len(trecho.split()) >= 6, "frase, nao palavra"
