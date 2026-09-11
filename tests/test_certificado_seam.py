"""O seam do certificado: a aplicacao pede, o provedor responde.

A pergunta desta fatia: a aplicacao precisa saber que os certificados moram no
Certificate Store? Os testes abaixo respondem que nao — e respondem com um
provedor que NAO importa Windows, PowerShell, registro nem navegador.

O certificado e POR LINHA. Duas linhas com certificados diferentes recebem
certificados diferentes, e nenhuma resolucao unica vale para a execucao inteira:
escolher o certificado errado significa autenticar na conta de outra empresa.
"""
import ast
import pathlib

import pytest

from automation import app, certificados, eventos, planilha
from automation.captcha import ConfigCaptcha
from automation.domain import ResultadoDaBusca
from automation.login import Certificado

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ConfigCaptcha(api_key="chave-ficticia")


class ProvedorDeMentira:
    """Um catalogo em memoria. Nenhuma linha de Windows aqui dentro.

    E de proposito que ele nao herda de nada: `ProvedorDeCertificados` e um
    Protocol, entao conformidade e ter os metodos — nao ser filho de alguem.
    """

    def __init__(self, catalogo=None, ao_carregar=None):
        self.catalogo = dict(catalogo or {})
        self._ao_carregar = ao_carregar
        self.carregou = 0
        self.resolvidos = []
        self.entregues = []

    def carregar(self) -> int:
        self.carregou += 1
        if self._ao_carregar is not None:
            raise self._ao_carregar
        return len(self.catalogo)

    def resolver(self, nome: str) -> ResultadoDaBusca:
        self.resolvidos.append(nome)
        achado = self.catalogo.get(nome)
        if achado is None:
            return ResultadoDaBusca()
        if isinstance(achado, tuple):      # empate
            return ResultadoDaBusca(ambiguidade=achado)
        return ResultadoDaBusca(chave=nome, criterio="exato")

    def certificado(self, chave: str) -> Certificado:
        self.entregues.append(chave)
        return self.catalogo[chave]


def _certificado(cn, serial="0A01"):
    return Certificado(subject_cn=cn, serial=serial)


def _item(posicao, certificado, cnpj="11111111000191"):
    return planilha.ItemPendente(posicao=posicao, cnpj=cnpj,
                                 certificado=certificado, linha=posicao + 2)


def _execucao(provedor, emissor=None):
    class PlanilhaInerte:
        def salvar(self):
            pass

        def descartar(self):
            pass

        def mapa_status(self, caminho):
            return {}

    return app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, emissor, provedor)


# ── a aplicacao aceita o provedor ────────────────────────────────────────────

def test_a_aplicacao_usa_o_provedor_QUE_RECEBE():
    provedor = ProvedorDeMentira({"ALFA": _certificado("ALFA FICTICIA:111")})

    execucao = _execucao(provedor)

    assert execucao.provedor is provedor
    assert execucao.chave_do_certificado("ALFA") == "ALFA"
    assert provedor.resolvidos == ["ALFA"]


def test_sem_provedor_a_execucao_continua_sendo_a_do_DESKTOP():
    """O padrao existe para a transicao: `main.py` e `local.py` nao mudaram."""
    from automation.certificados_windows import CertificadosDoWindows

    execucao = _execucao(provedor=None)

    assert isinstance(execucao.provedor, CertificadosDoWindows)


def test_a_assinatura_publica_aceita_o_provedor():
    import inspect

    assert "provedor_de_certificados" in inspect.signature(app.executar).parameters


# ── o certificado e POR LINHA ────────────────────────────────────────────────

def test_cada_linha_recebe_O_SEU_certificado():
    alfa, beta = _certificado("ALFA:111"), _certificado("BETA:222", serial="0B02")
    provedor = ProvedorDeMentira({"ALFA": alfa, "BETA": beta})
    execucao = _execucao(provedor)

    primeiro = execucao.provedor.certificado(
        execucao.chave_do_certificado(_item(0, "ALFA").certificado))
    segundo = execucao.provedor.certificado(
        execucao.chave_do_certificado(_item(1, "BETA").certificado))

    assert primeiro == alfa
    assert segundo == beta
    assert primeiro != segundo, "duas linhas, dois certificados"


