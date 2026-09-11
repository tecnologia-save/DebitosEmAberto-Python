"""A semantica do retry de CNPJ: o que retenta, o que sobe, e o que mudou.

RETRY_SEMANTIC_CHANGE (fatia 11)
--------------------------------
Ate o commit `ccb237a` o laco capturava `except Exception`. QUALQUER falha que
atravessasse a unidade de CNPJ — inclusive um `TypeError` nosso — fechava a
sessao, consumia retentativa e reprocessava a mesma linha. O bug nunca chegava a
ninguem: o log dizia "falha ao processar", como se o portal estivesse fora do ar.

O comportamento antigo esta CARACTERIZADO em `ccb237a`, e este arquivo era, ate
aquele commit, o registro do ANTES. Cada teste abaixo diz o que afirmava antes e
o que afirma agora — a mudanca foi deliberada e autorizada, e nao um teste
reescrito como se o passado nao tivesse existido.

O criterio novo, e ele cabe numa frase: retenta o que uma segunda tentativa pode
resolver.

Nenhum teste abre navegador, portal, Gemini, registro ou PowerShell. CNPJs e
certificados ficticios.
"""
import ast
import pathlib

import pytest
from casos_certificado import provedor_de

from automation import app, eventos, navegador
from automation.captcha import ConfigCaptcha, ConfiguracaoInvalida
from automation.login import AUTENTICADO, ResultadoDoLogin
from automation.planilha import ItemPendente

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ConfigCaptcha(api_key="AIzaSy-SENTINELA-FICTICIA-0000")
CNPJ = "11111111000191"
CERTS = {"cert": {"subject_cn": "ALFA FICTICIA:11111111000191", "serial": "0A01"}}


class SessaoFalsa:
    def __init__(self):
        self.pagina = "page"
        self.encerrada = False

    def encerrar(self):
        self.encerrada = True


class PlanilhaInerte:
    def __init__(self):
        self.estado = {}

    def precisa_gravar(self):
        return False

    def descartar(self):
        pass


def percorrer(monkeypatch, falha, itens=1):
    """Roda o laco com a unidade de CNPJ levantando `falha`.

    Devolve (eventos, tentativas, sessoes): quantas vezes cada CNPJ foi
    processado e quantas sessoes foram abertas distinguem "retentou" de "subiu".
    """
    tentativas = []
    sessoes = []

    def processar(execucao, item):
        tentativas.append(item.cnpj)
        raise falha

    monkeypatch.setattr(app, "_processar_item", processar)
    monkeypatch.setattr(app.navegador, "encerrar_no_portal", lambda page: None)
    monkeypatch.setattr(
        app.maquina, "abrir_sessao",
        lambda c, a, k: sessoes.append(1) or ResultadoDoLogin(AUTENTICADO, SessaoFalsa()),
    )

    emitidos = []
    execucao = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, emitidos.append)
    execucao.provedor = provedor_de(CERTS)
    execucao.certificado_atual = "CERT"

    app._percorrer(execucao, [
        ItemPendente(posicao=n, cnpj=f"{n + 11111111000191}", certificado="CERT", linha=n + 2)
        for n in range(itens)
    ])
    return emitidos, tentativas, sessoes


# ── O que RETENTA ─────────────────────────────────────────────────────────────

def test_falha_do_navegador_retenta(monkeypatch):
    """A familia que o mecanismo existe para atender.

    O navegador caiu, a pagina morreu, o seletor expirou: uma sessao nova pode
    resolver, e por isso vale a segunda tentativa.
    """
    emitidos, tentativas, sessoes = percorrer(
        monkeypatch, navegador.FalhaDoNavegador("o navegador falhou")
    )
    codigos = [e.codigo for e in emitidos]

    assert len(tentativas) == 2, "o MESMO CNPJ foi processado duas vezes"
    assert len(sessoes) == 2, "a sessao foi fechada e reaberta entre as tentativas"
    assert codigos.count(eventos.ITEM_FALHOU) == 2
    assert eventos.ITEM_ESGOTOU_RETENTATIVAS in codigos


def test_representacao_nao_concluida_retenta(monkeypatch):
    """Anti-bot esgotado e representacao nao confirmada sao condicao da SESSAO,
    e nao do CNPJ. Um login novo pode resolver — comportamento da 9A, preservado.
    """
    _, tentativas, sessoes = percorrer(monkeypatch, app._RepresentacaoNaoConcluida())

    assert len(tentativas) == 2
    assert len(sessoes) == 2


