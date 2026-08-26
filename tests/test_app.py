"""A aplicacao: o seam de eventos, a retomada dentro da execucao, e o que o app
NAO pode saber.

Nenhum teste abre navegador, portal, Gemini, registro ou PowerShell. Todas as
capacidades entram por substituicao das NOSSAS funcoes — nunca por callable na
assinatura publica.

CNPJs, empresas e certificados ficticios.
"""
import ast
import pathlib

import pytest

from automation import app, apresentacao_eventos, eventos, planilha
from automation.boundary import EntradaDebitosEmAberto
from automation.captcha import ConfigCaptcha
from automation.consulta_fiscal import (
    COM_PENDENCIA,
    SEM_PENDENCIA,
    ExtracaoFiscal,
    SituacaoFiscal,
)
from automation.eventos import EventoOperacional
from automation.login import AUTENTICADO, ResultadoDoLogin
from automation.policy_certificado import ATIVADA, ResultadoDaPolicy
from automation.representacao import REPRESENTADO, ResultadoDaRepresentacao

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ConfigCaptcha(api_key="AIzaSy-SENTINELA-FICTICIA-0000")
CNPJ = "11111111000191"
CERT = "ALFA FICTICIA LTDA"
CERTS = {
    "alfa ficticia ltda": {"subject_cn": f"{CERT}:{CNPJ}", "serial": "0A01",
                           "display": CERT},
}


class SessaoFalsa:
    def __init__(self):
        self.pagina = "page"
        self.encerrada = False

    def encerrar(self):
        self.encerrada = True


class PlanilhaFalsa:
    """Guarda o progresso em memoria, como as colunas D e E guardam no disco."""

    def __init__(self, dctfweb="", processos=""):
        self.d = dctfweb
        self.e = processos
        self.gravacoes = 0
        self.estado = {}

    def retomada(self, caminho, cnpj, encerra_linha):
        return planilha.RetomadaDaLinha(bool(self.d), bool(self.e),
                                        encerrada=bool(encerra_linha(self.d)))

    def registrar_debitos(self, cnpj, linhas):
        self.d = planilha.STATUS_CONCLUIDO
        return planilha.RegistroDeDetalhe(destinos=list(range(len(linhas))), marcado=True)

    def registrar_processos(self, cnpj, linhas):
        self.e = planilha.STATUS_CONCLUIDO
        return planilha.RegistroDeDetalhe(destinos=list(range(len(linhas))), marcado=True)

    def registrar_sem_debitos(self, cnpj):
        self.d = planilha.STATUS_SEM_DEBITOS
        return True

    def registrar_sem_processos(self, cnpj):
        self.e = planilha.STATUS_SEM_PROCESSOS
        return True

    def registrar_debitos_concluidos(self, cnpj):
        self.d = planilha.STATUS_CONCLUIDO
        return True

    def registrar_debitos_nao_compensaveis(self, cnpj):
        self.d = planilha.STATUS_DEBITOS_NAO_COMPENSAVEIS
        return True

    def registrar_recusa_do_portal(self, cnpj, status):
        self.d = status
        return True

    def precisa_gravar(self):
        return True

    def gravar(self):
        self.gravacoes += 1

    def marcar_gravado(self):
        pass

    def abrir(self, caminho):
        pass

    def descartar(self):
        pass

    def mapa_status(self, caminho):
        return {}


def item(posicao=0, cnpj=CNPJ, certificado=CERT):
    return planilha.ItemPendente(posicao=posicao, cnpj=cnpj, certificado=certificado)


def execucao_com(sessao_planilha=None, emissor=None, sessao=None):
    ex = app._Execucao(sessao_planilha or PlanilhaFalsa(), "p.xlsx", CONFIG, emissor)
    ex.certificados = CERTS
    ex.certificado_atual = CERT
    ex.sessao = sessao if sessao is not None else SessaoFalsa()
    return ex


