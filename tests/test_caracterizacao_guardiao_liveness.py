"""A vida do PROCESSO guardiao, como ela e hoje — que e: ninguem pergunta.

Escrito ANTES de qualquer mudanca da fatia 13A.4 e commitado antes dela.

O que a 13A.3 provou e nao resolveu
-----------------------------------
GUARDIAN_FAILURE_ORPHANED_POLICY_ACCEPTANCE_RISK. O guardiao escreve a policy e
morre; o processo principal rele o registro, encontra exatamente o que pediu, e
conclui ATIVADA — com `tem_guardiao=True`, para um processo que ja nao existe.
O navegador sobe achando que ha um responsavel pela limpeza.

A aparicao da policy prova que o guardiao PASSOU pelo attach. Nao prova que ele
CONTINUA vivo. Sao duas coisas, e so a primeira esta provada hoje.

O que este arquivo registra
---------------------------
    §2  o que a elevacao ja devolve, e o que o codigo faz com isso;
    §20 policy coerente + guardiao morto = ATIVADA (o defeito);
    §12 abrir sessao nao pergunta nada sobre o guardiao;
    §15 o relogin tampouco;
    §18 o lease e um objeto compartilhado — a morte do guardiao solta o dele;
    §29 o handle do canal de limpeza nunca e fechado por quem o criou.

Nenhum teste toca registro, UAC, processo elevado ou navegador. CNs ficticios.
"""
import inspect
import pathlib
import types

import pytest

import cert_windows
from automation import app, exclusividade_host, planilha
from automation.captcha import ConfigCaptcha
from automation.policy_certificado import ATIVADA, garantir_policy

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ConfigCaptcha(api_key="AIzaSy-SENTINELA-FICTICIA-0000")
CERT = "ALFA FICTICIA LTDA"
CNPJ = "11111111000191"
CN_A = f"{CERT}:{CNPJ}"
CERTS = {"alfa ficticia ltda": {"subject_cn": CN_A, "serial": "0A01",
                                "display": CERT}}


def _codigo(nome: str) -> str:
    """A fonte de `cert_windows` SEM comentario nenhum.

    Assertiva de texto batendo na propria prosa e o tropeco recorrente deste
    projeto: os comentarios deste arquivo citam `hProcess` e `CloseHandle`
    justamente para contar esta historia.
    """
    fonte = (RAIZ / nome).read_text(encoding="utf-8")
    return "\n".join(linha for linha in fonte.splitlines()
                     if not linha.lstrip().startswith("#"))


# ── §2 · o que a elevacao ja devolve ─────────────────────────────────────────

def test_2a_o_ShellExecuteEx_JA_pede_o_handle_do_processo():
    """SEE_MASK_NOCLOSEPROCESS ja esta ligado, e o campo `hProcess` ja existe na
    struct. A informacao de liveness ja chega — a fatia nao precisa cria-la."""
    fonte = inspect.getsource(cert_windows._runas)

    assert "SEE_MASK_NOCLOSEPROCESS = 0x00000040" in fonte
    assert "sei.fMask = SEE_MASK_NOCLOSEPROCESS" in fonte
    assert "hProcess" in [nome for nome, _ in
                          cert_windows._SHELLEXECUTEINFOW._fields_]


def test_2b_e_o_handle_e_fechado_IMEDIATAMENTE():
    """No unico ramo que o guardiao usa — `wait_ms is None` — o handle e fechado
    e a funcao devolve 0. Depois disso nao ha mais como observar o processo."""
    fonte = inspect.getsource(cert_windows._runas)
    ramo = fonte[fonte.index("if wait_ms is None:"):fonte.index("_k32.WaitForSingleObject")]

    assert "CloseHandle(sei.hProcess)" in ramo
    assert "return 0" in ramo


def test_2b_e_o_ramo_que_ESPERA_nao_tem_chamador_vivo():
    """`_runas` tem UM chamador, e ele passa `wait_ms=None`. O ramo do
    `GetExitCodeProcess` e codigo morto — registrado, e nao removido: mexer em
    ctypes que ninguem executa nao paga o risco."""
    chamadas = [linha.strip() for linha in _codigo("cert_windows.py").splitlines()
                if "_runas(" in linha and "def _runas" not in linha]

    assert len(chamadas) == 1
    assert "wait_ms=None" in _codigo("cert_windows.py")[
        _codigo("cert_windows.py").index("_guard_args([\"--guard\""):][:200]


