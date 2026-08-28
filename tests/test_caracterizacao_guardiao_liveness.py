"""A vida do PROCESSO guardiao — a pergunta que ninguem fazia.

Caracterizado e commitado ANTES da fatia 13A.4; este e o arquivo de depois, e
cada teste reapontado diz o que afirmava antes.

O que a 13A.3 provou e nao resolveu
-----------------------------------
GUARDIAN_FAILURE_ORPHANED_POLICY_ACCEPTANCE_RISK. O guardiao escreve a policy e
morre; o processo principal rele o registro, encontra exatamente o que pediu, e
conclui ATIVADA — com `tem_guardiao=True`, para um processo que ja nao existe.
O navegador subia achando que havia um responsavel pela limpeza.

A aparicao da policy prova que o guardiao PASSOU pelo attach. Nao prova que ele
CONTINUA vivo. Sao duas coisas, e ate a 13A.4 so a primeira era verificada.

O que este arquivo cobre
------------------------
    §2  o que a elevacao ja devolvia, e o que passou a ser guardado;
    §7  os tres estados, e de onde cada um sai;
    §8  ignorancia nao e morte confirmada;
    §12 nenhuma sessao nova sem responsavel vivo;
    §15 o relogin passa pela mesma porta;
    §17 a policy orfa nao e apagada;
    §18 o lease e um objeto compartilhado — a morte do guardiao solta o dele;
    §24 handle de processo nao e lease, e nao segura processo nenhum;
    §29 os handles do controle fecham no ciclo de vida certo.

Nenhum teste toca registro, UAC, processo elevado ou navegador. CNs ficticios.
"""
import inspect
import pathlib
import types

import pytest

import cert_windows
from automation import app, exclusividade_host, planilha, policy_certificado
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
    """A fonte de um modulo SEM comentario nenhum.

    Assertiva de texto batendo na propria prosa e o tropeco recorrente deste
    projeto: os comentarios daquele arquivo citam `hProcess` e `CloseHandle`
    justamente para contar esta historia.
    """
    fonte = (RAIZ / nome).read_text(encoding="utf-8")
    return "\n".join(linha for linha in fonte.splitlines()
                     if not linha.lstrip().startswith("#"))


# ── §2 · o que a elevacao ja devolvia ────────────────────────────────────────

def test_2a_o_ShellExecuteEx_JA_pedia_o_handle_do_processo():
    """SEE_MASK_NOCLOSEPROCESS ja estava ligado e o campo `hProcess` ja existia
    na struct. A evidencia sempre chegou — nao foi preciso cria-la, so parar de
    descarta-la."""
    fonte = inspect.getsource(cert_windows._elevar)

    assert "SEE_MASK_NOCLOSEPROCESS = 0x00000040" in fonte
    assert "sei.fMask = SEE_MASK_NOCLOSEPROCESS" in fonte
    assert "hProcess" in [nome for nome, _ in
                          cert_windows._SHELLEXECUTEINFOW._fields_]


def test_2b_o_guardiao_deixou_de_passar_por_quem_FECHA_o_handle():
    """ANTES o guardiao elevava por `_runas`, cujo ramo `wait_ms is None` fecha
    o handle e devolve 0 — e com ele ia embora a unica evidencia runtime do
    processo lancado.

    `_runas` continua identico. O que mudou foi o chamador.
    """
    runas = inspect.getsource(cert_windows._runas)
    ramo = runas[runas.index("if wait_ms is None:"):
                 runas.index("_k32.WaitForSingleObject")]
    assert "CloseHandle(processo)" in ramo and "return 0" in ramo

    lancar = inspect.getsource(cert_windows._lancar_guardiao)
    assert "_runas(" not in lancar
    assert "_elevar(" in lancar

    depois_do_lancamento = lancar.split("if not lancou:")[1]
    assert "CloseHandle(processo)" not in depois_do_lancamento


