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

PUBLICAÇÃO. A plataforma descobre a automação por um decorator do SDK, aplicado
a `main`. Ele NÃO vem escrito aqui de propósito: cada automação declara os
próprios parâmetros, e um decorator herdado sem querer seria uma declaração
falsa — que não se vê. Ao publicar, descomente e preencha:

    # import autohub
    #
    # @autohub.task(id="AUT-XXXX", params=[...], inputs=[...])
    # def main(ctx):
    #     return executar(ctx.params)
"""
from __future__ import annotations

import os

from automation import app, apresentacao_eventos
from automation.boundary import EntradaInvalida, montar_entrada
from automation.captcha import ConfigCaptcha, ConfiguracaoInvalida
from automation.planilha import PlanilhaIndisponivel, validar_recurso


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


def executar(params: dict, emitir_evento=None) -> dict:
    """Traduz o disparo, roda a automação e devolve o contrato externo.

    Testável sem plataforma: recebe um dicionário comum. É esta função que
    `main` chama depois de extrair `ctx.params`, e é ela que os testes exercem.

    `emitir_evento` existe para os testes observarem o que atravessou o seam sem
    capturar stdout. Omitido, a apresentação padrão escreve no stdout — que é o
    que a plataforma coleta.

    O `ok` NÃO é um resumo da execução: `app.executar` devolve `None` de
    propósito, e inventar métricas para preencher um retorno seria fabricar
    precisão. Ele diz uma coisa só, observada pelo apresentador: a execução
    chegou ao fim, ou abortou por falta de certificado utilizável.
    """
    entrada = montar_entrada(params)
    validar_recurso(entrada.planilha)
    config = _config_do_ambiente()

    apresentador = apresentacao_eventos.Apresentador()

    def emissor(evento):
        apresentador(evento)
        if emitir_evento is not None:
            emitir_evento(evento)

    app.executar(entrada, config, emitir_evento=emissor)

    return {"ok": not apresentador.abortou}


__all__ = ["ConfiguracaoInvalida", "EntradaInvalida", "PlanilhaIndisponivel", "executar"]