@pytest.fixture
def capacidades(monkeypatch):
    """Todas as fronteiras substituidas nos NOSSOS modulos, nunca por parametro."""
    monkeypatch.setattr(app.maquina, "garantir_policy_do_windows",
                        lambda cn: ResultadoDaPolicy(ATIVADA, tem_guardiao=True))
    monkeypatch.setattr(app.maquina, "abrir_sessao",
                        lambda cert, auto, chave: ResultadoDoLogin(AUTENTICADO, SessaoFalsa()))
    monkeypatch.setattr(app.navegador, "encerrar_no_portal", lambda page: None)
    monkeypatch.setattr(app.representacao, "representar",
                        lambda *a, **k: ResultadoDaRepresentacao(REPRESENTADO))
    monkeypatch.setattr(app.representacao, "recuperar_apos_recusa", lambda page: True)
    return monkeypatch


def situacao(**kwargs):
    return SituacaoFiscal(kwargs.pop("situacao", COM_PENDENCIA), **kwargs)


# ── B · o seam e opcional ─────────────────────────────────────────────────────

def test_b_sem_emissor_a_execucao_e_funcionalmente_identica(capacidades):
    """Observabilidade nao e requisito de dominio: sem emissor, tudo acontece
    igual — e nada e impresso."""
    capacidades.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(situacao=SEM_PENDENCIA))

    com, sem = PlanilhaFalsa(), PlanilhaFalsa()
    emitidos = []
    app._processar_item(execucao_com(com, emitidos.append), item())
    app._processar_item(execucao_com(sem, None), item())

    assert (com.d, com.e) == (sem.d, sem.e)
    assert com.gravacoes == sem.gravacoes == 1
    assert emitidos != [], "com emissor, os fatos saem"


def test_b_sem_emissor_o_app_nao_imprime(capacidades, capsys):
    capacidades.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(situacao=SEM_PENDENCIA))

    app._processar_item(execucao_com(emissor=None), item())

    assert capsys.readouterr().out == ""


# ── C · D · o que atravessa o seam ────────────────────────────────────────────

def test_c_o_emissor_recebe_sempre_um_evento_operacional(capacidades):
    capacidades.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(situacao=SEM_PENDENCIA))
    recebidos = []

    app._processar_item(execucao_com(emissor=recebidos.append), item())

    assert recebidos != []
    assert all(isinstance(e, EventoOperacional) for e in recebidos)
    assert all(e.codigo in eventos.CODIGOS for e in recebidos)


def test_d_nenhum_evento_carrega_identificador(capacidades):
    """SECURITY: o operador localiza a linha pela POSICAO, e nunca por um
    identificador copiado para o console — ou, com `--log`, para um arquivo."""
    capacidades.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=True, tem_processo=True))
    capacidades.setattr(app.consulta_fiscal, "consultar_dctfweb",
                        lambda s, c: ExtracaoFiscal(linhas=({"cnpj": CNPJ},)))
    capacidades.setattr(app.consulta_fiscal, "consultar_processos",
                        lambda s, c: ExtracaoFiscal(linhas=({"cnpj": CNPJ},)))
    recebidos = []

    app._processar_item(execucao_com(emissor=recebidos.append), item())

    assert recebidos != []
    for evento in recebidos:
        texto = str(evento)
        for proibido in (CNPJ, "ALFA", "FICTICIA", "AIzaSy", "0A01", "http",
                         "xlsx", "Concluído"):
            assert proibido not in texto, f"{evento.codigo} vazou '{proibido}'"


def test_d_o_evento_so_tem_campos_estruturados():
    """Nao ha `mensagem: str` nem `payload: dict`: o contrato e o CODIGO."""
    import dataclasses

    tipos = {c.name: c.type for c in dataclasses.fields(EventoOperacional)}

    assert tipos["codigo"] == "str"
    assert set(tipos) - {"codigo", "tipo_da_falha"} == {
        "posicao", "total", "tentativa", "maximo", "quantidade", "paginas"
    }
    assert all(t == "int | None" for n, t in tipos.items()
               if n not in ("codigo", "tipo_da_falha"))


def test_d_codigo_desconhecido_e_recusado():
    from automation.eventos import CodigoDesconhecido

    with pytest.raises(CodigoDesconhecido):
        EventoOperacional("inventei_um_codigo")


# ── E · bug no adapter e bug ──────────────────────────────────────────────────

