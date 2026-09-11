"""Runtime composition e semantica de cleanup.

Duas perguntas, e nenhuma delas e sobre arquitetura bonita:

1. A chave do Gemini chega ao solver por PARAMETRO, ou o caminho novo depende de
   alguem ter posto a chave no ambiente antes?
2. Quando o encerramento da sessao falha, o que acontece com os recursos do
   navegador — e com a falha que obrigou o encerramento?

Nenhum teste abre navegador, portal, Gemini, registro ou PowerShell. Chaves,
CNPJs e certificados ficticios.
"""
import ast
import inspect
import pathlib

import pytest
from casos_certificado import provedor_de

from automation import app, captcha, eventos, login, maquina, navegador
from automation.captcha import ConfigCaptcha
from automation.planilha import ItemPendente

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CHAVE = "AIzaSy-SENTINELA-EXPLICITA-0000"
CHAVE_DO_AMBIENTE = "AIzaSy-SENTINELA-DO-AMBIENTE-9999"
CONFIG = ConfigCaptcha(api_key=CHAVE)
CNPJ = "11111111000191"


# ── §5 · o transporte do segredo ──────────────────────────────────────────────

def test_a_chave_da_config_chega_ao_solver_por_parametro(monkeypatch):
    """A regressao que a 9B.1 encontrou por sonda.

    `resolver` chamava `solve_hcaptcha(alvo)` sem a chave, o solver caia no seu
    fallback de ambiente, e o caminho novo so funcionava porque o adapter legado
    ainda escrevia `os.environ`. Dependencia invisivel — e agora fechada.
    """
    import resolvedor_captcha

    recebidas = []
    monkeypatch.setattr(resolvedor_captcha, "solve_hcaptcha",
                        lambda page, api_key=None: recebidas.append(api_key) or True)
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE_DO_AMBIENTE)

    captcha.resolver(object(), CONFIG)

    assert recebidas == [CHAVE], "a chave da config, e nao a do ambiente"