def test_2b_e_o_runas_ficou_sem_chamador_nenhum():
    """ANTES tinha um so, e era o guardiao. Registrado e nao removido:
    reescrever ctypes que ninguem executa nao paga o risco."""
    chamadas = [linha.strip() for linha in _codigo("cert_windows.py").splitlines()
                if "_runas(" in linha and "def _runas" not in linha]

    assert chamadas == []


def test_2c_o_controle_do_guardiao_passou_a_guardar_o_PROCESSO():
    """ANTES: `("evento", "nome", "cn")`. Ele sabia pedir limpeza e sabia o que
    comparar; nao sabia se havia alguem do outro lado.

    O acrescimo e o menor possivel: um handle. Nem usuario, nem caminho, nem
    PID.
    """
    assert cert_windows.ControleDoGuardiao.__slots__ == (
        "evento", "nome", "cn", "processo"
    )
    assert "processo" in inspect.getsource(cert_windows._lancar_guardiao)


def test_2c_e_ele_continua_sem_dizer_nada_no_repr():
    """§5. Nenhum campo novo no `repr`: nem CN, nem handle, nem nome do canal.
    DEFESA ADICIONAL, e nao garantia — acesso direto ao atributo continua
    expondo, e e por isso que so `cert_windows` o le."""
    controle = cert_windows.ControleDoGuardiao(1, "canal", CN_A, 2)

    assert repr(controle) == "ControleDoGuardiao(...)"
    assert CN_A not in repr(controle)


def test_2d_a_pergunta_de_LIVENESS_agora_existe_e_e_uma_so():
    """ANTES nao havia primitiva nem vocabulario: o parent so sabia olhar o
    registro."""
    protocolo = (RAIZ / "automation" / "policy_certificado.py").read_text(
        encoding="utf-8")

    assert "GUARDIAO_VIVO" in protocolo
    assert "GUARDIAO_ENCERRADO" in protocolo
    assert "GUARDIAO_NAO_VERIFICAVEL" in protocolo

    codigo = _codigo("cert_windows.py")
    assert codigo.count("def estado_do_guardiao(") == 1
    assert "WaitForSingleObject(controle.processo, 0)" in codigo


# ── §7 · §8 · os tres estados ────────────────────────────────────────────────

class K32:
    """O kernel, reduzido a uma resposta."""

    def __init__(self, resposta=0):
        self.resposta = resposta
        self.esperas = []
        self.fechados = []

    def WaitForSingleObject(self, h, ms):
        self.esperas.append((h, ms))
        return self.resposta

    def CloseHandle(self, h):
        self.fechados.append(h)
        return True


@pytest.mark.parametrize("resposta, esperado", [
    (cert_windows.WAIT_TIMEOUT, "GUARDIAO_VIVO"),
    (cert_windows.WAIT_OBJECT_0, "GUARDIAO_ENCERRADO"),
    (cert_windows.WAIT_FAILED, "GUARDIAO_NAO_VERIFICAVEL"),
    (0x80, "GUARDIAO_NAO_VERIFICAVEL"),
])
def test_7_os_tres_estados_saem_de_uma_espera_de_ZERO(resposta, esperado,
                                                      monkeypatch):
    """§7. Zero milissegundo: e uma pergunta, e nao uma espera.

    Qualquer resposta que nao seja "ainda nao sinalizou" ou "sinalizou" e
    ignorancia — e ignorancia NUNCA vira VIVO.
    """
    k32 = K32(resposta)
    monkeypatch.setattr(cert_windows, "_k32", k32)
    controle = cert_windows.ControleDoGuardiao(1, "canal", CN_A, 0xB0B0)

    assert cert_windows.estado_do_guardiao(controle) == getattr(
        policy_certificado, esperado)
    assert k32.esperas == [(0xB0B0, 0)]


