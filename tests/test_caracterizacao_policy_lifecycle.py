"""O ciclo de vida da policy COMO ELE E HOJE — e por que ele bloqueia o lock.

Escrito ANTES de qualquer mudanca e commitado antes dela.

A pergunta que esta fatia precisa responder: quando `app.executar` retorna, a
policy do Chrome ainda esta instalada na maquina? Se estiver, um lock de host em
volta de `app.executar` nao fecha o contrato de exclusividade — ele liberaria o
host com estado global desta execucao ainda ativo.

Nenhum teste toca registro, UAC, processo elevado ou navegador. As primitivas do
Windows entram por dublê, como o protocolo da fatia 7A ja previa.

CNs ficticios.
"""
import ast
import inspect
import pathlib

import pytest

from automation import app, policy_certificado
from automation.captcha import ConfigCaptcha
from automation.policy_certificado import (
    ATIVADA,
    ELEVACAO_RECUSADA,
    JA_ATIVA,
    NAO_APARECEU,
    garantir_policy,
)

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ConfigCaptcha(api_key="AIzaSy-SENTINELA-FICTICIA-0000")
CN_A = "ALFA FICTICIA:11111111000191"
CN_B = "BETA FICTICIA:22222222000172"


class _Controle:
    """Token opaco do guardiao."""

    def __init__(self, cn):
        self.cn = cn


class Registro:
    """A maquina, em memoria: o que a policy diz e o que os guardioes fizeram."""

    def __init__(self, cn_inicial=""):
        self.cn = cn_inicial
        self.guardioes = []
        self.limpezas = 0

    # ── as tres primitivas que `garantir_policy` recebe ──────────────────────
    def ler_cn(self):
        return self.cn

    def lancar(self, cn):
        """O guardiao ELEVADO escreve a policy. 0 = o Windows aceitou."""
        self.guardioes.append(cn)
        self.cn = cn
        return _Controle(cn)

    def recusar(self, cn):
        return None

    def lancar_sem_escrever(self, cn):
        """Guardiao subiu numa colmeia que este processo nao enxerga."""
        self.guardioes.append(cn)
        return _Controle(cn)

    def limpar(self):
        self.limpezas += 1
        self.cn = ""


def _decisao_de_antes(ler, cn):
    """A regra de startup ANTERIOR a fatia 12D, preservada aqui de proposito.

    O que este arquivo observa e o ciclo de vida do guardiao e da posse, e nao a
    validacao de estado preexistente — que tem os seus proprios testes. Manter a
    decisao antiga faz cada assercao abaixo continuar significando exatamente o
    que significava: "o CN visivel ja e o pedido" -> usa sem lancar ninguem.
    """
    from automation.policy_certificado import CRIAR, EMPRESTAR, DecisaoDeStartup

    return lambda: DecisaoDeStartup(EMPRESTAR if ler() == cn else CRIAR)


def pedir(registro, cn, lancar=None):
    return garantir_policy(
        cn, avaliar_inicio=_decisao_de_antes(registro.ler_cn, cn),
        lancar_guardiao=lancar or registro.lancar,
        aguardar=lambda: None,
    )


# ── A · JA_ATIVA: usa sem possuir ─────────────────────────────────────────────

def test_a_ja_ativa_nao_lanca_guardiao():
    """O caso critico. A policy ja aponta para o CN pedido, entao a execucao a
    USA — e nenhum guardiao desta execucao existe para remove-la depois."""
    reg = Registro(cn_inicial=CN_A)

    resultado = pedir(reg, CN_A)

    assert resultado.situacao == JA_ATIVA
    assert resultado.confiavel is True, "o Chrome vai auto-selecionar"
    assert resultado.tem_guardiao is False
    assert resultado.sera_limpa is False, "ninguem desta execucao vai remove-la"
    assert reg.guardioes == [], "nenhum guardiao foi lancado"