def test_e_exception_do_emissor_nao_e_engolida(capacidades):
    """Nao ha `try/except` em volta do emissor. Um adapter quebrado tem de
    aparecer como o que e, e nao virar silencio."""
    capacidades.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(situacao=SEM_PENDENCIA))

    def emissor_quebrado(evento):
        raise TypeError("bug no adapter de apresentação")

    with pytest.raises(TypeError, match="bug no adapter"):
        app._processar_item(execucao_com(emissor=emissor_quebrado), item())


def test_e_a_fonte_nao_protege_o_emissor():
    fonte = ast.unparse(ast.parse(
        (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")
    ))
    trecho = fonte[fonte.index("def emitir(self"):]
    trecho = trecho[: trecho.index("def salvar")]

    assert "except" not in trecho


# ── F · G · avisos das integracoes ────────────────────────────────────────────

def test_f_avisos_da_extracao_viram_evento(capacidades):
    """So a integracao observa "a rede nao estabilizou". Ela devolve o aviso no
    RESULTADO; quem o transforma em evento e o app."""
    from automation.navegador import PAGINACAO_NAO_ALTERADA, REDE_NAO_ESTABILIZOU

    capacidades.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=True, tem_processo=False))
    capacidades.setattr(
        app.consulta_fiscal, "consultar_dctfweb",
        lambda s, c: ExtracaoFiscal(avisos=(REDE_NAO_ESTABILIZOU, PAGINACAO_NAO_ALTERADA)),
    )
    recebidos = []

    app._processar_item(execucao_com(emissor=recebidos.append), item())

    codigos = [e.codigo for e in recebidos]
    assert eventos.REDE_NAO_ESTABILIZOU in codigos
    assert eventos.PAGINACAO_NAO_ALTERADA in codigos


def test_g_aviso_repetido_nao_vira_dois_eventos(capacidades):
    """A deduplicacao ja acontece na integracao — `ExtracaoFiscal` guarda o
    aviso uma vez. O app nao precisa deduplicar de novo, e nao deduplica."""
    from automation.consulta_fiscal import _anotar
    from automation.navegador import REDE_NAO_ESTABILIZOU

    avisos = []
    for _ in range(5):
        _anotar(avisos, REDE_NAO_ESTABILIZOU)

    capacidades.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=True, tem_processo=False))
    capacidades.setattr(app.consulta_fiscal, "consultar_dctfweb",
                        lambda s, c: ExtracaoFiscal(avisos=tuple(avisos)))
    recebidos = []

    app._processar_item(execucao_com(emissor=recebidos.append), item())

    codigos = [e.codigo for e in recebidos]
    assert codigos.count(eventos.REDE_NAO_ESTABILIZOU) == 1


def test_f_aviso_desconhecido_nao_vira_evento():
    """O conjunto de codigos e FECHADO. Um aviso novo na integracao nao inventa
    um evento — ele e ignorado ate alguem decidir o que ele significa."""
    assert eventos.AVISO_PARA_CODIGO.get("um aviso que ninguem mapeou") is None


# ── H · o que NAO virou evento ────────────────────────────────────────────────

def test_h_o_ruido_de_navegacao_nao_virou_evento():
    """Dos 35 prints migrados, os "→ Aguardando tabela carregar..." eram
    DEBUG_NOISE: anunciavam o inicio de um passo, nao um fato. Nao ha codigo
    para eles, e o conjunto e fechado — entao nao ha como inventa-los."""
    for ruido in ("aguardando", "clicando", "verificando", "abrindo", "digitando",
                  "carregando", "selecionando"):
        assert not any(ruido in c for c in eventos.CODIGOS)


def test_h_o_conjunto_de_codigos_e_fechado_e_pequeno():
    """Nao ha um codigo por print: prints equivalentes foram agrupados no mesmo
    fato, e o ruido ficou de fora."""
    assert len(eventos.CODIGOS) < 35, "menos codigos do que prints migrados"


# ── I · J · RESUMABILITY dentro da propria execucao ───────────────────────────