def test_2c_o_controle_do_guardiao_nao_guarda_nada_do_PROCESSO():
    """Ele sabe pedir limpeza (`evento`, `nome`) e sabe o que comparar (`cn`).
    Nao sabe se ha alguem do outro lado."""
    assert cert_windows.ControleDoGuardiao.__slots__ == ("evento", "nome", "cn")
    assert "hProcess" not in inspect.getsource(cert_windows._lancar_guardiao)


def test_2d_nao_existe_pergunta_de_LIVENESS_em_lugar_nenhum():
    """Nem primitiva, nem vocabulario de protocolo. O parent so sabe olhar o
    registro."""
    codigo = _codigo("cert_windows.py")
    protocolo = (RAIZ / "automation" / "policy_certificado.py").read_text(encoding="utf-8")

    assert "estado_do_guardiao" not in codigo
    assert "GUARDIAO_VIVO" not in protocolo
    assert "WaitForSingleObject(controle" not in codigo


# ── §20 · o defeito: policy coerente + guardiao morto = ATIVADA ──────────────

class GuardiaoQueMorre:
    """Um guardiao que escreve a policy pedida e termina em seguida.

    E o cenario da 13A.3: uma excecao inesperada depois da escrita, ou o
    processo elevado morto por fora. A policy fica coerente no registro, e o
    processo nao existe mais.
    """

    def __init__(self):
        self.cn_no_registro = ""
        self.vivo = False          # ja terminou quando o parent vai conferir

    def lancar(self, cn):
        self.cn_no_registro = cn
        return types.SimpleNamespace(cn=cn, dono=self)


def _decisao_pelo_registro(guardiao, cn):
    """EMPRESTAR quando o registro ja aponta para o CN pedido — a mesma leitura
    de coerencia que o protocolo faz hoje, reduzida ao que este arquivo
    observa: o ciclo de vida do PROCESSO, e nao a validacao de colmeias."""
    from automation.policy_certificado import CRIAR, EMPRESTAR, DecisaoDeStartup

    return lambda: DecisaoDeStartup(
        EMPRESTAR if guardiao.cn_no_registro == cn else CRIAR
    )


def test_20_policy_coerente_de_guardiao_MORTO_e_aceita_como_ATIVADA():
    """GUARDIAN_FAILURE_ORPHANED_POLICY_ACCEPTANCE_RISK, reproduzido no
    protocolo.

    O duble diz `vivo = False` desde o primeiro instante. O protocolo nunca
    pergunta — e por isso devolve o desfecho mais forte que existe.
    """
    g = GuardiaoQueMorre()

    resultado = garantir_policy(CN_A, avaliar_inicio=_decisao_pelo_registro(g, CN_A),
                                lancar_guardiao=g.lancar, aguardar=lambda: None)

    assert g.vivo is False, "o processo elevado ja nao existe"
    assert resultado.situacao == ATIVADA
    assert resultado.confiavel is True, "e o login recebe 'pode confiar'"
    assert resultado.tem_guardiao is True, "para um guardiao que nao existe"
    assert resultado.sera_limpa is True, "e ninguem vai limpar coisa nenhuma"


def test_20_e_o_protocolo_nao_tem_por_onde_perguntar():
    """Nao e que ele erre a pergunta: ele nao recebe a primitiva que
    responderia. As tres que entram sao registro, elevacao e espera."""
    parametros = list(inspect.signature(garantir_policy).parameters)

    assert parametros == ["cn", "avaliar_inicio", "lancar_guardiao", "aguardar"]


# ── §12 · §15 · abrir sessao nao pergunta nada ───────────────────────────────

class PlanilhaInerte:
    def precisa_gravar(self):
        return False

    def descartar(self):
        pass


class _Controle:
    """Token opaco do guardiao."""

    def __init__(self, cn):
        self.cn = cn


def execucao(monkeypatch, propria=True):
    aberturas = []

    def abrir(certificado, auto_select, api_key):
        aberturas.append(certificado.subject_cn)
        return types.SimpleNamespace(autenticado=True, sessao=object())

    monkeypatch.setattr(app.maquina, "abrir_sessao", abrir)
    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, None)
    ex.certificados = CERTS
    ex.certificado_atual = CERT
    ex.controle_da_policy = _Controle(CN_A) if propria else None
    return ex, aberturas


def item():
    return planilha.ItemPendente(posicao=0, cnpj=CNPJ, certificado=CERT)


def test_12_autenticar_abre_o_navegador_sem_perguntar_pelo_guardiao(monkeypatch):
    """A policy e NOSSA — ha controle — e mesmo assim a unica coisa que o login
    recebe e `policy_confiavel`, que veio da leitura do registro."""
    ex, aberturas = execucao(monkeypatch)

    assert ex.autenticar(item()) is True
    assert aberturas == [CN_A]
    assert "guardiao" not in inspect.getsource(app._Execucao.autenticar)