def test_a_ja_ativa_nao_distingue_stale_de_concorrente():
    """`ler_cn_atual` devolve um CN e nada mais: nao ha PID, dono nem carimbo de
    tempo no payload. Uma policy deixada por uma execucao de ontem e uma escrita
    por uma execucao viva produzem exatamente o mesmo resultado."""
    de_ontem = pedir(Registro(cn_inicial=CN_A), CN_A)
    de_uma_execucao_viva = pedir(Registro(cn_inicial=CN_A), CN_A)

    assert de_ontem == de_uma_execucao_viva


# ── B · ATIVADA: possui, mas a limpeza e do guardiao ──────────────────────────

def test_b_ativada_lanca_guardiao_e_a_policy_passa_a_apontar_para_o_cn():
    reg = Registro(cn_inicial="")

    resultado = pedir(reg, CN_A)

    assert resultado.situacao == ATIVADA
    assert resultado.tem_guardiao is True
    assert resultado.sera_limpa is True
    assert resultado.controle is not None, "e agora da para PEDIR a limpeza a ele"
    assert reg.guardioes == [CN_A]
    assert reg.cn == CN_A


def test_b_a_espera_e_pelo_CN_PEDIDO_e_nao_pela_existencia():
    """A propriedade que torna seguro trocar de certificado no meio da execucao:
    a policy do certificado anterior ainda esta escrita quando o novo guardiao
    sobe, e conferir so a existencia devolveria True de imediato."""
    reg = Registro(cn_inicial=CN_A)
    tentativas = []

    def demora(cn):
        tentativas.append(cn)
        return 0   # o guardiao subiu mas ainda nao escreveu

    resultado = garantir_policy(
        CN_B, avaliar_inicio=_decisao_de_antes(reg.ler_cn, CN_B),
        lancar_guardiao=demora, aguardar=lambda: None,
    )

    assert resultado.situacao == NAO_APARECEU, "nunca aceitou o CN_A como suficiente"


@pytest.mark.parametrize("situacao, guardiao, confiavel", [
    (JA_ATIVA, False, True),
    (ATIVADA, True, True),
    (ELEVACAO_RECUSADA, False, False),
    (NAO_APARECEU, True, False),
])
def test_a_tabela_de_ownership_atual(situacao, guardiao, confiavel):
    """Os quatro desfechos, e o que cada um diz sobre limpeza.

    `tem_guardiao` e hoje a UNICA informacao de ownership que existe: ele
    responde "esta execucao provocou a escrita?" — e nada mais.
    """
    from automation.policy_certificado import ResultadoDaPolicy

    resultado = ResultadoDaPolicy(situacao, tem_guardiao=guardiao)

    assert resultado.confiavel is confiavel
    assert resultado.sera_limpa is guardiao


def test_elevacao_recusada_nao_escreve_nada():
    reg = Registro(cn_inicial="")

    resultado = pedir(reg, CN_A, lancar=reg.recusar)

    assert resultado.situacao == ELEVACAO_RECUSADA
    assert reg.cn == "", "nada foi escrito"
    assert resultado.sera_limpa is False


def test_nao_apareceu_lancou_guardiao_mas_nao_ve_a_policy():
    """Elevacao em outra conta de usuario: o guardiao escreveu numa HKCU que este
    processo nao enxerga. Ha guardiao, e nao ha policy visivel."""
    reg = Registro(cn_inicial="")

    resultado = pedir(reg, CN_A, lancar=reg.lancar_sem_escrever)

    assert resultado.situacao == NAO_APARECEU
    assert resultado.tem_guardiao is True
    assert resultado.confiavel is False
    assert resultado.controle is not None, "ha guardiao, e ele pode ser avisado"


# ── C · D · policy stale ──────────────────────────────────────────────────────

def test_c_stale_com_o_MESMO_cn_e_adotada_sem_ownership():
    """Nenhuma execucao ativa, policy de ontem apontando para o CN que queremos.
    Hoje: adota, usa, e nao assume responsabilidade nenhuma."""
    reg = Registro(cn_inicial=CN_A)

    resultado = pedir(reg, CN_A)

    assert resultado.situacao == JA_ATIVA
    assert reg.guardioes == []
    assert resultado.sera_limpa is False