def test_i_a_retomada_e_relida_a_cada_tentativa(capacidades):
    """O motivo de D/E NAO viajarem em `ItemPendente`."""
    leituras = []
    sessao_planilha = PlanilhaFalsa()
    retomada_real = sessao_planilha.retomada

    def espionar(caminho, cnpj, encerra_linha):
        resultado = retomada_real(caminho, cnpj, encerra_linha)
        leituras.append(resultado.dctfweb_feito)
        return resultado

    sessao_planilha.retomada = espionar
    capacidades.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(situacao=SEM_PENDENCIA))

    execucao = execucao_com(sessao_planilha)
    app._processar_item(execucao, item())
    app._processar_item(execucao, item())

    assert leituras == [False, True], (
        "a segunda leitura ja enxerga o que a primeira gravou — se D/E viajassem "
        "no item, ela ainda diria False"
    )


def test_j_dctfweb_concluido_nao_e_refeito_na_retentativa_do_mesmo_cnpj(capacidades):
    """RESUMABILITY_CONTRACT, o invariante central — DENTRO de uma execucao.

    O DCTFWeb termina e grava a coluna D. Os Processos caem. O CNPJ e retentado.
    A retomada relida ja diz "D feito", entao o DCTFWeb NAO e consultado de novo.
    """
    consultas = []
    sessao_planilha = PlanilhaFalsa()

    def dctfweb(sessao, cnpj):
        consultas.append("dctfweb")
        return ExtracaoFiscal(linhas=({"cnpj": cnpj},))

    def processos_caem(sessao, cnpj):
        consultas.append("processos")
        raise RuntimeError("o portal caiu no meio dos processos")

    capacidades.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=True, tem_processo=True))
    capacidades.setattr(app.consulta_fiscal, "consultar_dctfweb", dctfweb)
    capacidades.setattr(app.consulta_fiscal, "consultar_processos", processos_caem)

    execucao = execucao_com(sessao_planilha)
    with pytest.raises(RuntimeError):
        app._processar_item(execucao, item())

    assert sessao_planilha.d == planilha.STATUS_CONCLUIDO, "D persistiu"
    assert sessao_planilha.e == "", "E continua pendente"
    assert sessao_planilha.gravacoes == 1, "o finally gravou mesmo com a falha"

    with pytest.raises(RuntimeError):
        app._processar_item(execucao, item())

    assert consultas == ["dctfweb", "processos", "processos"], "o DCTFWeb nao foi refeito"


def test_j_o_laco_retenta_o_mesmo_item_e_a_retomada_acompanha(capacidades):
    """O mesmo invariante, agora atravessando o laco de verdade — com o retry do
    CNPJ, o fechamento da sessao e o relogin."""
    consultas = []
    sessao_planilha = PlanilhaFalsa()

    capacidades.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=True, tem_processo=True))
    capacidades.setattr(app.consulta_fiscal, "consultar_dctfweb",
                        lambda s, c: consultas.append("dctfweb") or ExtracaoFiscal())
    capacidades.setattr(
        app.consulta_fiscal, "consultar_processos",
        # RETRY_SEMANTIC_CHANGE (fatia 11): era `RuntimeError`. A falha que faz o
        # CNPJ voltar tem nome desde a 11, e um RuntimeError qualquer nao retenta.
        lambda s, c: consultas.append("processos") or (_ for _ in ()).throw(
            app.navegador.FalhaDoNavegador("falha do navegador")
        ),
    )
    codigos = []
    execucao = app._Execucao(sessao_planilha, "p.xlsx", CONFIG, lambda e: codigos.append(e))
    execucao.certificados = CERTS

    app._percorrer(execucao, [item()])

    assert consultas == ["dctfweb", "processos", "processos"]
    assert [e.codigo for e in codigos].count(eventos.ITEM_FALHOU) == 2
    assert eventos.ITEM_ESGOTOU_RETENTATIVAS in [e.codigo for e in codigos]
    assert eventos.RETOMADA_PULA_DCTFWEB in [e.codigo for e in codigos]


# ── A · o evento de save, no momento da falha ─────────────────────────────────