def test_com_o_ambiente_vazio_o_captcha_continua_funcionando(monkeypatch):
    """A prova de que a dependencia sumiu: sem nada no ambiente, resolve igual."""
    import resolvedor_captcha

    recebidas = []
    monkeypatch.setattr(resolvedor_captcha, "solve_hcaptcha",
                        lambda page, api_key=None: recebidas.append(api_key) or True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert captcha.resolver(object(), CONFIG) == captcha.RESOLVIDO_OU_AUSENTE
    assert recebidas == [CHAVE]


def test_a_chave_tambem_chega_ao_login_por_parametro(monkeypatch):
    """O outro ponto de captcha: `fazer_login(gemini_api_key=...)`."""
    recebidas = {}

    def espiao(**kwargs):
        recebidas.update(kwargs)
        return None

    monkeypatch.setattr(maquina, "preparar_ambiente_do_certificado", lambda cn: None)
    monkeypatch.setitem(
        __import__("sys").modules, "servicos_rf_login",
        type("M", (), {"fazer_login": staticmethod(espiao)}),
    )

    maquina.abrir_sessao(login.Certificado(subject_cn="ALFA:11111111000191"), True, CHAVE)

    assert recebidas["gemini_api_key"] == CHAVE


def test_maquina_nao_le_a_chave_do_ambiente():
    """§5 B: nenhuma leitura de GEMINI_API_KEY no caminho novo."""
    fonte = (RAIZ / "automation" / "maquina.py").read_text(encoding="utf-8")

    assert 'environ.get("GEMINI_API_KEY"' not in fonte
    assert 'environ["GEMINI_API_KEY"]' not in fonte


def test_maquina_nao_escreve_a_chave_no_ambiente(monkeypatch, tmp_path):
    """§5 C: a unica variavel de ambiente tocada e CERT_SUBJECT_CN."""
    monkeypatch.setattr(maquina, "diretorio_de_perfil", lambda: str(tmp_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    maquina.preparar_ambiente_do_certificado("ALFA FICTICIA:11111111000191")

    import os

    assert "GEMINI_API_KEY" not in os.environ
    assert os.environ["CERT_SUBJECT_CN"] == "ALFA FICTICIA:11111111000191"


def test_o_env_do_usuario_nao_e_reescrito_por_limpeza(monkeypatch, tmp_path):
    """§0C: uma chave que JA esta no arquivo continua la, intacta.

    O arquivo e lido para PRESERVAR o que ele traz — nenhum valor lido vira
    configuracao do caminho novo.
    """
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=CHAVE-QUE-JA-ESTAVA\nOUTRA=coisa\n", encoding="utf-8")
    monkeypatch.setattr(maquina, "diretorio_de_perfil", lambda: str(tmp_path))

    maquina.preparar_ambiente_do_certificado("ALFA:11111111000191")

    conteudo = env.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY=CHAVE-QUE-JA-ESTAVA" in conteudo, "preservada, nao apagada"
    assert "OUTRA=coisa" in conteudo


def test_a_automacao_nao_escreve_mais_o_segredo_em_disco(monkeypatch, tmp_path):
    """SECRET_PERSISTED_TO_DISK, resolvido para o caminho novo.

    Ate a fatia 10 uma chave ausente do arquivo era ACRESCENTADA nele, em texto
    puro. Nenhum consumidor do caminho novo lia dali. O operador continua podendo
    fornecer um `.env`; a automacao parou de criar um.
    """
    env = tmp_path / ".env"
    env.write_text("OUTRA=coisa\n", encoding="utf-8")
    monkeypatch.setattr(maquina, "diretorio_de_perfil", lambda: str(tmp_path))

    maquina.preparar_ambiente_do_certificado("ALFA:11111111000191")

    conteudo = env.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY" not in conteudo
    assert "CERT_SUBJECT_CN=ALFA:11111111000191" in conteudo


def test_a_funcao_nem_recebe_mais_o_segredo():
    """A costura foi separada: preparar o certificado nao e assunto de chave."""
    parametros = list(inspect.signature(maquina.preparar_ambiente_do_certificado).parameters)

    assert parametros == ["cert_subject_cn"]


def test_o_cert_subject_cn_vai_para_os_DOIS_lugares(monkeypatch, tmp_path):
    """LEGACY_RUNTIME_STATE_TRANSPORT.

    O ambiente e o que importa no caminho vivo; o arquivo e preservacao do
    legado. A fatia 12A corrigiu o motivo que este teste documentava: o
    `load_dotenv(..., override=True)` do fork existe, mas so no ramo `.pfx`, e
    nunca e alcancado no modo Windows Store. Ver
    tests/test_concorrencia.py::test_modelo_a_o_override_do_dotenv_nao_alcanca_o_caminho_vivo.
    """
    import os

    monkeypatch.setattr(maquina, "diretorio_de_perfil", lambda: str(tmp_path))

    maquina.preparar_ambiente_do_certificado("ALFA:11111111000191")

    assert os.environ["CERT_SUBJECT_CN"] == "ALFA:11111111000191"
    assert "CERT_SUBJECT_CN=ALFA:11111111000191" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")

    fonte_do_fork = (RAIZ / "servicos_rf_login" / "login.py").read_text(encoding="utf-8")
    assert 'os.environ["CERT_SUBJECT_CN"] = cert_subject_cn' in fonte_do_fork


def test_o_residuo_do_modo_pfx_continua_sendo_removido(monkeypatch, tmp_path):
    """Preservado do legado: sobrando no .env, o login tentaria o .pfx."""
    env = tmp_path / ".env"
    env.write_text("CERT_PFX_PATH=c:/velho.pfx\nCERT_PFX_PASSPHRASE=x\n", encoding="utf-8")
    monkeypatch.setattr(maquina, "diretorio_de_perfil", lambda: str(tmp_path))

    maquina.preparar_ambiente_do_certificado("ALFA:11111111000191")

    conteudo = env.read_text(encoding="utf-8")
    assert "CERT_PFX_PATH" not in conteudo
    assert "CERT_PFX_PASSPHRASE" not in conteudo


def test_o_app_nao_toca_no_ambiente_nem_no_disco_de_config():
    """§34: o app recebe a config pronta."""
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")
    for proibido in ("os.environ", "environ", ".env", "load_dotenv", "_MEIPASS"):
        assert proibido not in fonte


# ── §8 · composition root ─────────────────────────────────────────────────────

def _importados(caminho):
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes.add(no.module.split(".")[0])
    return nomes


def test_a_maquina_pode_tocar_o_mundo():
    """O oposto do teste de pureza: aqui os efeitos reais SAO esperados."""
    usados = _importados(RAIZ / "automation" / "maquina.py")

    assert "os" in usados and "sys" in usados
    assert {"servicos_rf_login", "cert_windows"} <= usados


def test_a_maquina_nao_importa_o_app():
    """A direcao e uma so: adapter -> app -> capacidades, com a fiacao embaixo.

    Se a fiacao importasse o app, um ciclo apareceria e a composicao deixaria de
    ter raiz.
    """
    assert "automation.app" not in (RAIZ / "automation" / "maquina.py").read_text(
        encoding="utf-8"
    )
    arvore = ast.parse((RAIZ / "automation" / "maquina.py").read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and no.module:
            assert no.module != "automation.app"
            assert "app" not in [a.name for a in no.names] or no.module != "automation"


def _codigo_sem_docstrings(caminho) -> str:
    """A prosa explica; ela nao decide. Só o CODIGO entra nas afirmacoes."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        corpo = getattr(no, "body", None)
        # `body` de um lambda e uma EXPRESSAO, e nao uma lista — indexa-lo
        # levanta TypeError. So blocos entram aqui.
        if (isinstance(corpo, list) and corpo and isinstance(corpo[0], ast.Expr)
                and isinstance(corpo[0].value, ast.Constant)
                and isinstance(corpo[0].value.value, str)):
            corpo.pop(0)
            if not corpo:
                corpo.append(ast.Pass())
    return ast.unparse(arvore)


def test_a_maquina_nao_contem_decisao_de_aplicacao():
    """§3 e §7: ela CONSTROI e CHAMA implementacoes. Nao decide retry, nao decide
    o que gravar, nao decide quando trocar de certificado."""
    codigo = _codigo_sem_docstrings(RAIZ / "automation" / "maquina.py")

    for decisao in ("retentativa", "tentativa", "MAX_", "registrar_", "retomada",
                    "ItemPendente", "SessaoPlanilha", "emitir", "EventoOperacional"):
        assert decisao not in codigo, f"'{decisao}' e decisao de aplicacao"


def test_a_maquina_nao_e_deposito_de_impureza():
    """Criterio de admissao: cada funcao conecta uma fronteira NOSSA a um efeito
    real. Nada entra aqui so por ser impuro."""
    funcoes = [n for n, _ in inspect.getmembers(maquina, inspect.isfunction)
               if not n.startswith("_")]

    assert sorted(funcoes) == [
        "abrir_sessao", "diretorio_de_perfil", "encerrar_controle_do_guardiao",
        "estado_do_guardiao", "garantir_policy_do_windows",
        "liberar_policy_do_windows", "preparar_ambiente_do_certificado",
    ]


def test_as_regras_continuam_onde_estavam():
    """Nada de portal, planilha ou classificacao migrou para a fiacao."""
    fonte = (RAIZ / "automation" / "maquina.py").read_text(encoding="utf-8")

    for regra in ("xpath", "locator", "openpyxl", "pandas", "COL_", "ABA_",
                  "classificar", "status_portal", "consulta_fiscal", "representacao"):
        assert regra not in fonte


# ── §6 · profile ──────────────────────────────────────────────────────────────

def test_o_profile_e_derivado_na_fiacao_e_nao_decidido_pelo_app():
    fonte_app = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")

    assert "diretorio_perfil" not in fonte_app
    assert "diretorio_de_perfil" not in fonte_app
    assert isinstance(maquina.diretorio_de_perfil(), str)


def test_o_profile_nao_entra_em_config():
    import dataclasses

    campos = {c.name for c in dataclasses.fields(ConfigCaptcha)}
    assert "diretorio_perfil" not in campos and "profile" not in campos


# ── §9 · §11 · o teardown ─────────────────────────────────────────────────────

class SessaoFalsa:
    def __init__(self, pagina="page"):
        self.pagina = pagina
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

    def abrir(self, caminho):
        pass

    def mapa_status(self, caminho):
        return {}


def execucao(emissor=None):
    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, emissor)
    ex.sessao = SessaoFalsa()
    return ex


def test_a_cleanup_normal(monkeypatch):
    """A · sem erro nenhum: logout, recursos liberados, sessao esquecida."""
    logouts = []
    monkeypatch.setattr(navegador, "encerrar_no_portal", logouts.append)

    ex = execucao()
    sessao = ex.sessao
    ex.encerrar_sessao()

    assert logouts == ["page"]
    assert sessao.encerrada is True
    assert ex.sessao is None


def test_a_o_logout_vem_antes_do_fechamento(monkeypatch):
    """A ordem importa: depois de `encerrar()` nao ha pagina para clicar."""
    ordem = []

    def logout(page):
        ordem.append("logout")

    monkeypatch.setattr(navegador, "encerrar_no_portal", logout)
    ex = execucao()
    ex.sessao.encerrar = lambda: ordem.append("encerrar")

    ex.encerrar_sessao()

    assert ordem == ["logout", "encerrar"]


def test_b_erro_do_navegador_no_logout_e_best_effort():
    """B · o logout SEMPRE foi best-effort: o legado engolia a falha para que
    `context.close()` e `playwright.stop()` acontecessem mesmo assim.

    Preservado — a sessao esta sendo descartada de qualquer forma.
    """
    from patchright.sync_api import Error as ErroDoNavegador

    class PaginaMorta:
        def locator(self, *a, **k):
            raise ErroDoNavegador("Target page, context or browser has been closed")

    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, None)
    ex.sessao = SessaoFalsa(PaginaMorta())
    sessao = ex.sessao

    ex.encerrar_sessao()   # nao levanta

    assert sessao.encerrada is True, "os recursos foram liberados assim mesmo"


def test_b_o_timeout_do_avatar_tambem_e_best_effort():
    """O lifecycle historico nunca tratou logout falho como fatal.

    A implementacao REAL e exercitada de proposito: e nela que o best-effort
    mora, e um dublê que so levanta provaria outra coisa.
    """
    from patchright.sync_api import TimeoutError as TimeoutDoNavegador

    class AvatarQueNaoAparece:
        def locator(self, *a, **k):
            raise TimeoutDoNavegador("Timeout 8000ms exceeded")

    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, None)
    ex.sessao = SessaoFalsa(AvatarQueNaoAparece())
    sessao = ex.sessao

    ex.encerrar_sessao()

    assert sessao.encerrada is True


@pytest.mark.parametrize("erro", [TypeError("bug"), AttributeError("bug"),
                                  ValueError("bug"), KeyError("bug")])
def test_c_bug_nosso_no_logout_sobe_e_nao_vaza_recursos(monkeypatch, erro):
    """C · sem erro primario, um bug de teardown NAO e invisivel: ele sobe.

    E os recursos vao embora antes. Sem o `finally`, este bug pulava
    `encerrar()` e deixava o Chrome e o Playwright abertos — o legado sempre os
    fechava, e piorar LOGIN_RESOURCE_CLEANUP_GAP nao estava em questao.
    """
    def logout(page):
        raise erro

    monkeypatch.setattr(navegador, "encerrar_no_portal", logout)
    ex = execucao()
    sessao = ex.sessao

    with pytest.raises(type(erro)):
        ex.encerrar_sessao()

    assert sessao.encerrada is True, "os recursos do navegador foram liberados"
    assert ex.sessao is None, "a sessao nao fica pendurada"


def test_d_o_bug_de_teardown_nao_toma_o_lugar_da_causa(monkeypatch):
    """D · o caso critico. CLEANUP_PRIMARY_ERROR_MASKING, corrigido.

    Antes: o `TypeError` do teardown chegava ao caller e o `RuntimeError` que
    obrigou o teardown virava apenas `__context__`. Quem lia o log via o sintoma
    e perdia o diagnostico.
    """
    def logout(page):
        raise TypeError("bug de teardown")

    monkeypatch.setattr(navegador, "encerrar_no_portal", logout)
    ex = execucao()

    with pytest.raises(RuntimeError, match="ERRO PRIMARIO") as erro:
        try:
            raise RuntimeError("ERRO PRIMARIO: o portal caiu")
        finally:
            ex.encerrar_sessao_sem_apagar_a_causa()

    assert not isinstance(erro.value, TypeError)


def test_d_o_bug_de_teardown_nao_desaparece(monkeypatch):
    """Nao e `except Exception: pass`. A falha vira um EVENTO, no ato."""
    def logout(page):
        raise TypeError("bug de teardown")

    monkeypatch.setattr(navegador, "encerrar_no_portal", logout)
    recebidos = []
    ex = execucao(recebidos.append)

    ex.encerrar_sessao_sem_apagar_a_causa()

    (evento,) = recebidos
    assert evento.codigo == eventos.FALHA_AO_ENCERRAR_SESSAO
    assert evento.tipo_da_falha == "TypeError"
    assert "bug de teardown" not in str(evento), "o nome da classe, nunca a mensagem"


def test_d_mesmo_sem_apagar_a_causa_os_recursos_sao_liberados(monkeypatch):
    def logout(page):
        raise TypeError("bug de teardown")

    monkeypatch.setattr(navegador, "encerrar_no_portal", logout)
    ex = execucao()
    sessao = ex.sessao

    ex.encerrar_sessao_sem_apagar_a_causa()

    assert sessao.encerrada is True


# ── §17 · o teardown nao pode matar o retry ───────────────────────────────────

def test_bug_de_teardown_no_retry_nao_aborta_a_execucao(monkeypatch):
    """Um bug no encerramento acontecia DENTRO do tratador de retry, entao ele
    escapava do laco e matava a execucao inteira — em vez de retentar o CNPJ."""
    from automation.login import AUTENTICADO, ResultadoDoLogin

    def logout(page):
        raise TypeError("bug de teardown")

    monkeypatch.setattr(navegador, "encerrar_no_portal", logout)
    monkeypatch.setattr(app.maquina, "abrir_sessao",
                        lambda c, a, k: ResultadoDoLogin(AUTENTICADO, SessaoFalsa()))
    monkeypatch.setattr(app, "_processar_item",
                        lambda ex, it: (_ for _ in ()).throw(
                            navegador.FalhaDoNavegador("falha do navegador")))

    codigos = []
    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, lambda e: codigos.append(e.codigo))
    ex.provedor = provedor_de({"cert": {"subject_cn": "ALFA:11111111000191", "serial": "0A01"}})
    ex.certificado_atual = "CERT"

    app._percorrer(ex, [ItemPendente(0, CNPJ, "CERT", 2)])

    assert codigos.count(eventos.ITEM_FALHOU) == 2, "as duas tentativas aconteceram"
    assert eventos.ITEM_ESGOTOU_RETENTATIVAS in codigos
    assert codigos.count(eventos.FALHA_AO_ENCERRAR_SESSAO) == 2


def test_o_teardown_falho_nao_conta_como_falha_do_cnpj(monkeypatch):
    """§17: uma exception vinda SO do teardown nao pode virar mais uma
    retentativa. O contador continua sendo do CNPJ."""
    from automation.login import AUTENTICADO, ResultadoDoLogin

    monkeypatch.setattr(navegador, "encerrar_no_portal",
                        lambda page: (_ for _ in ()).throw(TypeError("bug")))
    monkeypatch.setattr(app.maquina, "abrir_sessao",
                        lambda c, a, k: ResultadoDoLogin(AUTENTICADO, SessaoFalsa()))
    monkeypatch.setattr(app, "_processar_item",
                        lambda ex, it: (_ for _ in ()).throw(
                            navegador.FalhaDoNavegador("falha do navegador")))

    eventos_vistos = []
    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, eventos_vistos.append)
    ex.provedor = provedor_de({"cert": {"subject_cn": "ALFA:11111111000191", "serial": "0A01"}})
    ex.certificado_atual = "CERT"

    app._percorrer(ex, [ItemPendente(0, CNPJ, "CERT", 2)])

    tentativas = [e.tentativa for e in eventos_vistos if e.codigo == eventos.ITEM_FALHOU]
    assert tentativas == [1, 2], "duas tentativas, e nao quatro"


# ── §12 · §13 · os dois cenarios sao decididos separadamente ──────────────────

def test_os_dois_cenarios_tem_caminhos_diferentes_no_codigo():
    """Sem erro primario -> `encerrar_sessao`, e o bug sobe.
    Com erro primario -> `encerrar_sessao_sem_apagar_a_causa`, e a causa segue.

    Sao duas decisoes, e o codigo as mantem separadas em vez de escolher uma
    politica unica para os dois casos.
    """
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")

    assert fonte.count("def encerrar_sessao_sem_apagar_a_causa") == 1
    assert fonte.count("encerrar_sessao_sem_apagar_a_causa()") == 2, (
        "usado SO onde ha falha em voo: o tratador de retry e o cleanup final"
    )
    assert "except BaseException:" in fonte, "o cleanup final distingue os dois casos"


def test_o_cleanup_seguro_nao_e_um_pass_silencioso():
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")
    inicio = fonte.index("def encerrar_sessao_sem_apagar_a_causa")
    trecho = fonte[fonte.index("try:", inicio):]
    trecho = trecho[: trecho.index("__name__)") + len("__name__)")]

    assert "pass" not in trecho, "a falha nao e engolida"
    assert "self.emitir(" in trecho, "ela vira um evento"


# ── §16 · save e cleanup nao se atropelam ─────────────────────────────────────

def test_a_falha_de_save_no_cleanup_final_nao_apaga_a_causa(monkeypatch):
    """§16: o `finally` que grava roda depois do teardown, e nem um nem outro
    pode substituir a falha que interrompeu o processamento."""
    class PlanilhaTravada(PlanilhaInerte):
        def precisa_gravar(self):
            return True

        def gravar(self):
            raise PermissionError("arquivo aberto no Excel")

    monkeypatch.setattr(app.certificados_windows, "descobrir",
                        lambda: ({"c": {"subject_cn": "A:1", "serial": "0"}}, 0))
    monkeypatch.setattr(app, "SessaoPlanilha", PlanilhaTravada)
    monkeypatch.setattr(app.planilha, "ler_e_ordenar",
                        lambda caminho: (_ for _ in ()).throw(RuntimeError("ERRO PRIMARIO")))

    from automation.boundary import EntradaDebitosEmAberto

    codigos = []
    with pytest.raises(RuntimeError, match="ERRO PRIMARIO"):
        app.executar(EntradaDebitosEmAberto(planilha="C:/ficticia.xlsx"), CONFIG,
                     emitir_evento=lambda e: codigos.append(e.codigo))

    assert eventos.SALVAMENTO_PLANILHA_FALHOU in codigos, "a falha de save foi relatada"


# ── §0A · nem o relato da falha de cleanup pode apagar a causa ────────────────

def test_emissor_que_levanta_durante_a_preservacao_nao_apaga_a_causa(monkeypatch):
    """PRIMARY_FAILURE_EVENT_EMISSION_MASKING, reproduzido e corrigido.

    Tres falhas empilhadas: a primaria, o bug de teardown, e um bug no proprio
    adapter de eventos ao relatar o segundo. Antes, a terceira chegava ao caller.
    """
    monkeypatch.setattr(navegador, "encerrar_no_portal",
                        lambda page: (_ for _ in ()).throw(TypeError("bug de teardown")))

    def emissor_quebrado(evento):
        raise AttributeError("bug no adapter de apresentação")

    ex = execucao(emissor_quebrado)
    sessao = ex.sessao

    with pytest.raises(RuntimeError, match="ERRO PRIMARIO"):
        try:
            raise RuntimeError("ERRO PRIMARIO: o portal caiu")
        finally:
            ex.encerrar_sessao_sem_apagar_a_causa()

    assert sessao.encerrada is True, "os recursos saem mesmo assim"


def test_fora_da_preservacao_o_emissor_continua_propagando(monkeypatch):
    """A protecao vale SO no caminho cujo proposito e nao apagar a causa.

    Em qualquer outro lugar um bug no adapter e um bug, e sobe.
    """
    ex = execucao(lambda evento: (_ for _ in ()).throw(AttributeError("bug no adapter")))

    with pytest.raises(AttributeError, match="bug no adapter"):
        ex.emitir(eventos.LOGIN_CONCLUIDO)


def test_a_supressao_do_emissor_existe_num_ponto_so():
    """Nao ha supressao generalizada.

    Todas as capturas largas do modulo sao de cleanup, e vem em pares: o
    cleanup que nao apaga a causa, e o relato desse cleanup. So o segundo de
    cada par nao faz nada com o erro — e e o unico que nao tem para onde contar,
    porque o emissor era o canal.
    """
    arvore = ast.parse((RAIZ / "automation" / "app.py").read_text(encoding="utf-8"))
    largas = [
        no for no in ast.walk(arvore)
        if isinstance(no, ast.ExceptHandler)
        and isinstance(no.type, ast.Name) and no.type.id == "Exception"
    ]
    # Eram tres ate a fatia 11; o APP_RETRY_CATCHALL_LEGACY foi embora e sobraram
    # as duas do cleanup, cada uma documentada no proprio corpo.
    assert len(largas) == 4, "duas do teardown de sessão, duas do da policy"

    mudos = [h for h in largas
             if all(isinstance(c, (ast.Return, ast.Pass)) for c in h.body)]
    assert len(mudos) == 2, "so o relato de cada cleanup e mudo"

    donos = {
        f.name for f in ast.walk(arvore)
        if isinstance(f, ast.FunctionDef)
        and any(m in list(ast.walk(f)) for m in mudos)
    }
    assert donos == {"encerrar_sessao_sem_apagar_a_causa",
                     "liberar_policy_sem_apagar_a_causa"}