def test_d_stale_com_OUTRO_cn_e_sobrescrita_e_a_execucao_passa_a_ter_guardiao():
    """A policy antiga nao e removida: os VALORES sao sobrescritos pelo guardiao
    novo, e a partir dai esta execucao tem um guardiao associado."""
    reg = Registro(cn_inicial=CN_A)

    resultado = pedir(reg, CN_B)

    assert resultado.situacao == ATIVADA
    assert resultado.tem_guardiao is True
    assert reg.cn == CN_B
    assert reg.limpezas == 0, (
        "no PROTOCOLO ninguem remove a antiga: ela e sobrescrita. Quem a libera "
        "antes e o app, em trocar_certificado — ver test_caracterizacao_guardiao"
    )


def test_d_a_escrita_NAO_apaga_mais_os_valores_anteriores():
    """ANTES: `definir_autoselect` percorria 1..n apagando antes de escrever os
    seus, e um valor alheio nesse intervalo desaparecia.

    AGORA (13A) ela confere e so preenche o que esta ausente. Nenhum
    `DeleteValue` no caminho da escrita.
    """
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    escrita = fonte[
        fonte.index("def definir_autoselect"):fonte.index("def _remover_owned_da_colmeia")
    ]

    assert "DeleteValue" not in escrita
    assert "_conflita_na_colmeia(raiz, esperados)" in escrita
    assert "is _AUSENTE" in escrita, "so escreve o que nao existe"


# ── E · I · nao existe cleanup normal ─────────────────────────────────────────

def test_e_o_cleanup_normal_passou_a_existir_e_mora_na_fiacao():
    """ANTES da fatia 12B: nenhum caminho da aplicacao chamava
    `limpar_autoselect`, e a policy so saia quando o PROCESSO morria.

    AGORA: existe um caminho normal, e ele mora onde as primitivas do Windows
    moram. O app pede; ele nao conhece winreg.
    """
    chamadores = [
        arquivo for arquivo in ("automation/app.py", "runner.py", "local.py",
                                "automation/policy_certificado.py")
        if "limpar_autoselect" in (RAIZ / arquivo).read_text(encoding="utf-8")
    ]
    assert chamadores == [], "nem o app nem os adapters chamam a primitiva"

    fiacao = (RAIZ / "automation" / "maquina.py").read_text(encoding="utf-8")
    assert "cert_windows.pedir_limpeza" in fiacao, "quem remove e o guardiao elevado"


def test_i_a_policy_sobrevive_ao_retorno_de_app_executar():
    """POLICY_LIFETIME_EXCEEDS_APP_EXECUTION, na fonte.

    O guardiao nao espera `app.executar` terminar: ele espera o PID do PROCESSO
    principal morrer, com `WaitForSingleObject(h, INFINITE)`. Enquanto o processo
    viver — e um adapter reutilizavel vive — a policy continua instalada.
    """
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")

    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]
    assert "_limpar_confirmando(cn)" in guarda
    assert "WaitForMultipleObjects" in guarda, (
        "a limpeza deixou de depender da morte do processo: o pedido tambem acorda"
    )
    assert "WaitForSingleObject(h, INFINITE)" in guarda, "sem canal, o de sempre"


def test_i_o_app_libera_a_policy_por_ultimo():
    """ANTES: o `finally` de `executar` fechava sessao e planilha, e mais nada —
    a policy ficava para o guardiao, que so age quando o processo morre.

    AGORA: a liberacao acontece, e acontece POR ULTIMO. A ordem importa: enquanto
    houver navegador vivo, a policy ainda esta em uso.
    """
    fonte = inspect.getsource(app.executar)

    # A liberacao mudou de lugar na 12B.1: saiu do `finally` para os dois ramos
    # de desfecho, porque so ali da para distinguir "ha falha em voo" de "nao ha"
    # sem consultar `sys.exc_info()`.
    ramo_normal = fonte[fonte.index("    else:"):fonte.index("    finally:")]
    assert "encerrar_sessao()" in ramo_normal
    assert "liberar_policy()" in ramo_normal
    assert ramo_normal.index("encerrar_sessao()") < ramo_normal.index("liberar_policy()"), (
        "enquanto houver navegador vivo, a policy ainda está em uso"
    )


