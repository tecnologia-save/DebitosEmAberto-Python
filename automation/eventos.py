"""Fatos operacionais que a aplicacao emite enquanto executa.

Por que isto existe
-------------------
Uma execucao dura horas. O operador age DURANTE ela, nao depois: quando a
gravacao da planilha falha porque o arquivo esta aberto no Excel, a sessao
continua suja de proposito e o save do proximo CNPJ tenta de novo — entao fechar
o Excel no meio da execucao salva o progresso. Um resumo no fim chega tarde
demais para isso.

Esse e o unico motivo pelo qual existe um seam de eventos. Nao e log, nao e
metrica, nao e telemetria.

O que um evento e
-----------------
Um CODIGO fechado mais os poucos campos estruturados que o fato exige. Nao ha
`mensagem: str` nem `payload: dict`: quem transforma codigo em frase e o adapter
de apresentacao, e ele e o unico que sabe como o operador le.

Isso mantem duas coisas separadas que o `print` misturava: o FATO e a
APRESENTACAO. Ninguem precisa fazer parsing de mensagem para reagir a um evento.

O que um evento NUNCA carrega
-----------------------------
CNPJ, empresa, CN, serial, chave de API, URL, titulo de pagina, caminho da
planilha, numero de processo, valor fiscal, texto bruto do portal ou texto bruto
de exception.

Quando o operador precisa localizar a linha, ele recebe `posicao` — a mesma
posicao opaca de `ItemPendente`. Ela identifica a linha na lista sem copiar
nenhum identificador para o console nem para o arquivo de `--log`.

A unica excecao, e ela e deliberada
-----------------------------------
`tipo_da_falha` carrega o NOME DA CLASSE de uma exception — `PermissionError` —
e nunca a mensagem. E o que diz ao operador o que fazer: `PermissionError`
significa "feche o Excel". Sem isso o evento de save vira um alarme sem acao, e
o comportamento de recuperacao que justificou este seam nao acontece. O codigo
legado ja tomava essa decisao ao gravar `type(e).__name__` no log de erros.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Percurso da lista ─────────────────────────────────────────────────────────
ITEM_INICIADO = "item_iniciado"
ITEM_IGNORADO_SEM_CNPJ = "item_ignorado_sem_cnpj"
ITEM_JA_ENCERRADO = "item_ja_encerrado"
ITEM_JA_CONCLUIDO = "item_ja_concluido"
RETOMADA_PULA_DCTFWEB = "retomada_pula_dctfweb"
RETOMADA_PULA_PROCESSOS = "retomada_pula_processos"

# ── Certificado, policy e sessao ──────────────────────────────────────────────
CERTIFICADO_INICIADO = "certificado_iniciado"
CERTIFICADO_NAO_INSTALADO = "certificado_nao_instalado"
CERTIFICADO_AMBIGUO = "certificado_ambiguo"
CERTIFICADOS_INDISPONIVEIS = "certificados_indisponiveis"
LEITURA_DE_CERTIFICADOS_FALHOU = "leitura_de_certificados_falhou"
POLICY_NAO_CONFIAVEL = "policy_nao_confiavel"
POLICY_PERMANECERA_NA_MAQUINA = "policy_permanecera_na_maquina"
LOGIN_CONCLUIDO = "login_concluido"
LOGIN_FALHOU = "login_falhou"
SESSAO_RECUPERADA_APOS_RECUSA = "sessao_recuperada_apos_recusa"
SESSAO_NAO_RECUPERADA_APOS_RECUSA = "sessao_nao_recuperada_apos_recusa"

# ── Desfechos do CNPJ ─────────────────────────────────────────────────────────
CNPJ_RECUSADO_PELO_PORTAL = "cnpj_recusado_pelo_portal"
ITEM_FALHOU = "item_falhou"
ITEM_ESGOTOU_RETENTATIVAS = "item_esgotou_retentativas"
SITUACAO_FISCAL_NAO_RECONHECIDA = "situacao_fiscal_nao_reconhecida"

# ── O que foi extraido e o que foi registrado ─────────────────────────────────
DEBITOS_REGISTRADOS = "debitos_registrados"
PROCESSOS_REGISTRADOS = "processos_registrados"
SEM_DEBITOS_REGISTRADO = "sem_debitos_registrado"
DEBITOS_NAO_COMPENSAVEIS_REGISTRADO = "debitos_nao_compensaveis_registrado"
SEM_PROCESSOS_REGISTRADO = "sem_processos_registrado"
RECUSA_REGISTRADA = "recusa_registrada"
LINHA_NAO_ENCONTRADA_NA_PLANILHA = "linha_nao_encontrada_na_planilha"
SALVAMENTO_PLANILHA_FALHOU = "salvamento_planilha_falhou"

# ── Repassados das integracoes ────────────────────────────────────────────────
# So a integracao observa estes fatos, e por isso eles chegam ao app dentro do
# RESULTADO (ExtracaoFiscal.avisos) em vez de por callback. O app os traduz.
REDE_NAO_ESTABILIZOU = "rede_nao_estabilizou"
PAGINACAO_NAO_ALTERADA = "paginacao_nao_alterada"

CODIGOS = frozenset({
    ITEM_INICIADO, ITEM_IGNORADO_SEM_CNPJ, ITEM_JA_ENCERRADO, ITEM_JA_CONCLUIDO,
    RETOMADA_PULA_DCTFWEB, RETOMADA_PULA_PROCESSOS,
    CERTIFICADO_INICIADO, CERTIFICADO_NAO_INSTALADO, CERTIFICADO_AMBIGUO,
    CERTIFICADOS_INDISPONIVEIS, LEITURA_DE_CERTIFICADOS_FALHOU,
    POLICY_NAO_CONFIAVEL, POLICY_PERMANECERA_NA_MAQUINA,
    LOGIN_CONCLUIDO, LOGIN_FALHOU,
    SESSAO_RECUPERADA_APOS_RECUSA, SESSAO_NAO_RECUPERADA_APOS_RECUSA,
    CNPJ_RECUSADO_PELO_PORTAL, ITEM_FALHOU, ITEM_ESGOTOU_RETENTATIVAS,
    SITUACAO_FISCAL_NAO_RECONHECIDA,
    DEBITOS_REGISTRADOS, PROCESSOS_REGISTRADOS,
    SEM_DEBITOS_REGISTRADO, DEBITOS_NAO_COMPENSAVEIS_REGISTRADO,
    SEM_PROCESSOS_REGISTRADO, RECUSA_REGISTRADA,
    LINHA_NAO_ENCONTRADA_NA_PLANILHA, SALVAMENTO_PLANILHA_FALHOU,
    REDE_NAO_ESTABILIZOU, PAGINACAO_NAO_ALTERADA,
})

# Avisos das integracoes -> codigo de evento. O app traduz; a integracao nao
# conhece eventos.
AVISO_PARA_CODIGO = {
    "rede não estabilizou dentro do tempo": REDE_NAO_ESTABILIZOU,
    "não foi possível alterar os itens por página": PAGINACAO_NAO_ALTERADA,
}


class CodigoDesconhecido(Exception):
    """Codigo fora do conjunto fechado. Mensagem constante."""


@dataclass(frozen=True)
class EventoOperacional:
    """Um fato, e so os campos que aquele fato exige.

    Todos os campos sao opcionais porque nenhum evento usa todos — e nenhum
    campo existe "por padrao": cada um entrou porque um evento real perderia
    informacao operacional sem ele.
    """

    codigo: str
    posicao: int | None = None
    total: int | None = None
    tentativa: int | None = None
    maximo: int | None = None
    quantidade: int | None = None
    paginas: int | None = None
    # NOME DA CLASSE da exception, nunca a mensagem. Ver o docstring do modulo.
    tipo_da_falha: str | None = None

    def __post_init__(self) -> None:
        if self.codigo not in CODIGOS:
            raise CodigoDesconhecido("Código de evento fora do conjunto conhecido.")