def test_a_o_evento_de_save_sai_antes_de_a_execucao_continuar(capacidades):
    """O caso que justificou o seam: o operador libera o arquivo NO MEIO da
    execucao e o proximo save recupera o progresso."""
    ordem = []

    class PlanilhaTravada(PlanilhaFalsa):
        def gravar(self):
            ordem.append("tentou gravar")
            raise PermissionError("arquivo aberto no Excel")

    capacidades.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(situacao=SEM_PENDENCIA))

    def emissor(evento):
        ordem.append(evento.codigo)

    app._processar_item(execucao_com(PlanilhaTravada(), emissor), item())

    assert ordem[-1] == eventos.SALVAMENTO_PLANILHA_FALHOU
    assert ordem[-2] == "tentou gravar", "o evento sai logo depois da tentativa"


def test_a_a_falha_de_save_nao_muda_o_fluxo(capacidades):
    """O evento avisa; ele NAO decide. O laco segue para o proximo item."""
    class PlanilhaTravada(PlanilhaFalsa):
        def gravar(self):
            raise PermissionError("arquivo aberto no Excel")

    capacidades.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(situacao=SEM_PENDENCIA))
    codigos = []
    execucao = app._Execucao(PlanilhaTravada(), "p.xlsx", CONFIG,
                             lambda e: codigos.append(e.codigo))
    execucao.certificados = CERTS

    app._percorrer(execucao, [item(0), item(1, cnpj="22222222000172")])

    assert codigos.count(eventos.ITEM_INICIADO) == 2, "o segundo item foi processado"
    assert eventos.ITEM_FALHOU not in codigos, "salvar nao levanta"


# ── §39 · o app nao conhece tecnologia ────────────────────────────────────────

class _Parar(Exception):
    """Interrompe a execucao no ponto que interessa, sem montar uma planilha."""


def _importados(caminho):
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes.add(no.module.split(".")[0])
    return nomes


@pytest.mark.parametrize("proibido", [
    "pandas", "openpyxl", "patchright", "playwright", "winreg", "ctypes",
    "subprocess", "google", "tkinter", "pywinauto", "os", "sys", "dotenv",
    "servicos_rf_login", "resolvedor_captcha", "cert_windows", "ui_upload", "main",
])
def test_o_app_nao_importa_tecnologia(proibido):
    assert proibido not in _importados(RAIZ / "automation" / "app.py")


@pytest.mark.parametrize("proibido", [
    "xpath=", "//div", "document.", "querySelector", "powershell", "-NoProfile",
    "HKEY", "SOFTWARE\\\\Policies", "Empresas", "Débitos", "openpyxl",
    "GEMINI_API_KEY", ".env", "COL_STATUS", "ABA_",
])
def test_o_app_nao_contem_detalhe_de_tecnologia(proibido):
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")
    assert proibido not in fonte


def test_o_app_nao_imprime():
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")
    assert "print(" not in fonte


def test_o_app_nao_importa_as_exceptions_legadas():
    """LEGACY_ADAPTER_CONTROL_FLOW nao sobe para a aplicacao: os desfechos da
    representacao chegam como VALOR, e sao lidos como valor."""
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")
    for legada in ("FalhaPermanente", "AntiBotEsgotado", "RepresentacaoNaoConfirmada"):
        assert legada not in fonte


def test_a_assinatura_publica_nao_recebe_capacidades():
    """Nada de `garantir_policy=`, `autenticar=`, `representar=`. Os testes
    substituem os NOSSOS modulos; a API publica nao carrega seam de teste."""
    import inspect

    parametros = list(inspect.signature(app.executar).parameters)

    assert parametros == ["entrada", "config_captcha", "emitir_evento"]
    assert inspect.signature(app.executar).return_annotation == "None"


def test_o_emitir_evento_e_opcional_e_nao_esta_dentro_de_config():
    import dataclasses
    import inspect

    assert inspect.signature(app.executar).parameters["emitir_evento"].default is None
    campos = {c.name for c in dataclasses.fields(ConfigCaptcha)}
    assert "emitir_evento" not in campos and "callback" not in campos