# ── F · o guardiao depois de uma limpeza normal ───────────────────────────────

def test_f_a_limpeza_do_guardiao_tolera_policy_inexistente():
    """Se a execucao limpar a policy antes de morrer, o guardiao nao quebra: ele
    faz DeleteKey, `FileNotFoundError` e engolido, e `policy_existe()` responde
    False no primeiro giro."""
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    limpeza = fonte[fonte.index("def limpar_autoselect"):fonte.index("def _ler_cn")]

    assert "except FileNotFoundError:" in limpeza
    assert "pass" in limpeza


def test_f_o_guardiao_nao_reescreve_a_policy_depois_de_comecar():
    """Ele escreve UMA vez, no inicio, e depois so espera e limpa. Uma limpeza
    normal no fim da execucao nao e desfeita por ele."""
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]

    assert guarda.count("definir_autoselect(cn)") == 1
    assert guarda.index("definir_autoselect(cn)") < guarda.index("WaitForSingleObject")


def test_f_a_limpeza_do_guardiao_insiste_ate_dez_vezes():
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    helper = fonte[fonte.index("def _limpar_confirmando"):fonte.index("def guardiao(")]

    assert "for _ in range(10):" in helper
    # Fatia 13A: confirma a ausencia do que E NOSSO, e nao de qualquer estado.
    assert "if not policy_owned_existe(cn):" in helper


# ── H · HKCU e HKLM divergentes ───────────────────────────────────────────────

def test_h_a_decisao_de_ownership_le_apenas_a_primeira_colmeia_nao_vazia():
    """PARTIAL_POLICY_STATE interferindo no OWNERSHIP.

    `policy_cn()` devolve o CN da primeira colmeia que tiver valor. Uma execucao
    pode concluir "ja ativa" olhando HKCU e nao saber que HKLM aponta para outro
    CN — ou esta vazia. A decisao de adotar ou limpar nasce dessa leitura parcial.
    """
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    leitura = fonte[fonte.index("def policy_cn"):]
    leitura = leitura[: leitura.index("\ndef ")]

    assert "for" in leitura and "_COLMEIAS" in leitura
    assert "return" in leitura, "devolve a primeira que responder"


def test_h_o_protocolo_passou_a_receber_o_ESTADO_e_nao_so_um_cn():
    """ANTES (ate a 12D): `garantir_policy` recebia cn, ler_cn_atual,
    lancar_guardiao e aguardar, e decidia com UM valor lido de UMA colmeia. A
    divergencia entre HKCU e HKLM simplesmente nao chegava ate ele.

    A 12D acrescentou `avaliar_inicio` para a DECISAO, mas a CONFIRMACAO
    continuou saindo de `ler_cn_atual` — e era por ali que a divergencia
    voltava a passar.

    AGORA (13A.1) ha uma pergunta so, e ela e a forte. `ler_cn_atual` saiu.
    """
    parametros = list(inspect.signature(policy_certificado.garantir_policy).parameters)

    assert parametros == ["cn", "avaliar_inicio", "lancar_guardiao", "aguardar"]

    # E a avaliacao vem ANTES do unico ponto que escreve no registro.
    fonte = inspect.getsource(policy_certificado.garantir_policy)
    assert fonte.index("avaliar_inicio()") < fonte.index("lancar_guardiao(cn)")


# ── G · o cleanup nao pode mascarar a causa ───────────────────────────────────