def test_8_falhar_ao_CONSULTAR_nao_e_morte_confirmada(monkeypatch):
    """§8. As duas coisas que o estado precisa distinguir:

        ENCERRADO       -> nenhuma limpeza futura daquele guardiao pode aparecer;
        NAO_VERIFICAVEL -> ele pode estar vivo, e portanto ainda pode agir.

    Um `bool` juntaria as duas atras do mesmo `False`.
    """
    class Recusa:
        def WaitForSingleObject(self, h, ms):
            raise OSError("o Win32 recusou a consulta")

    monkeypatch.setattr(cert_windows, "_k32", Recusa())
    controle = cert_windows.ControleDoGuardiao(1, "canal", CN_A, 0xB0B0)

    assert cert_windows.estado_do_guardiao(controle) == \
        policy_certificado.GUARDIAO_NAO_VERIFICAVEL


def test_8_e_elevacao_SEM_handle_tambem_e_ignorancia():
    """O lancamento foi aceito e nao veio handle. Nunca havera resposta — e isso
    e nao saber, e nao morte."""
    controle = cert_windows.ControleDoGuardiao(1, "canal", CN_A, None)

    assert cert_windows.estado_do_guardiao(controle) == \
        policy_certificado.GUARDIAO_NAO_VERIFICAVEL


def test_24_o_handle_do_processo_nao_e_o_lease_do_host():
    """§23 e §24. O handle conserva o OBJETO do kernel para que o desfecho possa
    ser lido; ele nao conserva o processo executando. E nao e o lease: o lease e
    um evento nomeado e serve para exclusividade, nao para liveness."""
    controle = cert_windows.ControleDoGuardiao(1, "canal", CN_A, 0xB0B0)
    fonte = inspect.getsource(cert_windows.estado_do_guardiao)

    assert controle.processo != controle.evento
    assert "exclusividade_host" not in fonte
    assert "NOME_DO_LEASE" not in fonte


# ── §9 · §20 · o protocolo pergunta as DUAS coisas ───────────────────────────

class GuardiaoQueMorre:
    """Um guardiao que escreve a policy pedida e termina em seguida.

    E o cenario da 13A.3: uma excecao inesperada depois da escrita, ou o
    processo elevado morto por fora. A policy fica coerente no registro, e o
    processo nao existe mais.
    """

    def __init__(self, vida=None):
        self.cn_no_registro = ""
        self.vida = vida or policy_certificado.GUARDIAO_ENCERRADO
        self.consultas = 0

    def lancar(self, cn):
        self.cn_no_registro = cn
        return types.SimpleNamespace(cn=cn)

    def estado(self, _controle):
        self.consultas += 1
        return self.vida


def _decisao_pelo_registro(guardiao, cn):
    """EMPRESTAR quando o registro ja aponta para o CN pedido — a mesma leitura
    de coerencia que o protocolo faz hoje, reduzida ao que este arquivo
    observa: o ciclo de vida do PROCESSO, e nao a validacao de colmeias."""
    from automation.policy_certificado import CRIAR, EMPRESTAR, DecisaoDeStartup

    return lambda: DecisaoDeStartup(
        EMPRESTAR if guardiao.cn_no_registro == cn else CRIAR
    )


def _pedir(guardiao, cn=CN_A):
    return garantir_policy(cn, avaliar_inicio=_decisao_pelo_registro(guardiao, cn),
                           lancar_guardiao=guardiao.lancar, aguardar=lambda: None,
                           estado_do_guardiao=guardiao.estado)


def test_20_policy_coerente_de_guardiao_MORTO_deixou_de_ser_ATIVADA():
    """ANTES este mesmo cenario devolvia ATIVADA, `confiavel=True` e
    `tem_guardiao=True` — para um processo que ja nao existia. Era
    GUARDIAN_FAILURE_ORPHANED_POLICY_ACCEPTANCE_RISK, e e o teste principal da
    fatia."""
    g = GuardiaoQueMorre()

    resultado = _pedir(g)

    assert resultado.situacao == policy_certificado.GUARDIAO_MORREU
    assert resultado.confiavel is False, "o login NAO recebe 'pode confiar'"
    assert resultado.tem_guardiao is False, "nao ha mais ator elevado nenhum"
    assert resultado.controle is not None, "e ainda ha handles a fechar"


