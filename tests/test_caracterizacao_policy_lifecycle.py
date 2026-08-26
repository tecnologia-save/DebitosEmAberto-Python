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
        return 0

    def recusar(self, cn):
        return 1

    def lancar_sem_escrever(self, cn):
        """Guardiao subiu numa colmeia que este processo nao enxerga."""
        self.guardioes.append(cn)
        return 0

    def limpar(self):
        self.limpezas += 1
        self.cn = ""


def pedir(registro, cn, lancar=None):
    return garantir_policy(
        cn, ler_cn_atual=registro.ler_cn,
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
        CN_B, ler_cn_atual=reg.ler_cn, lancar_guardiao=demora, aguardar=lambda: None
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
    assert reg.limpezas == 0, "ninguem removeu a policy antiga; ela foi sobrescrita"


def test_d_a_escrita_apaga_os_valores_anteriores_da_chave():
    """Na primitiva real: `definir_autoselect` percorre os valores 1..n e os
    apaga antes de escrever os seus."""
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    escrita = fonte[fonte.index("def definir_autoselect"):fonte.index("def limpar_autoselect")]

    assert "winreg.DeleteValue(key, str(i))" in escrita


# ── E · I · nao existe cleanup normal ─────────────────────────────────────────

def test_e_nenhum_caminho_da_aplicacao_limpa_a_policy():
    """I · cleanup normal existe hoje: NAO.

    Nem o app, nem os adapters, nem a fiacao chamam `limpar_autoselect`. O unico
    ponto do projeto que chama e o dispatch de `--guard` no main legado, e la e
    o tratamento de erro do proprio guardiao.
    """
    chamadores = []
    for arquivo in ("automation/app.py", "automation/maquina.py", "runner.py",
                    "local.py", "automation/policy_certificado.py"):
        if "limpar_autoselect" in (RAIZ / arquivo).read_text(encoding="utf-8"):
            chamadores.append(arquivo)

    assert chamadores == []


def test_i_a_policy_sobrevive_ao_retorno_de_app_executar():
    """POLICY_LIFETIME_EXCEEDS_APP_EXECUTION, na fonte.

    O guardiao nao espera `app.executar` terminar: ele espera o PID do PROCESSO
    principal morrer, com `WaitForSingleObject(h, INFINITE)`. Enquanto o processo
    viver — e um adapter reutilizavel vive — a policy continua instalada.
    """
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")

    assert "_runas(_guard_args([\"--guard\", str(os.getpid()), cn_b64])" in fonte
    assert "WaitForSingleObject(h, INFINITE)" in fonte

    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]
    assert "limpar_autoselect()" in guarda
    assert guarda.index("WaitForSingleObject") < guarda.index("limpar_autoselect()"), (
        "a limpeza so acontece DEPOIS de o processo principal morrer"
    )


def test_i_o_app_termina_sem_tocar_na_policy():
    """O `finally` de `executar` fecha sessao e planilha — e mais nada."""
    fonte = inspect.getsource(app.executar)
    trecho = fonte[fonte.index("finally:"):]

    assert "salvar()" in trecho and "descartar()" in trecho, "o que ele faz"
    assert "policy" not in trecho.lower(), "e o que ele nao faz"
    assert "limpar" not in trecho.lower()


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
    guarda = fonte[fonte.index("def guardiao("):fonte.index("def _lancar_guardiao")]

    assert "for _ in range(10):" in guarda
    assert "if not policy_existe():" in guarda


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


def test_h_o_protocolo_recebe_um_unico_cn_e_nao_o_estado_das_duas_colmeias():
    """`garantir_policy` decide com UM valor. Ele nao tem como saber que as
    colmeias divergem — a informacao nao chega ate ele."""
    parametros = list(inspect.signature(policy_certificado.garantir_policy).parameters)

    assert parametros == ["cn", "ler_cn_atual", "lancar_guardiao", "aguardar"]
    assert "colmeia" not in inspect.getsource(policy_certificado.garantir_policy)


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