def test_g_a_regra_de_preservacao_da_causa_ja_existe_e_vale_para_o_que_vier():
    """Qualquer limpeza que seja acrescentada tem de respeitar o que a 9B.1 e a
    fatia 10 fixaram: uma falha de cleanup nao substitui a falha primaria."""
    fonte = (RAIZ / "automation" / "app.py").read_text(encoding="utf-8")

    assert "encerrar_sessao_sem_apagar_a_causa" in fonte
    assert "except BaseException:" in fonte


# ── O que o app sabe hoje sobre a policy ──────────────────────────────────────

def test_o_app_guarda_apenas_se_a_policy_e_confiavel():
    """`policy_confiavel` e o unico resquicio do resultado que sobrevive. O
    ownership — `tem_guardiao` — e lido e descartado."""
    fonte = inspect.getsource(app._Execucao.trocar_certificado)

    assert "self.policy_confiavel = resultado.confiavel" in fonte
    assert "sera_limpa" in fonte, "hoje só vira evento"
    assert "self.policy_tem_guardiao" not in fonte, "o ownership não é guardado"


def test_o_evento_de_policy_stale_ja_existe():
    """POLICY_PERMANECERA_NA_MAQUINA ja e emitido quando `sera_limpa` e False —
    o operador ja e avisado; o que falta e alguem AGIR sobre isso."""
    from automation import eventos

    assert eventos.POLICY_PERMANECERA_NA_MAQUINA in eventos.CODIGOS

    fonte = inspect.getsource(app._Execucao.trocar_certificado)
    assert "POLICY_PERMANECERA_NA_MAQUINA" in fonte


def test_o_app_nao_importa_as_primitivas_do_windows():
    """A limpeza, se vier, entra pela fiacao — nao por winreg no app."""
    arvore = ast.parse((RAIZ / "automation" / "app.py").read_text(encoding="utf-8"))
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes.add(no.module.split(".")[0])

    assert "winreg" not in nomes and "cert_windows" not in nomes


# ── A liberação normal: só o que é nosso ──────────────────────────────────────

class PlanilhaInerte:
    def __init__(self):
        self.estado = {}

    def precisa_gravar(self):
        return False

    def descartar(self):
        pass


def execucao(emissor=None):
    return app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG, emissor)


def test_a_policy_propria_e_removida_no_fim(monkeypatch):
    """ATIVADA: esta execução provocou a escrita, então ela sai antes do retorno."""
    remocoes = []
    monkeypatch.setattr(app.maquina, "liberar_policy_do_windows",
                        lambda controle: remocoes.append(controle) or True)

    ex = execucao()
    ex.controle_da_policy = _Controle(CN_A)
    ex.liberar_policy()

    assert len(remocoes) == 1, "o controle do guardiao desta policy foi usado"
    assert ex.controle_da_policy is None, "saiu, CONFIRMADO — não tenta de novo"


def test_a_policy_de_outro_NAO_e_removida(monkeypatch):
    """JA_ATIVA: a policy já estava lá quando chegamos.

    Apagá-la seria repetir, do outro lado, o mesmo cleanup cego que produziu
    GLOBAL_CERT_POLICY_CONCURRENCY_RISK — só que agora seria a NOSSA execução
    removendo o estado de outra.
    """
    remocoes = []
    monkeypatch.setattr(app.maquina, "liberar_policy_do_windows",
                        lambda controle: remocoes.append(controle))

    ex = execucao()
    ex.controle_da_policy = None
    ex.liberar_policy()

    assert remocoes == [], "sem controle, nao ha o que pedir"


def test_o_ownership_nasce_do_tem_guardiao(monkeypatch):
    """`tem_guardiao` é a única informação de ownership que existe hoje, e ela
    responde exatamente à pergunta certa: esta execução provocou a escrita?"""
    from automation.policy_certificado import ATIVADA as _ATIVADA
    from automation.policy_certificado import JA_ATIVA as _JA_ATIVA
    from automation.policy_certificado import ResultadoDaPolicy

    for situacao, guardiao, esperado in (
        (_JA_ATIVA, False, False),
        (_ATIVADA, True, True),
    ):
        monkeypatch.setattr(
            app.maquina, "garantir_policy_do_windows",
            lambda cn, nossa=False, s=situacao, g=guardiao: ResultadoDaPolicy(
                s, g, controle=_Controle(cn) if g else None
            ),
        )
        ex = execucao()
        ex.certificados = {"c": {"subject_cn": CN_A, "serial": "0A01"}}
        ex.trocar_certificado(
            __import__("automation.planilha", fromlist=["x"]).ItemPendente(
                posicao=0, cnpj="11111111000191", certificado=CN_A
            )
        )
        assert (ex.controle_da_policy is not None) is esperado, situacao


