"""EventoOperacional → a linha que o operador lê. Sem imprimir nada.

Os três adapters — `runner.py`, `local.py` e o `main.py` legado — mostram os
mesmos fatos. Três implementações das mesmas frases seriam três oportunidades de
uma delas vazar algo que as outras não vazam, e nenhum teste perceberia.

Este módulo NÃO é aplicação e NÃO é domínio: é apresentação. Ele traduz e
devolve texto. Quem decide imprimir, para onde, e o que fazer com o resultado é
o adapter.

Nenhuma frase pode conter identificador: o evento não carrega nenhum, e há um
teste por código provando que a frase também não inventa um. Quando o operador
precisa achar a linha, ele recebe a POSIÇÃO e abre a planilha nela.
"""
from __future__ import annotations

from automation import eventos
from automation.eventos import EventoOperacional


def frase(e: EventoOperacional) -> str | None:
    """Um fato da aplicação vira a linha que o operador lê. `None` = não mostrar.

    Aqui, e só aqui, o vocabulário volta a ser humano. O app emite CÓDIGOS: ele
    não sabe que existe console, nem `--log`, nem português.

    Nenhuma frase tem CNPJ, empresa ou CN — o app não os envia. Quando o
    operador precisa achar a linha, ele recebe a POSIÇÃO e abre a planilha nela.
    """
    E = eventos
    onde = "" if e.posicao is None else f"linha {e.posicao + 1}"
    de = "" if e.total is None else f"/{e.total}"

    if e.codigo == E.ITEM_INICIADO:
        return f"\n  [{onde}{de}] Processando..."
    if e.codigo == E.ITEM_IGNORADO_SEM_CNPJ:
        return f"  [{onde}{de}] CNPJ inválido/vazio. Ignorando linha."
    if e.codigo == E.ITEM_JA_ENCERRADO:
        return f"    → [{onde}] Status terminal na coluna D. Nada a processar."
    if e.codigo == E.ITEM_JA_CONCLUIDO:
        return f"    → [{onde}] Já totalmente processada. Pulando."
    if e.codigo == E.RETOMADA_PULA_DCTFWEB:
        return "    → Débitos DCTFWeb já concluídos. Fará apenas Processos Fiscais."
    if e.codigo == E.RETOMADA_PULA_PROCESSOS:
        return "    → Processos Fiscais já concluídos. Fará apenas Débitos DCTFWeb."

    if e.codigo == E.CERTIFICADO_INICIADO:
        return f"\n{'═' * 60}\n  Certificado da {onde} em diante\n{'═' * 60}"
    if e.codigo == E.CERTIFICADO_NAO_INSTALADO:
        return (f"  [!] O certificado pedido na {onde} não está instalado nesta "
                "máquina. Abra a planilha nessa linha para ver qual é.")
    if e.codigo == E.CERTIFICADO_AMBIGUO:
        return (f"  [!] O nome de certificado da {onde} corresponde a "
                f"{e.quantidade} certificados instalados.\n"
                "       Escreva na planilha um nome que identifique só um deles.")
    if e.codigo == E.LEITURA_DE_CERTIFICADOS_FALHOU:
        return ("  [!] Não foi possível ler o repositório de certificados desta "
                "máquina.\n"
                "       Isso NÃO significa que não há certificado "
                "instalado — a leitura em si falhou.")
    if e.codigo == E.CERTIFICADOS_INDISPONIVEIS:
        return ("  [!] Nenhum certificado com chave privada, válido e não arquivado,\n"
                "      foi encontrado em Cert:\\CurrentUser\\My.\n"
                "      Instale o certificado no Windows antes de rodar a automação.")
    if e.codigo == E.POLICY_NAO_CONFIAVEL:
        return ("    [!] Policy de auto-seleção não ficou ativa (UAC negado?). "
                "A janela de certificado será resolvida por UI.")
    if e.codigo == E.POLICY_PERMANECERA_NA_MAQUINA:
        return ("    [!] A policy do Chrome já existia e continuará na máquina "
                "depois desta execução.")
    if e.codigo == E.LOGIN_CONCLUIDO:
        return "    [✓] Login no portal concluído."
    if e.codigo == E.LOGIN_FALHOU:
        return f"    [!] Login não autenticou. Pulando a {onde}."

    if e.codigo == E.CNPJ_RECUSADO_PELO_PORTAL:
        return f"    [!] O portal recusou o CNPJ da {onde}. Linha encerrada."
    if e.codigo == E.SESSAO_RECUPERADA_APOS_RECUSA:
        return "    [✓] Sessão mantida — seguindo para o próximo CNPJ deste certificado."
    if e.codigo == E.SESSAO_NAO_RECUPERADA_APOS_RECUSA:
        return "    [!] Sessão não recuperada após a recusa. Fechando o navegador."
    if e.codigo == E.FALHA_AO_ENCERRAR_SESSAO:
        return (f"    [!] O encerramento da sessão falhou ({e.tipo_da_falha}). "
                "A falha original acima é a que importa.")
    if e.codigo == E.ITEM_FALHOU:
        resta = "Reabrindo sessão e retentando" if e.tentativa < e.maximo else "Pulando"
        return (f"    [!] Falha ao processar a {onde} "
                f"(tentativa {e.tentativa}/{e.maximo}). {resta}...")
    if e.codigo == E.ITEM_ESGOTOU_RETENTATIVAS:
        return f"    [!] Máximo de tentativas atingido na {onde}."
    if e.codigo == E.SITUACAO_FISCAL_NAO_RECONHECIDA:
        return (f"    [!] Status de pendências não reconhecido na {onde}. "
                "Nada foi gravado — a linha voltará pendente.")

    if e.codigo == E.DEBITOS_REGISTRADOS:
        if e.quantidade is None:
            return "    [✓] Coluna D → concluída pelo Processo Fiscal."
        return (f"    [✓] {e.quantidade} linha(s) de débito DCTFWeb em "
                f"{e.paginas} página(s) — gravadas, coluna D concluída.")
    if e.codigo == E.PROCESSOS_REGISTRADOS:
        return (f"    [✓] {e.quantidade} linha(s) de processo fiscal em "
                f"{e.paginas} página(s) — gravadas, coluna E concluída.")
    if e.codigo == E.SEM_DEBITOS_REGISTRADO:
        return "    [✓] Sem débitos."
    if e.codigo == E.DEBITOS_NAO_COMPENSAVEIS_REGISTRADO:
        return "    [✓] Débitos não compensáveis."
    if e.codigo == E.SEM_PROCESSOS_REGISTRADO:
        return "    [✓] Sem processos."
    if e.codigo == E.RECUSA_REGISTRADA:
        return "    [✓] Motivo da recusa gravado na planilha."
    if e.codigo == E.LINHA_NAO_ENCONTRADA_NA_PLANILHA:
        return (f"    [!] A {onde} não foi encontrada na planilha para escrita. "
                "O status foi descartado.")
    if e.codigo == E.SALVAMENTO_PLANILHA_FALHOU:
        acao = (" — feche o arquivo no Excel e a próxima gravação recupera."
                if e.tipo_da_falha == "PermissionError" else "")
        return f"    [!] Falha ao salvar a planilha ({e.tipo_da_falha}){acao}"

    if e.codigo == E.REDE_NAO_ESTABILIZOU:
        return "    [!] A página não estabilizou no tempo; a extração seguiu assim mesmo."
    if e.codigo == E.PAGINACAO_NAO_ALTERADA:
        return "    [!] Não foi possível alterar os itens por página."
    return None

class Apresentador:
    """Renderiza eventos no stdout e lembra se a execução abortou.

    `executar` devolve `None` de propósito — não há resumo final. O código de
    saída do processo vem do que o adapter OBSERVOU passar pelo seam, e não de
    uma métrica inventada para preencher um retorno.

    Um adapter que precise de mais — gravar em log, contar, filtrar — herda daqui
    e acrescenta. É o que o entrypoint desktop legado faz.
    """

    def __init__(self, escrever=print) -> None:
        self._escrever = escrever
        self.abortou = False

    def __call__(self, evento: EventoOperacional) -> None:
        if evento.codigo in (eventos.CERTIFICADOS_INDISPONIVEIS,
                             eventos.LEITURA_DE_CERTIFICADOS_FALHOU):
            self.abortou = True
        texto = frase(evento)
        if texto is not None:
            self._escrever(texto)