def test_o_desfecho_nao_representado_chega_ao_retry_pelo_caminho_real(monkeypatch):
    """A cadeia inteira, sem dublê no meio: `representar` devolve ANTI_BOT_ESGOTADO
    -> `_processar_item` levanta o sinal interno -> o laco retenta."""
    from automation.representacao import ANTI_BOT_ESGOTADO, ResultadoDaRepresentacao

    class Planilha(PlanilhaInerte):
        def retomada(self, caminho, cnpj, encerra_linha):
            from automation.planilha import RetomadaDaLinha

            return RetomadaDaLinha(False, False, encerrada=False)

    sessoes = []
    monkeypatch.setattr(app.navegador, "encerrar_no_portal", lambda page: None)
    monkeypatch.setattr(
        app.maquina, "abrir_sessao",
        lambda c, a, k: sessoes.append(1) or ResultadoDoLogin(AUTENTICADO, SessaoFalsa()),
    )
    monkeypatch.setattr(app.representacao, "representar",
                        lambda *a, **k: ResultadoDaRepresentacao(ANTI_BOT_ESGOTADO))

    emitidos = []
    execucao = app._Execucao(Planilha(), "p.xlsx", CONFIG, emitidos.append)
    execucao.provedor = provedor_de(CERTS)
    execucao.certificado_atual = "CERT"

    app._percorrer(execucao, [ItemPendente(posicao=0, cnpj=CNPJ, certificado="CERT", linha=2)])

    assert len(sessoes) == 2, "retentou com sessao nova"
    assert [e.codigo for e in emitidos].count(eventos.ITEM_FALHOU) == 2


# ── O que NAO retenta mais ────────────────────────────────────────────────────

@pytest.mark.parametrize("bug", [
    TypeError("bug nosso"),
    AttributeError("bug nosso"),
    KeyError("bug nosso"),
    NameError("bug nosso"),
])
def test_bug_nosso_nao_retenta_e_chega_ao_caller(monkeypatch, bug):
    """ANTES (`ccb237a`): duas tentativas, sessao reaberta, e o bug sumia.
    AGORA: uma tentativa, e o bug aparece como o que e.
    """
    with pytest.raises(type(bug)):
        percorrer(monkeypatch, bug)


@pytest.mark.parametrize("bug", [TypeError("bug"), AttributeError("bug"),
                                 KeyError("bug"), NameError("bug")])
def test_bug_nosso_nao_gasta_uma_segunda_tentativa(monkeypatch, bug):
    """A prova de que o CNPJ nao foi reprocessado: uma passagem so."""
    tentativas = []

    def processar(execucao, item):
        tentativas.append(item.cnpj)
        raise bug

    monkeypatch.setattr(app, "_processar_item", processar)
    monkeypatch.setattr(app.navegador, "encerrar_no_portal", lambda page: None)
    monkeypatch.setattr(app.maquina, "abrir_sessao",
                        lambda c, a, k: ResultadoDoLogin(AUTENTICADO, SessaoFalsa()))

    execucao = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, None)
    execucao.provedor = provedor_de(CERTS)
    execucao.certificado_atual = "CERT"

    with pytest.raises(type(bug)):
        app._percorrer(execucao, [ItemPendente(posicao=0, cnpj=CNPJ, certificado="CERT", linha=2)])

    assert tentativas == [CNPJ], "uma vez, e para"


def test_bug_nosso_nao_emite_evento_enganoso(monkeypatch):
    """§17: sem retry, nao pode existir "item falhou, tentando de novo"."""
    emitidos = []

    def processar(execucao, item):
        raise TypeError("bug nosso")

    monkeypatch.setattr(app, "_processar_item", processar)
    monkeypatch.setattr(app.navegador, "encerrar_no_portal", lambda page: None)
    monkeypatch.setattr(app.maquina, "abrir_sessao",
                        lambda c, a, k: ResultadoDoLogin(AUTENTICADO, SessaoFalsa()))

    execucao = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, emitidos.append)
    execucao.provedor = provedor_de(CERTS)
    execucao.certificado_atual = "CERT"

    with pytest.raises(TypeError):
        app._percorrer(execucao, [ItemPendente(posicao=0, cnpj=CNPJ, certificado="CERT", linha=2)])

    codigos = [e.codigo for e in emitidos]
    assert eventos.ITEM_FALHOU not in codigos
    assert eventos.ITEM_ESGOTOU_RETENTATIVAS not in codigos