def test_o_ownership_sobrevive_a_troca_de_certificado(monkeypatch):
    """Um certificado adotado (JA_ATIVA) depois de um escrito (ATIVADA) não
    apaga a responsabilidade: a policy da máquina continua sendo consequência
    desta execução."""
    from automation.policy_certificado import ATIVADA as _ATIVADA
    from automation.policy_certificado import JA_ATIVA as _JA_ATIVA
    from automation.policy_certificado import ResultadoDaPolicy

    resultados = iter([ResultadoDaPolicy(_ATIVADA, True, controle=_Controle(CN_A)),
                       ResultadoDaPolicy(_JA_ATIVA, False)])
    monkeypatch.setattr(app.maquina, "garantir_policy_do_windows",
                        lambda cn, nossa=False: next(resultados))
    monkeypatch.setattr(app.maquina, "liberar_policy_do_windows", lambda c: True)
    from automation.planilha import ItemPendente

    ex = execucao()
    ex.certificados = {"c": {"subject_cn": CN_A, "serial": "0A01"}}
    ex.trocar_certificado(ItemPendente(0, "11111111000191", CN_A))
    assert ex.controle_da_policy is not None, "a de ATIVADA e nossa"

    ex.trocar_certificado(ItemPendente(1, "22222222000172", CN_A))
    assert ex.controle_da_policy is None, (
        "a nossa foi liberada na troca, e a de JA_ATIVA e emprestada"
    )


# ── §21 · a liberação não pode mascarar a causa ───────────────────────────────

def test_falha_ao_remover_vira_evento_e_nao_interrompe(monkeypatch):
    """O guardião ainda é fallback, e o operador precisa saber que a policy ficou."""
    from automation import eventos

    monkeypatch.setattr(app.maquina, "liberar_policy_do_windows", lambda c: False)
    emitidos = []
    ex = execucao(emitidos.append)
    controle = _Controle(CN_A)
    ex.controle_da_policy = controle

    ex.liberar_policy()   # não levanta

    (evento,) = emitidos
    assert evento.codigo == eventos.POLICY_NAO_REMOVIDA
    assert ex.controle_da_policy is controle, (
        "o guardiao continua sendo o nosso, e segue como fallback de crash"
    )


def test_a_falha_de_remocao_nao_apaga_a_causa_primaria(monkeypatch):
    """A liberação roda no `finally` de `executar`. Uma falha conhecida dela não
    pode tomar o lugar do erro que interrompeu a execução."""
    monkeypatch.setattr(
        app.maquina, "liberar_policy_do_windows",
        lambda: (_ for _ in ()).throw(TypeError("bug de cleanup")),
    )
    ex = execucao()
    ex.policy_propria = True

    with pytest.raises(RuntimeError, match="ERRO PRIMARIO"):
        try:
            raise RuntimeError("ERRO PRIMARIO: o portal caiu")
        finally:
            ex.liberar_policy_sem_apagar_a_causa()


def test_a_frase_da_falha_nao_carrega_registro_nem_cn():
    from automation import apresentacao_eventos, eventos

    frase = apresentacao_eventos.frase(
        eventos.EventoOperacional(eventos.POLICY_NAO_REMOVIDA,
                                  tipo_da_falha="PermissionError")
    )

    assert "Software" not in frase and "HKCU" not in frase and "HKLM" not in frase
    assert CN_A not in frase
    assert "guardião" in frase, "diz ao operador que ainda há um fallback"