def test_NAO_existe_certificado_unico_para_a_execucao_inteira():
    """Uma resolucao global seria autenticar a planilha toda com o primeiro
    certificado — e o defeito nao apareceria como erro, e sim como acesso a
    conta errada."""
    fonte = ast.parse((RAIZ / "automation" / "app.py").read_text(encoding="utf-8"))
    trocar = next(n for n in ast.walk(fonte)
                  if isinstance(n, ast.FunctionDef) and n.name == "trocar_certificado")

    chamadas = {n.func.attr for n in ast.walk(trocar)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}

    assert "buscar_certificado" in chamadas, "resolve a cada troca, e nao uma vez"
    assert "certificado" in chamadas


def test_o_certificado_da_linha_chega_ao_login(monkeypatch):
    alfa = _certificado("ALFA:111")
    provedor = ProvedorDeMentira({"ALFA": alfa})
    execucao = _execucao(provedor)
    execucao.certificado_atual = "ALFA"
    execucao.policy_confiavel = True
    recebidos = []

    class SessaoFalsa:
        pass

    monkeypatch.setattr(app, "maquina", type("M", (), {
        "estado_do_guardiao": staticmethod(lambda c: None),
        "abrir_sessao": staticmethod(
            lambda cert, auto, chave: recebidos.append(cert) or _login_ok(SessaoFalsa())),
    }))
    monkeypatch.setattr(app._Execucao, "exigir_responsavel_pela_policy",
                        lambda self: None)

    assert execucao.autenticar(_item(0, "ALFA")) is True
    assert recebidos == [alfa], "o login recebe o certificado DAQUELA linha"


def _login_ok(sessao):
    from automation.login import AUTENTICADO, ResultadoDoLogin

    return ResultadoDoLogin(AUTENTICADO, sessao)


# ── desfechos que nao sao sucesso ────────────────────────────────────────────

def test_certificado_ausente_vira_evento_proprio():
    provedor = ProvedorDeMentira({})
    recebidos = []
    execucao = _execucao(provedor, emissor=lambda e: recebidos.append(e.codigo))

    assert execucao.trocar_certificado(_item(0, "SUMIU")) is False
    assert eventos.CERTIFICADO_NAO_INSTALADO in recebidos
    assert eventos.CERTIFICADO_AMBIGUO not in recebidos


def test_certificado_ambiguo_vira_OUTRO_evento():
    """Instalar o certificado e reescrever o nome na planilha sao acoes
    diferentes do operador. Um codigo so esconderia a diferenca."""
    provedor = ProvedorDeMentira({"ALFA": ("ALFA UM", "ALFA DOIS")})
    recebidos = []
    execucao = _execucao(provedor, emissor=lambda e: recebidos.append(e.codigo))

    assert execucao.trocar_certificado(_item(0, "ALFA")) is False
    assert eventos.CERTIFICADO_AMBIGUO in recebidos
    assert eventos.CERTIFICADO_NAO_INSTALADO not in recebidos


def test_catalogo_ilegivel_e_diferente_de_catalogo_vazio():
    """A distincao da fatia 5A continua valendo para QUALQUER provedor: sem ela
    o operador e mandado instalar um certificado que ja esta instalado."""
    falha = ProvedorDeMentira(ao_carregar=certificados.FalhaAoLerCertificados("x"))
    vazio = ProvedorDeMentira({})

    with pytest.raises(certificados.FalhaAoLerCertificados):
        falha.carregar()
    assert vazio.carregar() == 0


# ── neutralidade de plataforma ───────────────────────────────────────────────

def test_o_seam_nao_conhece_windows_navegador_nem_plataforma():
    """Prova por import: `automation/certificados.py` roda em qualquer sistema."""
    arvore = ast.parse((RAIZ / "automation" / "certificados.py").read_text(
        encoding="utf-8"))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])

    proibidos = {"winreg", "ctypes", "subprocess", "os", "patchright", "playwright",
                 "autohub_sdk", "autohub", "cert_windows", "certificados_windows",
                 "servicos_rf_login", "resolvedor_captcha", "tkinter"}
    assert not (importados & proibidos), importados & proibidos


def test_o_provedor_de_teste_nao_encosta_no_windows():
    """Este arquivo inteiro roda sem Certificate Store, sem PowerShell e sem
    registro — e o unico import de Windows aqui e o do teste que confere o
    padrao do desktop, feito dentro da funcao."""
    arvore = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    no_topo = set()
    for no in arvore.body:
        if isinstance(no, ast.Import):
            no_topo.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            no_topo.add(no.module.split(".")[0])

    assert "cert_windows" not in no_topo
    assert "subprocess" not in no_topo
