"""Entrypoint da plataforma. Adapter FINO.

A plataforma executa este arquivo, na raiz do código. Ele é a única coisa do
projeto que conhece a Save Automation.

    parâmetros do disparo  ->  boundary  ->  config  ->  app  ->  resultado/erro

O que NÃO pode entrar aqui: regra de planilha, certificado, policy, login,
navegador, retry, laço de CNPJ, seletor, JavaScript ou parsing fiscal. Se surgir
a vontade de pôr um `for` que decide algo, o lugar é `automation/app.py` — e
`tests/test_runner.py` reprova quem tentar.

Irmão de `local.py`: os dois chamam `automation.app.executar` e nenhum importa o
outro. Se o local dependesse do adapter da plataforma, rodar na sua máquina
passaria a depender da plataforma.

PUBLICAÇÃO. A plataforma descobre a task pelo decorator do SDK aplicado a
`main`. O contrato não é template: é o do runner histórico desta automação e o
da documentação do próprio SDK instalado no agente. `id` e `retries` são
literais porque o describe estático do SDK lê o decorator como árvore, sem
importar nada, e descarta o que for computado. Nenhum `params` e nenhum
`inputs` são declarados: o único contrato comprovado da entrada é
`ctx.input_file("planilha")`, lido em `executar_no_save`.
"""
from __future__ import annotations

import os
from pathlib import Path

import autohub_sdk as autohub

from automation import (
    app,
    apresentacao_eventos,
    espaco_de_trabalho,
    eventos,
    exclusividade_host,
    maquina,
)
from automation.boundary import EntradaInvalida, montar_entrada
from automation.captcha import ConfigCaptcha, ConfiguracaoInvalida
from automation.exclusividade_host import (
    ExecucaoJaAtivaNoHost,
    FalhaAoVerificarExclusividade,
)
from automation.planilha import PlanilhaIndisponivel, validar_recurso
from certificados_do_cofre import CertificadosDoCofre
from certificados_instalados import CertificadosInstalados

# O alias da credencial do Gemini no cofre. CANONICO, e nao o que uma
# automacao irma esta usando hoje: la existe um `Gemini_KEY` que e sonda de uma
# regressao do resolver da plataforma (ele casa por NOME da credencial em vez de
# por ALIAS do vinculo), e copiar isso para ca propagaria o defeito em vez de
# expo-lo. Se o resolver recusar este alias, o problema e la — e aparece na
# primeira execucao, que e onde tem de aparecer.
ALIAS_DO_GEMINI = "gemini_api_key"

# O nome com que a planilha aparece na aba de Artefatos da execucao. Estavel de
# proposito: o checkpoint faz upsert POR NOME, entao um nome que mudasse a cada
# publicacao encheria a aba de copias em vez de atualizar uma.
NOME_DA_SAIDA = "debitos-em-aberto.xlsx"
TIPO_DA_SAIDA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _config_do_ambiente() -> ConfigCaptcha:
    """O segredo vem do ambiente do processo que a plataforma preparou.

    NÃO há `.env`, nem arquivo local, nem chave embutida: no runtime da
    plataforma nada disso existe, e depender de um deles tornaria a execução
    remota diferente da local por um motivo invisível.

    `validar()` recusa chave ausente com mensagem constante — a chave nunca
    aparece no erro.
    """
    config = ConfigCaptcha(api_key=os.environ.get("GEMINI_API_KEY", ""))
    config.validar()
    return config