# ── O limite de liberação do lock ─────────────────────────────────────────────

def test_no_retorno_de_executar_a_policy_propria_ja_saiu():
    """A propriedade que a fatia 12C precisa: quando `executar` retorna, nenhuma
    policy escrita por esta execução continua instalada.

    Isto NÃO cobre uma policy adotada (JA_ATIVA) — ela nunca foi nossa, e
    continua sendo POLICY_STALE_OWNERSHIP_GAP.
    """
    fonte = inspect.getsource(app.executar)

    assert "liberar_policy()" in fonte
    assert "liberar_policy_sem_apagar_a_causa()" in fonte, "e no ramo com falha em voo"
    assert fonte.index("_percorrer(execucao, itens)") < fonte.index("liberar_policy")

    # E o que a 12B.1 acrescentou: a confirmacao. Se a policy NAO saiu,
    # `policy_propria` continua True — e e isso que a 12C precisa consultar
    # antes de liberar o host.
    assert "if maquina.liberar_policy_do_windows(self.controle_da_policy):" in (
        inspect.getsource(app._Execucao.liberar_policy)
    )


# ── §16 · §17 · §18 · o que mais precisa estar quieto no momento do release ───

def test_cert_subject_cn_nao_impede_a_liberacao_do_lock():
    """§16. O valor sobrevive à execução — em `os.environ` e no `.env` — mas
    ninguém o consome depois que a sessão fecha, e a próxima execução o
    sobrescreve ANTES de autenticar.

    `abrir_sessao` prepara o ambiente e só então chama o fork: não há janela em
    que uma execução nova autentique com o valor da anterior.
    """
    fonte = inspect.getsource(
        __import__("automation.maquina", fromlist=["x"]).abrir_sessao
    )

    # "autenticar" solto casaria com "autenticada" no docstring — pela quarta
    # vez neste projeto uma asserção bateu na prosa. Marcador de chamada.
    assert fonte.index("preparar_ambiente_do_certificado(") < fonte.index("login.autenticar(")


def test_o_caminho_de_sucesso_devolve_perfil_e_porta():
    """§17. `SessaoReceita.encerrar` fecha o CONTEXTO e depois para o Playwright,
    nessa ordem. Depois disso nenhum Chrome desta execução detém o perfil nem a
    porta 9222."""
    from automation import login

    fonte = inspect.getsource(login.SessaoReceita.encerrar)

    assert '(self.contexto, "close")' in fonte
    assert '(self.playwright, "stop")' in fonte
    assert fonte.index("contexto") < fonte.index("playwright"), "contexto primeiro"


def test_o_caminho_de_falha_de_login_passou_a_fechar_o_contexto():
    """§18 · LOGIN_RESOURCE_CLEANUP_GAP — era o RESIDUO da 12B, fechado na 12B.1.

    Todo `return None` de `fazer_login` chamava `p.stop()` e nenhum chamava
    `context.close()`. Agora os sete fecham, cada um com sua propria guarda: uma
    falha ao fechar nao impede a parada do Playwright.

    O detalhe da caracterizacao completa esta em
    tests/test_caracterizacao_host_release.py.
    """
    fonte = (RAIZ / "servicos_rf_login" / "login.py").read_text(encoding="utf-8")
    corpo = fonte[fonte.index("p = sync_playwright().start()"):]

    assert corpo.count("p.stop()") >= 7
    assert corpo.count("context.close()") == 7


def test_o_app_nao_depende_do_fork_para_fechar_o_que_ele_mesmo_abriu():
    """O contrapeso: quando o login DEVOLVE uma sessão, quem a fecha é o app —
    e aí o contexto é fechado. O resíduo é só o caminho em que o fork desiste
    antes de devolver."""
    fonte = inspect.getsource(app._Execucao.encerrar_sessao)

    assert "sessao.encerrar()" in fonte
    assert "finally:" in fonte, "os recursos saem mesmo se o logout falhar"