def test_20_e_o_guardiao_VIVO_continua_dando_ATIVADA():
    """O caminho normal nao mudou. Era o que a fatia nao podia quebrar."""
    g = GuardiaoQueMorre(vida=policy_certificado.GUARDIAO_VIVO)

    resultado = _pedir(g)

    assert resultado.situacao == ATIVADA
    assert resultado.confiavel is True
    assert resultado.tem_guardiao is True


def test_9_liveness_NAO_VERIFICAVEL_tambem_nao_e_ATIVADA():
    """§19. Nao conseguimos provar que ele morreu, entao ele pode estar vivo — e
    pode, portanto, ainda limpar. Isso preserva `tem_guardiao`, e nao autoriza
    navegador nenhum."""
    g = GuardiaoQueMorre(vida=policy_certificado.GUARDIAO_NAO_VERIFICAVEL)

    resultado = _pedir(g)

    assert resultado.situacao == policy_certificado.GUARDIAO_INCERTO
    assert resultado.confiavel is False, "fail-closed"
    assert resultado.tem_guardiao is True, "ele ainda pode agir"


def test_10_a_ORDEM_e_policy_primeiro_e_guardiao_depois():
    """§10. Liveness nao substitui a postcondicao da policy: sao as duas, nesta
    ordem. Enquanto o registro nao estiver coerente, o guardiao sequer e
    consultado."""
    g = GuardiaoQueMorre(vida=policy_certificado.GUARDIAO_VIVO)

    resultado = garantir_policy(
        CN_A, avaliar_inicio=_decisao_pelo_registro(g, CN_A),
        lancar_guardiao=lambda cn: types.SimpleNamespace(cn=cn),
        aguardar=lambda: None, estado_do_guardiao=g.estado,
    )

    assert resultado.situacao == policy_certificado.NAO_APARECEU
    assert g.consultas == 0, "policy incoerente nem chega a perguntar pelo processo"


def test_10_e_emprestada_nunca_pergunta_pelo_guardiao():
    """§12 e §30. Uma policy JA_ATIVA nunca teve guardiao desta execucao;
    exigir um dela seria recusar exatamente o caso que a 12D decidiu aceitar."""
    g = GuardiaoQueMorre()
    g.cn_no_registro = CN_A         # ja estava la quando chegamos

    resultado = _pedir(g)

    assert resultado.situacao == policy_certificado.JA_ATIVA
    assert g.consultas == 0


# ── §12 · §13 · §15 · §21 · nenhuma sessao sem responsavel vivo ──────────────

class PlanilhaInerte:
    def precisa_gravar(self):
        return False

    def descartar(self):
        pass


class _Controle:
    """Token opaco do guardiao."""

    def __init__(self, cn):
        self.cn = cn


def execucao(monkeypatch, vida=None, propria=True):
    aberturas = []

    def abrir(certificado, auto_select, api_key):
        aberturas.append(certificado.subject_cn)
        return types.SimpleNamespace(autenticado=True, sessao=object())

    monkeypatch.setattr(app.maquina, "abrir_sessao", abrir)
    monkeypatch.setattr(app.maquina, "estado_do_guardiao",
                        lambda _c: vida or policy_certificado.GUARDIAO_VIVO)
    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, None)
    ex.certificados = CERTS
    ex.certificado_atual = CERT
    ex.controle_da_policy = _Controle(CN_A) if propria else None
    return ex, aberturas


def item():
    return planilha.ItemPendente(posicao=0, cnpj=CNPJ, certificado=CERT, linha=2)