def test_15_o_RELOGIN_tambem_nao_pergunta(monkeypatch):
    """A sessao caiu e o app abre outra para o mesmo certificado. Segunda
    abertura, mesma ausencia de pergunta — e este e o ponto do §15."""
    ex, aberturas = execucao(monkeypatch)
    ex.autenticar(item())

    ex.sessao = None               # a sessao caiu; o certificado e o mesmo
    ex.autenticar(item())

    assert aberturas == [CN_A, CN_A], "duas aberturas, zero perguntas"


def test_12_e_o_UNICO_ponto_que_cria_sessao_e_esse(monkeypatch):
    """Mapeamento pedido pelo §12: ha um so call site de `abrir_sessao` no app,
    e e dentro de `autenticar`. Uma verificacao ali cobre todos os caminhos."""
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")

    assert fonte.count("maquina.abrir_sessao(") == 1
    assert "maquina.abrir_sessao(" in inspect.getsource(app._Execucao.autenticar)


# ── §18 · o lease e um OBJETO compartilhado ──────────────────────────────────

class ObjetoNomeado:
    """O evento nomeado do Windows, modelado pelo que importa: ele existe
    enquanto ALGUM handle estiver aberto."""

    def __init__(self):
        self.handles = 0

    def criar(self, nome):
        ja_existia = self.handles > 0
        self.handles += 1
        return object(), exclusividade_host.ERROR_ALREADY_EXISTS if ja_existia else 0

    def abrir(self, nome):
        if not self.handles:
            return 0
        self.handles += 1
        return object()

    def fechar(self, handle):
        self.handles -= 1


def test_18_a_morte_do_guardiao_solta_o_handle_DELE_e_so():
    """A cadeia de A a F do §18, sem escrever uma linha nova.

    O guardiao morto nao tem como executar cleanup tardio — e tambem nao tem
    como segurar o host. Quem decide a entrega e o parent, fechando o SEU
    handle, e so entao a proxima execucao entra.
    """
    objeto = ObjetoNomeado()
    controle = exclusividade_host.adquirir(criar=objeto.criar, fechar=objeto.fechar)
    guardiao = exclusividade_host.anexar(abrir=objeto.abrir)

    assert objeto.handles == 2, "parent + guardiao"

    objeto.fechar(guardiao)        # o guardiao morre: o SO fecha o handle dele
    with pytest.raises(exclusividade_host.ExecucaoJaAtivaNoHost):
        exclusividade_host.adquirir(criar=objeto.criar, fechar=objeto.fechar)

    exclusividade_host.liberar(controle, fechar=objeto.fechar)
    assert objeto.handles == 0

    outra = exclusividade_host.adquirir(criar=objeto.criar, fechar=objeto.fechar)
    assert outra is not None, "a proxima execucao entra"


def test_18_e_o_parent_nao_tem_como_liberar_o_handle_do_guardiao():
    """Nem deve. `liberar` fecha UM handle — o que recebeu. Nao ha caminho no
    modulo que force a liberacao do host por cima de um guardiao vivo."""
    fonte = inspect.getsource(exclusividade_host.liberar)

    assert "fechar(controle._handle)" in fonte
    assert "anexar" not in fonte


# ── §29 · o handle do canal nunca e fechado por quem o criou ─────────────────

def test_29_o_handle_do_EVENTO_de_limpeza_vaza_hoje():
    """`_lancar_guardiao` cria o evento; ninguem do lado do parent o fecha. Uma
    troca de certificado cria outro, e o anterior fica aberto ate o processo
    morrer. Registrado aqui porque a fatia vai acrescentar um SEGUNDO handle ao
    mesmo controle."""
    codigo = _codigo("cert_windows.py")

    assert "CreateEventW(None, True, False, nome)" in codigo
    assert "CloseHandle(controle." not in codigo

    app_py = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")
    assert "controle_da_policy = None" in app_py
    assert "fechar" not in app_py.split("def liberar_policy")[1].split("def ")[0]


def test_29_e_cada_troca_de_certificado_cria_um_controle_novo():
    """Um guardiao por certificado, cada um com o seu canal — e por isso o
    fechamento tem de ser por ciclo de vida, e nao no fim de tudo."""
    fonte = inspect.getsource(cert_windows._lancar_guardiao)

    assert "next(_SEQUENCIA_DE_GUARDIOES)" in fonte
    assert "CreateEventW" in fonte