def executar(params: dict, emitir_evento=None, provedor_de_certificados=None,
             config: ConfigCaptcha | None = None,
             diretorio_da_execucao: str | None = None) -> dict:
    """Traduz o disparo, roda a automação e devolve o contrato externo.

    Testável sem plataforma: recebe um dicionário comum. É esta função que
    `executar_no_save` chama com a cópia de trabalho, e é ela que os testes
    exercem.

    `emitir_evento` existe para os testes observarem o que atravessou o seam sem
    capturar stdout. Omitido, a apresentação padrão escreve no stdout — que é o
    que a plataforma coleta.

    O `ok` NÃO é um resumo da execução: `app.executar` devolve `None` de
    propósito, e inventar métricas para preencher um retorno seria fabricar
    precisão. Ele diz uma coisa só, observada pelo apresentador: a execução
    chegou ao fim, ou abortou por falta de certificado utilizável.

    `provedor_de_certificados`, `config` e `diretorio_da_execucao` atravessam sem
    ser interpretados. Omitidos, valem o provedor do desktop, a chave do ambiente
    do processo e o diretório de sempre — que é o que mantém este arquivo
    utilizável fora da plataforma.
    """
    entrada = montar_entrada(params)
    validar_recurso(entrada.planilha)
    if config is None:
        config = _config_do_ambiente()

    apresentador = apresentacao_eventos.Apresentador()

    def emissor(evento):
        apresentador(evento)
        if emitir_evento is not None:
            emitir_evento(evento)

    # SINGLE_HOST_CONCURRENCY_CONTRACT, imposto aqui e não pelo app: a
    # exclusividade é assunto de runtime. Antes de `app.executar` porque o
    # primeiro efeito global — a policy do Chrome — acontece lá dentro.
    controle = exclusividade_host.adquirir()
    try:
        app.executar(entrada, config, emitir_evento=emissor,
                     provedor_de_certificados=provedor_de_certificados,
                     diretorio_da_execucao=diretorio_da_execucao)
    finally:
        # O que o provedor pos na maquina sai AINDA SOB o lock: outra execucao
        # so entra depois de os certificados desta terem ido embora.
        encerrar = getattr(provedor_de_certificados, "encerrar", None)
        if encerrar is not None:
            encerrar()
        # Fechar ESTE handle não libera o host por si: se o guardião ainda
        # mantiver o dele, o objeto continua existindo. É intencional.
        exclusividade_host.liberar(controle)

    return {"ok": not apresentador.abortou}


def diretorio_persistente() -> Path:
    """O que esta automacao guarda NESTA maquina entre uma execucao e outra.

    O perfil do navegador e o marcador dos certificados instalados. Fica em
    `%LOCALAPPDATA%/debitos-em-aberto`, fora de qualquer repositorio e fora do
    diretorio do agente. `DEBITOS_DIRETORIO_PERSISTENTE` o troca (os testes usam).
    """
    explicito = os.environ.get("DEBITOS_DIRETORIO_PERSISTENTE")
    if explicito:
        return Path(explicito)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "debitos-em-aberto"