def test_12_com_guardiao_vivo_a_sessao_abre_como_sempre(monkeypatch):
    ex, aberturas = execucao(monkeypatch)

    assert ex.autenticar(item()) is True
    assert aberturas == [CN_A]


@pytest.mark.parametrize("vida", ["GUARDIAO_ENCERRADO", "GUARDIAO_NAO_VERIFICAVEL"])
def test_13_guardiao_morto_antes_do_navegador_NAO_abre_navegador(vida, monkeypatch):
    """§13 e §21. ANTES `autenticar` abria a sessao sem perguntar nada: a unica
    coisa que o login recebia era `policy_confiavel`, vinda da leitura do
    registro.

    A falha sobe como condicao do HOST, e nao como falha do CNPJ.
    """
    ex, aberturas = execucao(monkeypatch, vida=getattr(policy_certificado, vida))

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel) as erro:
        ex.autenticar(item())

    assert aberturas == [], "zero navegador"
    assert erro.value.motivo == policy_certificado.GUARDIAO_NAO_ESTA_VIVO


def test_15_o_RELOGIN_passa_pela_mesma_porta(monkeypatch):
    """§15. A sessao caiu e o app abriria outra para o mesmo certificado. ANTES
    eram duas aberturas e zero perguntas.

    Zero login novo, zero captcha novo, zero navegador novo.
    """
    estados = [policy_certificado.GUARDIAO_VIVO,
               policy_certificado.GUARDIAO_ENCERRADO]
    ex, aberturas = execucao(monkeypatch)
    monkeypatch.setattr(app.maquina, "estado_do_guardiao",
                        lambda _c: estados.pop(0))

    ex.autenticar(item())
    ex.sessao = None               # a sessao caiu; o certificado e o mesmo

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        ex.autenticar(item())

    assert aberturas == [CN_A], "a primeira abriu; a segunda, nao"


def test_14_com_a_sessao_JA_ABERTA_a_morte_do_guardiao_nao_interrompe(monkeypatch):
    """§14 e §22. GUARDIAN_DIES_DURING_ACTIVE_SESSION, registrado como
    KNOWN_RUNTIME_FAILURE_MODE — e nao como defeito de identidade.

    Respondendo pelo codigo o que o §14 pergunta: a sessao autenticada PODE
    continuar. A morte do guardiao nao altera o CN ja escrito, e o parent ainda
    possui o lease do host, entao nao ha segunda execucao para disputar a
    policy. O que se perde e a capacidade de LIMPEZA, e nao a identidade da
    sessao corrente.

    A deteccao acontece na proxima fronteira que exigir sessao nova. Nao ha
    monitor continuo, e esta fatia nao criou nenhum.
    """
    consultas = []
    ex, aberturas = execucao(monkeypatch)
    ex.sessao = object()           # ja ha navegador aberto
    monkeypatch.setattr(app.maquina, "estado_do_guardiao", consultas.append)

    assert ex.autenticar(item()) is True
    assert aberturas == [], "nao reabre nada"
    assert consultas == [], "e nao derruba o que ja esta aberto"


def test_25_nao_ha_protocolo_de_EXIT_CODE(monkeypatch):
    """§25. Vivo ou morto resolve o finding; um vocabulario de codigos de saida
    seria protocolo novo sem pergunta que o exija."""
    fonte = inspect.getsource(cert_windows.estado_do_guardiao)

    assert "GetExitCodeProcess" not in fonte
    assert "exit" not in fonte.lower().replace("existe", "")


def test_12_a_policy_EMPRESTADA_nao_exige_guardiao(monkeypatch):
    """§12. Sem controle nao ha guardiao desta execucao — e a regra nao se
    aplica. Recusar aqui seria recusar o caso que a 12D aceitou."""
    consultas = []
    ex, aberturas = execucao(monkeypatch, propria=False)
    monkeypatch.setattr(app.maquina, "estado_do_guardiao", consultas.append)

    assert ex.autenticar(item()) is True
    assert aberturas == [CN_A]
    assert consultas == []