def test_configuracao_invalida_nao_retenta(monkeypatch):
    """Uma chave ausente continua ausente na segunda tentativa.

    ANTES: gastava as duas e pulava a linha em silencio.
    """
    with pytest.raises(ConfiguracaoInvalida):
        percorrer(monkeypatch, ConfiguracaoInvalida("configuração ausente"))


def test_falha_do_adapter_de_eventos_nao_retenta(monkeypatch):
    """O caso mais caro do comportamento antigo.

    Um bug no adapter de apresentacao fazia o app refazer login, representacao e
    captcha — pagando Gemini por um erro de `print`. ADAPTER_FAILURE nao e falha
    do portal.
    """
    def processar(execucao, item):
        execucao.emitir(eventos.ITEM_JA_CONCLUIDO, posicao=item.posicao)

    sessoes = []

    def emissor(evento):
        if evento.codigo == eventos.ITEM_JA_CONCLUIDO:
            raise AttributeError("bug no adapter de apresentação")

    monkeypatch.setattr(app, "_processar_item", processar)
    monkeypatch.setattr(app.navegador, "encerrar_no_portal", lambda page: None)
    monkeypatch.setattr(
        app.maquina, "abrir_sessao",
        lambda c, a, k: sessoes.append(1) or ResultadoDoLogin(AUTENTICADO, SessaoFalsa()),
    )

    execucao = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, emissor)
    execucao.provedor = provedor_de(CERTS)
    execucao.certificado_atual = "CERT"

    with pytest.raises(AttributeError, match="bug no adapter"):
        app._percorrer(execucao, [ItemPendente(posicao=0, cnpj=CNPJ, certificado="CERT", linha=2)])

    assert len(sessoes) == 1, "nenhum login novo foi pago"


# ── §14 · o mecanismo em si nao mudou ─────────────────────────────────────────

def test_o_maximo_continua_dois(monkeypatch):
    _, tentativas, _ = percorrer(monkeypatch, navegador.FalhaDoNavegador("falhou"))

    assert len(tentativas) == 2
    assert app.MAX_TENTATIVAS_POR_ITEM == 2


def test_o_contador_continua_sendo_por_cnpj(monkeypatch):
    """Dois CNPJs falhando nao somam num contador unico."""
    _, tentativas, _ = percorrer(monkeypatch, navegador.FalhaDoNavegador("falhou"), itens=2)

    assert len(tentativas) == 4
    assert len(set(tentativas)) == 2


def test_o_proximo_cnpj_ainda_e_tentado_depois_de_esgotar(monkeypatch):
    _, tentativas, _ = percorrer(monkeypatch, navegador.FalhaDoNavegador("falhou"), itens=2)

    assert tentativas.count(tentativas[0]) == 2
    assert len(set(tentativas)) == 2, "o segundo CNPJ nao foi pulado"


# ── §15 · cleanup ─────────────────────────────────────────────────────────────

def test_bug_nosso_nao_impede_o_cleanup_final(monkeypatch, tmp_path):
    """O `finally` externo continua liberando sessao e planilha — e nao depende
    do handler de retry para isso."""
    from planilhas_sinteticas import criar_planilha

    from automation.boundary import EntradaDebitosEmAberto

    fechadas = []

    class Sessao(SessaoFalsa):
        def encerrar(self):
            fechadas.append("sessao")

    class Planilha(PlanilhaInerte):
        def abrir(self, caminho):
            pass

        def mapa_status(self, caminho):
            return {}

        def retomada(self, caminho, cnpj, encerra_linha):
            from automation.planilha import RetomadaDaLinha

            return RetomadaDaLinha(False, False, encerrada=False)

        def descartar(self):
            fechadas.append("planilha")

    monkeypatch.setattr(app.certificados_windows, "descobrir", lambda: (CERTS, 0))
    monkeypatch.setattr(app, "SessaoPlanilha", Planilha)
    monkeypatch.setattr(app.maquina, "garantir_policy_do_windows",
                        lambda cn, nossa=False: __import__(
                            "automation.policy_certificado", fromlist=["x"]
                        ).ResultadoDaPolicy("ativada", tem_guardiao=True))
    monkeypatch.setattr(app.maquina, "abrir_sessao",
                        lambda c, a, k: ResultadoDoLogin(AUTENTICADO, Sessao()))
    monkeypatch.setattr(app.navegador, "encerrar_no_portal", lambda page: None)
    monkeypatch.setattr(app, "_processar_item",
                        lambda ex, it: (_ for _ in ()).throw(TypeError("bug nosso")))

    caminho = str(criar_planilha(tmp_path / "base.xlsx"))
    with pytest.raises(TypeError, match="bug nosso"):
        app.executar(EntradaDebitosEmAberto(planilha=caminho), CONFIG)

    assert "sessao" in fechadas
    assert "planilha" in fechadas