def executar_no_save(ctx, emitir_evento=None) -> dict:
    """A composição da plataforma — e o único lugar onde `ctx` existe.

        ctx.input_file → espaço da execução → ctx.secrets.cert → provedor → app

    O `ctx` MORRE AQUI. A aplicação recebe um caminho de arquivo e um objeto que
    responde três perguntas sobre certificados; ela não sabe que existe uma
    plataforma, e é isso que a deixa rodar pelo `local.py` e ser testada sem
    nada instalado.

    POR QUE A PLANILHA É LIDA DUAS VEZES. O cofre não é enumerável: não dá para
    perguntar "quais certificados existem", só "me dê o chamado X". Então os
    aliases precisam sair da planilha antes de a execução começar — e saem pela
    mesma porta que a execução usa, `planilha.aliases_de_certificado`, para que
    não exista um segundo entendimento de qual coluna guarda o certificado aqui
    na borda. Só as linhas PENDENTES pedem certificado, e quem sabe quais são é a
    aplicação: a borda PERGUNTA, por `app.aliases_necessarios`, e não conhece a
    regra que define uma linha encerrada. Uma segunda cópia dessa regra aqui
    faria o cofre receber pedidos que a execução não faz.

    A CHAVE DO GEMINI vem do cofre, e só quando a aplicação a usa — no primeiro
    login. Uma execução sem nada a processar nunca pede a credencial, e por isso
    não depende de ela existir; quando ela é necessária e falta, a falha sobe
    como veio. O que atravessa para a aplicação é a `ConfigCaptcha` que ela já
    consumia, agora com uma função que obtém a chave: ela não vê `ctx`, nem o
    cofre, nem de onde a chave veio. A revelação continua aqui, dentro dessa
    função — greppável, como o SDK pede.

    O QUE SAI DAQUI para a plataforma: um checkpoint da planilha a cada linha
    gravada, o artefato final e um resultado estruturado pequeno. Nenhum deles
    carrega segredo — a planilha é a mesma que o operador enviou, com as colunas
    de progresso preenchidas.

    QUEM CHAMA é `main`, a task registrada logo abaixo, e ela não acrescenta
    nada: o que a execução faz na plataforma está inteiro aqui.
    """
    segredos = ctx.secrets

    def chave_do_gemini() -> str:
        return segredos.get(ALIAS_DO_GEMINI).reveal()

    config = ConfigCaptcha.sob_demanda(chave_do_gemini)

    anexo = ctx.input_file("planilha")

    with espaco_de_trabalho.abrir(anexo) as espaco:
        # A CÓPIA é o que a execução processa. O anexo que a plataforma montou
        # não é nosso para sobrescrever, e a aplicação grava na planilha o tempo
        # todo — é assim que ela retoma de onde parou.
        de_trabalho = str(espaco.planilha)
        persistente = diretorio_persistente()
        # Os certificados do cofre sao INSTALADOS no Windows durante a execucao
        # (e removidos ao fim): assim o proprio Chrome os apresenta ao gov.br,
        # como no executavel desktop. Entregues como arquivo, eles poem o proxy
        # do Playwright no meio — e o portal desconfia e pede captcha.
        provedor = CertificadosInstalados(
            CertificadosDoCofre(
                app.aliases_necessarios(de_trabalho),
                ctx.secrets.cert,
                espaco.raiz / "certificados",
            ),
            persistente / "certificados-instalados.json",
        )
        # O PERFIL DO NAVEGADOR e o mesmo de uma execucao para a outra, como no
        # executavel desktop. Um perfil novo a cada run, sem cookie nem
        # historico, e o retrato de um navegador automatizado — e o portal pede
        # captcha. Uma execucao por vez neste host e garantida pelo lock.
        navegador = persistente / "navegador"
        navegador.mkdir(parents=True, exist_ok=True)
        def publicar(evento):
            if emitir_evento is not None:
                emitir_evento(evento)
            # O CHECKPOINT SAI AQUI, e nao por relogio. `planilha_gravada` e o
            # instante em que o arquivo acabou de ficar consistente em disco;
            # publicar em intervalos significaria le-lo no meio de uma gravacao,
            # e um arquivo lido pela metade nao levanta erro — ele sobe
            # corrompido. Sem thread, sem concorrencia, sem hash para descobrir
            # se mudou: este evento so acontece quando algo FOI gravado.
            if evento.codigo == eventos.PLANILHA_GRAVADA:
                ctx.checkpoint(NOME_DA_SAIDA, espaco.planilha.read_bytes(),
                               mime=TIPO_DA_SAIDA, kind="spreadsheet")

        try:
            resultado = executar({"planilha": de_trabalho}, emitir_evento=publicar,
                                 provedor_de_certificados=provedor, config=config,
                                 diretorio_da_execucao=str(navegador))
        finally:
            # O perfil fica; o CN do ultimo certificado, que a fiacao grava ao
            # lado dele, nao.
            maquina.descartar_ambiente_do_certificado(str(navegador))

        # A entrega final, ainda DENTRO do espaco: depois do `with` o arquivo
        # nao existe mais. Se a execucao tivesse falhado, nao chegariamos aqui —
        # e o ultimo checkpoint ja estaria publicado, que e justamente para isso
        # que ele serve.
        ctx.artifact(NOME_DA_SAIDA, espaco.planilha.read_bytes(),
                     mime=TIPO_DA_SAIDA, kind="spreadsheet")
        ctx.output(resultado)
        return resultado


@autohub.task(id="AUT-DEBITOS-ABERTO", retries=0)
def main(ctx) -> dict:
    """A task. Entrega o `ctx` à boundary, e nada além disso.

    `AUT-DEBITOS-ABERTO` é o id da TASK, o mesmo do runner histórico desta
    automação. `AUT-0071` é o id da AUTOMAÇÃO na plataforma — os dois não são
    intercambiáveis.

    `retries=0` escrito, e não herdado do default do SDK: a execução autentica
    no portal e grava a planilha, e nada prova que repeti-la inteira seja
    inofensivo. Uma retransmissão automática refaria efeitos externos sem
    ninguém pedir.

    O retorno é o mesmo objeto que `executar_no_save` entrega a `ctx.output`. O
    SDK prefere o output e usa o retorno só na falta dele; os dois dizem a mesma
    coisa, e nenhum é reinterpretado aqui.
    """
    return executar_no_save(ctx)


__all__ = [
    "ALIAS_DO_GEMINI",
    "ConfiguracaoInvalida",
    "EntradaInvalida",
    "ExecucaoJaAtivaNoHost",
    "FalhaAoVerificarExclusividade",
    "PlanilhaIndisponivel",
    "executar",
    "executar_no_save",
    "main",
]


# O agente executa este arquivo como script. `autohub.run` monta o resultado da
# execução — sucesso ou erro — e o emite para a plataforma; nada aqui repete isso.
if __name__ == "__main__":
    autohub.run(main)