def test_12_e_a_fronteira_e_UMA_so():
    """§12 pediu o mapeamento de TODOS os pontos vivos que criam sessao com
    policy owned. Ha um: `autenticar`. Toda abertura — a primeira e a do
    relogin — passa por ele."""
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")

    assert fonte.count("maquina.abrir_sessao(") == 1
    assert "exigir_responsavel_pela_policy()" in inspect.getsource(
        app._Execucao.autenticar)
    assert fonte.count("self.exigir_responsavel_pela_policy()") == 1


def test_26_a_recusa_nao_diz_quem_era(monkeypatch):
    """§26. Sem CN, sem PID, sem handle, sem mensagem bruta do Win32, sem
    caminho de registro."""
    ex, _ = execucao(monkeypatch, vida=policy_certificado.GUARDIAO_ENCERRADO)

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel) as erro:
        ex.autenticar(item())

    texto = f"{erro.value} {erro.value.motivo}"
    for proibido in (CN_A, CERT, CNPJ, "0xB0B0", "HKCU", "HKLM", "Software",
                     "WaitForSingleObject"):
        assert proibido not in texto


# ── §17 · a policy orfa ──────────────────────────────────────────────────────

def test_17_guardiao_morto_com_policy_de_pe_NAO_pede_limpeza(monkeypatch):
    """§17. Nao ha a quem pedir: o processo elevado terminou, e este aqui nao
    tem privilegio para remover de HKLM. Pedir mesmo assim so gastaria a espera
    inteira contra um canal que ninguem escuta."""
    from automation import eventos

    pedidos = []
    codigos = []
    monkeypatch.setattr(app.maquina, "estado_do_guardiao",
                        lambda _c: policy_certificado.GUARDIAO_ENCERRADO)
    monkeypatch.setattr(app.maquina, "liberar_policy_do_windows",
                        lambda c: pedidos.append(c) or True)
    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG,
                       lambda e: codigos.append(e.codigo))
    ex.controle_da_policy = _Controle(CN_A)

    ex.liberar_policy()

    assert pedidos == [], "ninguem escuta do outro lado"
    assert codigos == [eventos.POLICY_ORFA_NA_MAQUINA]


def test_17_e_a_policy_orfa_NAO_e_apagada(monkeypatch):
    """Ela continua sendo nossa, continua escrita, e quem decide o que fazer com
    ela e o startup da proxima execucao — com BORROW/CREATE/REFUSE."""
    removidos = []
    monkeypatch.setattr(app.maquina, "estado_do_guardiao",
                        lambda _c: policy_certificado.GUARDIAO_ENCERRADO)
    monkeypatch.setattr(app.maquina, "liberar_policy_do_windows",
                        lambda c: removidos.append(c) or True)
    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, None)
    ex.controle_da_policy = _Controle(CN_A)

    ex.liberar_policy()

    assert removidos == []
    assert ex.controle_da_policy is not None, "esta execucao sabe que instalou algo"


def test_17_e_por_isso_a_troca_de_certificado_para(monkeypatch):
    """A guarda fail-closed da 13A continua sendo a que decide: com a policy
    anterior de pe, nenhum guardiao novo e lancado."""
    lancados = []
    monkeypatch.setattr(app.maquina, "estado_do_guardiao",
                        lambda _c: policy_certificado.GUARDIAO_ENCERRADO)
    monkeypatch.setattr(app.maquina, "garantir_policy_do_windows",
                        lambda cn: lancados.append(cn))
    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, None)
    ex.certificados = CERTS
    ex.controle_da_policy = _Controle(CN_A)

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel) as erro:
        ex.trocar_certificado(item())

    assert lancados == []
    assert erro.value.motivo == policy_certificado.POLICY_ANTERIOR_NAO_REMOVIDA


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
    handle, e so entao a proxima execucao entra e ve a policy como PREEXISTING.
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
    """§19, do outro lado. `liberar` fecha UM handle — o que recebeu. Enquanto
    um guardiao possivelmente vivo mantiver o dele, o objeto continua existindo
    e a proxima execucao continua sendo recusada. Nada disso e decidido por
    suposicao."""
    fonte = inspect.getsource(exclusividade_host.liberar)

    assert "fechar(controle._handle)" in fonte
    assert "anexar" not in fonte