def test_a_entrada_e_a_config_chegam_prontas(capacidades, monkeypatch):
    """O app nao le `os.environ`, nao le `.env`, nao descobre a planilha."""
    vistos = {}
    monkeypatch.setattr(app.certificados_windows, "descobrir", lambda: (CERTS, 0))
    monkeypatch.setattr(app, "SessaoPlanilha", PlanilhaFalsa)

    def anotar(caminho):
        vistos["caminho"] = caminho
        raise _Parar

    monkeypatch.setattr(app.planilha, "ler_e_ordenar", anotar)

    with pytest.raises(_Parar):
        app.executar(EntradaDebitosEmAberto(planilha="C:/ficticia/base.xlsx"), CONFIG)

    assert vistos["caminho"] == "C:/ficticia/base.xlsx"
    assert CONFIG.api_key not in str(vistos), "a chave nao vaza para a leitura"


# ── §37 · o adapter de apresentacao ───────────────────────────────────────────

def _evento_completo(codigo):
    return EventoOperacional(codigo, posicao=0, total=10, tentativa=1, maximo=2,
                             quantidade=3, paginas=1, tipo_da_falha="PermissionError")


@pytest.mark.parametrize("codigo", sorted(eventos.CODIGOS))
def test_todo_codigo_tem_frase(codigo):
    """Um codigo sem frase e um fato que o operador nunca ve."""

    assert apresentacao_eventos.frase(_evento_completo(codigo)), codigo


@pytest.mark.parametrize("codigo", sorted(eventos.CODIGOS))
def test_nenhuma_frase_inventa_identificador(codigo):
    """O renderer so pode dizer o que o evento carrega — e o evento nao carrega
    identificador nenhum."""

    frase = apresentacao_eventos.frase(_evento_completo(codigo))
    for proibido in (CNPJ, "ALFA", "FICTICIA", "AIzaSy", "0A01"):
        assert proibido not in frase, codigo


def test_a_frase_do_save_orienta_a_acao():
    """O `tipo_da_falha` existe por isto: PermissionError significa "feche o
    Excel", e e essa acao que recupera o progresso."""

    frase = apresentacao_eventos.frase(
        EventoOperacional(eventos.SALVAMENTO_PLANILHA_FALHOU, tipo_da_falha="PermissionError")
    )
    assert "Excel" in frase

    outra = apresentacao_eventos.frase(
        EventoOperacional(eventos.SALVAMENTO_PLANILHA_FALHOU, tipo_da_falha="ValueError")
    )
    assert "Excel" not in outra, "a orientação é do PermissionError, não de toda falha"


def test_o_renderer_marca_o_aborto_por_certificado():
    """`executar` devolve None de proposito; o codigo de saida 1 do processo vem
    do que o adapter OBSERVOU, e nao de um resumo final inventado."""
    import main

    renderer = main.Renderer()
    assert renderer.abortou is False

    renderer(EventoOperacional(eventos.ITEM_INICIADO, posicao=0, total=1))
    assert renderer.abortou is False

    renderer(EventoOperacional(eventos.CERTIFICADOS_INDISPONIVEIS))
    assert renderer.abortou is True


def test_o_main_nao_conhece_o_laco():
    """§36: o adapter mantem argparse, --log, UI, --guard e o segredo legado.
    A orquestracao inteira saiu."""
    fonte = (RAIZ / "main.py").read_text(encoding="utf-8-sig")

    for saiu in ("def processar", "def processar_cnpj", "def verificar_pendencias",
                 "_MAX_RETENT_CNPJ", "cert_atual", "SessaoPlanilha", "iterrows"):
        assert saiu not in fonte, f"'{saiu}' ainda esta em main.py"

    for fica in ("argparse", "--log", "--guard", "selecionar_planilha",
                 "_resolver_gemini_key", "montar_entrada", "app.executar"):
        assert fica in fonte, f"'{fica}' deveria continuar no adapter"


def test_o_tee_do_log_nao_subiu_para_o_app():
    """§8: o `--log` continua funcionando porque o adapter imprime e o Tee
    captura stdout. O app nao sabe que existe arquivo de log."""
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")
    for proibido in ("_Tee", "stdout", "logging", "--log"):
        assert proibido not in fonte
