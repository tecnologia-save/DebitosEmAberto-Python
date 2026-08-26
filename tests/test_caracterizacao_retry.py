"""Caracterizacao do APP_RETRY_CATCHALL_LEGACY — o comportamento COMO ELE E HOJE.

Escrito ANTES de estreitar o `except Exception` do laco, e commitado antes da
correcao. E contra este registro que a mudanca da fatia 11 sera comparada.

O que se prova aqui: hoje QUALQUER exception que atravesse a unidade de CNPJ —
inclusive um bug nosso — fecha a sessao, consome retentativa e reprocessa a
mesma linha. O portal e culpado por um `TypeError` nosso.

Nenhum teste abre navegador, portal, Gemini, registro ou PowerShell. CNPJs e
certificados ficticios.
"""
import pytest

from automation import app, eventos
from automation.captcha import ConfigCaptcha
from automation.login import AUTENTICADO, ResultadoDoLogin
from automation.planilha import ItemPendente

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
    """Roda o laco com a unidade de CNPJ levantando `falha`. Devolve os eventos.

    Registra tambem quantas vezes cada CNPJ foi processado e quantas sessoes
    foram abertas — o que distingue "retentou" de "pulou".
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
    execucao.certificados = CERTS
    execucao.certificado_atual = "CERT"

    app._percorrer(execucao, [
        ItemPendente(posicao=n, cnpj=f"{n + 11111111000191}", certificado="CERT")
        for n in range(itens)
    ])
    return emitidos, tentativas, sessoes


# ── O catch-all engole tudo ───────────────────────────────────────────────────

@pytest.mark.parametrize("bug", [
    TypeError("bug nosso"),
    AttributeError("bug nosso"),
    KeyError("bug nosso"),
    NameError("bug nosso"),
])
def test_hoje_um_bug_nosso_entra_no_retry(monkeypatch, bug):
    """O registro do que sera mudado.

    Um `TypeError` nosso e hoje indistinguivel de um portal fora do ar: fecha a
    sessao, gasta as duas tentativas, e ninguem nunca ve o bug.
    """
    emitidos, tentativas, sessoes = percorrer(monkeypatch, bug)
    codigos = [e.codigo for e in emitidos]

    assert len(tentativas) == 2, "o MESMO CNPJ foi processado duas vezes"
    assert len(sessoes) == 2, "a sessao foi fechada e reaberta entre as tentativas"
    assert codigos.count(eventos.ITEM_FALHOU) == 2
    assert eventos.ITEM_ESGOTOU_RETENTATIVAS in codigos


@pytest.mark.parametrize("bug", [TypeError("bug"), AttributeError("bug"),
                                 KeyError("bug"), NameError("bug")])
def test_hoje_o_bug_nunca_chega_ao_caller(monkeypatch, bug):
    """A consequencia que importa: o bug e silenciado. `_percorrer` termina
    normalmente, e o log diz "falha ao processar" como se fosse do portal."""
    emitidos, _, _ = percorrer(monkeypatch, bug)

    assert eventos.ITEM_FALHOU in [e.codigo for e in emitidos]


def test_hoje_o_contador_e_por_cnpj_mesmo_para_bug(monkeypatch):
    """Dois CNPJs com bug nao somam num contador unico: cada um gasta duas."""
    _, tentativas, _ = percorrer(monkeypatch, TypeError("bug nosso"), itens=2)

    assert len(tentativas) == 4
    assert len(set(tentativas)) == 2


# ── Falha externa real: o que DEVE continuar retentando ───────────────────────

def test_hoje_falha_do_navegador_entra_no_retry(monkeypatch):
    """Esta e a familia que o mecanismo existe para atender, e ela continua
    depois da fatia 11."""
    from patchright.sync_api import Error as ErroDoNavegador

    emitidos, tentativas, sessoes = percorrer(
        monkeypatch, ErroDoNavegador("Target page has been closed")
    )

    assert len(tentativas) == 2
    assert len(sessoes) == 2
    assert [e.codigo for e in emitidos].count(eventos.ITEM_FALHOU) == 2


def test_hoje_timeout_do_navegador_entra_no_retry(monkeypatch):
    from patchright.sync_api import TimeoutError as TimeoutDoNavegador

    _, tentativas, _ = percorrer(monkeypatch, TimeoutDoNavegador("Timeout 30000ms"))

    assert len(tentativas) == 2


# ── Falhas que NAO sao do portal, e hoje viram retry mesmo assim ──────────────

def test_hoje_configuracao_invalida_entra_no_retry(monkeypatch):
    """Uma chave ausente nao melhora na segunda tentativa. Hoje ela gasta uma."""
    from automation.captcha import ConfiguracaoInvalida

    _, tentativas, _ = percorrer(monkeypatch, ConfiguracaoInvalida("sem chave"))

    assert len(tentativas) == 2


def test_hoje_falha_do_adapter_de_eventos_entra_no_retry(monkeypatch):
    """O caso mais caro: um bug no adapter de apresentacao faz o app refazer
    login, representacao e captcha — pagando Gemini por um erro de `print`."""
    def processar(execucao, item):
        execucao.emitir(eventos.ITEM_JA_CONCLUIDO, posicao=item.posicao)

    tentativas = []
    sessoes = []

    def emissor(evento):
        if evento.codigo == eventos.ITEM_JA_CONCLUIDO:
            tentativas.append(1)
            raise AttributeError("bug no adapter de apresentação")

    monkeypatch.setattr(app, "_processar_item", processar)
    monkeypatch.setattr(app.navegador, "encerrar_no_portal", lambda page: None)
    monkeypatch.setattr(
        app.maquina, "abrir_sessao",
        lambda c, a, k: sessoes.append(1) or ResultadoDoLogin(AUTENTICADO, SessaoFalsa()),
    )

    execucao = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, emissor)
    execucao.certificados = CERTS
    execucao.certificado_atual = "CERT"

    app._percorrer(execucao, [ItemPendente(posicao=0, cnpj=CNPJ, certificado="CERT")])

    assert len(tentativas) == 2, "o bug do adapter foi retentado"
    assert len(sessoes) == 2, "e custou um login novo"


# ── O catch-all, na fonte ─────────────────────────────────────────────────────

def test_hoje_o_handler_captura_exception_larga():
    """O registro literal do que sera estreitado."""
    import ast
    import pathlib

    RAIZ = pathlib.Path(__file__).resolve().parents[1]
    arvore = ast.parse((RAIZ / "automation" / "app.py").read_text(encoding="utf-8"))
    laco = next(f for f in ast.walk(arvore)
                if isinstance(f, ast.FunctionDef) and f.name == "_percorrer")

    largos = [h for h in ast.walk(laco)
              if isinstance(h, ast.ExceptHandler)
              and isinstance(h.type, ast.Name) and h.type.id == "Exception"]

    assert len(largos) == 1, "o catch-all do retry, ainda de pe"