# ── §20 · §21 · o handler, na fonte ───────────────────────────────────────────

def _laco():
    arvore = ast.parse((RAIZ / "automation" / "app.py").read_text(encoding="utf-8"))
    return next(f for f in ast.walk(arvore)
                if isinstance(f, ast.FunctionDef) and f.name == "_percorrer")


def test_o_handler_de_retry_nao_captura_exception_larga():
    """APP_RETRY_CATCHALL_LEGACY resolvido."""
    largos = [h for h in ast.walk(_laco())
              if isinstance(h, ast.ExceptHandler)
              and isinstance(h.type, ast.Name) and h.type.id == "Exception"]

    assert largos == []


def test_o_handler_nomeia_as_familias_que_retentam():
    """A decisao e por TIPO, e o conjunto e pequeno e legivel na propria linha."""
    handlers = [ast.unparse(h.type) for h in ast.walk(_laco())
                if isinstance(h, ast.ExceptHandler) and h.type is not None]

    assert "(_RepresentacaoNaoConcluida, navegador.FalhaDoNavegador)" in handlers


def test_o_app_nao_conhece_exception_de_fornecedor():
    """§12: a integracao traduz antes de atravessar a fronteira."""
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")

    for vendor in ("patchright", "playwright", "openpyxl", "winreg", "requests",
                   "google", "TimeoutError"):
        assert vendor not in fonte


def test_a_decisao_nao_e_por_texto_nem_por_classificador():
    """§21: sem `str(exc)`, sem `is_retryable`, sem tabela de politica."""
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")

    assert "str(erro)" not in fonte and "str(exc)" not in fonte
    assert ".args" not in fonte
    for proibido in ("is_retryable", "retryable", "RetryPolicy", "classificar_excecao",
                     "ExceptionClassifier", "TransientError"):
        assert proibido not in fonte


# ── §13 · a tradução na fronteira da integração ───────────────────────────────

def test_as_integracoes_traduzem_a_falha_do_navegador():
    """INTEGRATION_EXCEPTION_LEAK, fechado. Confirmado por sonda antes: o tipo do
    patchright atravessava tres integracoes e chegava inteiro ao laco."""
    from automation import consulta_fiscal, representacao

    for modulo, funcoes in (
        (consulta_fiscal, ("ler_situacao", "consultar_dctfweb", "consultar_processos")),
        (representacao, ("representar", "recuperar_apos_recusa")),
    ):
        for nome in funcoes:
            fonte = __import__("inspect").getsource(getattr(modulo, nome))
            assert "falhas_traduzidas()" in fonte, f"{modulo.__name__}.{nome}"


def test_a_falha_traduzida_nao_carrega_a_mensagem_original():
    """A do patchright embute a URL da pagina autenticada e o seletor."""
    from patchright.sync_api import Error as ErroDoNavegador

    with pytest.raises(navegador.FalhaDoNavegador) as erro:
        with navegador.falhas_traduzidas():
            raise ErroDoNavegador(
                "Timeout: https://servicos.exemplo/autenticado?token=SENTINELA"
            )

    assert "SENTINELA" not in str(erro.value)
    assert erro.value.__suppress_context__ is True, "`from None` corta o traceback"


def test_bug_nosso_atravessa_a_traducao_intacto():
    """So o tipo do fornecedor e traduzido."""
    with pytest.raises(TypeError, match="bug nosso"):
        with navegador.falhas_traduzidas():
            raise TypeError("bug nosso")