def test_18_e_o_lease_nao_foi_tocado_pela_fatia():
    """§23 e a entrega W. O mecanismo de exclusividade e outro assunto."""
    fonte = (RAIZ / "automation" / "exclusividade_host.py").read_text(
        encoding="utf-8")

    assert "estado_do_guardiao" not in fonte
    assert "13A.4" not in fonte


# ── §29 · os handles do controle ─────────────────────────────────────────────

def test_29_a_limpeza_confirmada_fecha_os_DOIS_handles(monkeypatch):
    """ANTES o handle do canal nunca era fechado por quem o criou: uma troca de
    certificado criava outro e o anterior ficava aberto ate o processo morrer.

    Agora fecham juntos, e no ciclo de vida certo — depois da confirmacao.
    """
    k32 = K32()
    monkeypatch.setattr(cert_windows, "_k32", k32)
    controle = cert_windows.ControleDoGuardiao(0xE0E0, "canal", CN_A, 0xB0B0)

    cert_windows.encerrar_controle(controle)

    assert sorted(k32.fechados) == [0xB0B0, 0xE0E0]
    assert controle.evento is None and controle.processo is None


def test_29_e_e_a_confirmacao_que_dispara_o_fechamento(monkeypatch):
    """Nao confirmou, nao fecha: o guardiao continua sendo o fallback de crash e
    o canal precisa continuar aberto para ele."""
    from automation import eventos

    fechados = []
    codigos = []
    monkeypatch.setattr(app.maquina, "estado_do_guardiao",
                        lambda _c: policy_certificado.GUARDIAO_VIVO)
    monkeypatch.setattr(app.maquina, "encerrar_controle_do_guardiao",
                        fechados.append)
    monkeypatch.setattr(app.maquina, "liberar_policy_do_windows", lambda c: False)
    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG,
                       lambda e: codigos.append(e.codigo))
    ex.controle_da_policy = _Controle(CN_A)

    ex.liberar_policy()

    assert fechados == []
    assert ex.controle_da_policy is not None
    assert codigos == [eventos.POLICY_NAO_REMOVIDA]


def test_29_a_troca_de_certificado_nao_acumula_handles(monkeypatch):
    """§6 e §29. Guardiao A fecha quando o ciclo de vida de A termina; B ganha
    controle novo. Nada acumula por certificado."""
    fechados = []
    controles = [_Controle("A"), _Controle("B")]
    monkeypatch.setattr(app.maquina, "estado_do_guardiao",
                        lambda _c: policy_certificado.GUARDIAO_VIVO)
    monkeypatch.setattr(app.maquina, "encerrar_controle_do_guardiao",
                        fechados.append)
    monkeypatch.setattr(app.maquina, "liberar_policy_do_windows", lambda c: True)
    ex = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, None)

    ex.controle_da_policy = controles[0]
    ex.liberar_policy()
    ex.controle_da_policy = controles[1]
    ex.liberar_policy()

    assert fechados == controles, "um fechamento por guardiao, na ordem"
    assert ex.controle_da_policy is None


def test_29_e_cada_troca_de_certificado_cria_um_controle_novo():
    """Um guardiao por certificado, cada um com o seu canal — e por isso o
    fechamento tem de ser por ciclo de vida, e nao no fim de tudo."""
    fonte = inspect.getsource(cert_windows._lancar_guardiao)

    assert "next(_SEQUENCIA_DE_GUARDIOES)" in fonte
    assert "CreateEventW" in fonte
