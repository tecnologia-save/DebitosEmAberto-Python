"""Caracterizacao da ORQUESTRACAO como ela e hoje.

O laco de `processar` coordena tudo o que as fatias 1 a 8 isolaram: certificado,
policy, sessao, representacao, consulta, persistencia, retry e cleanup. Nenhuma
dessas capacidades e exercitada de verdade aqui — todas entram por substituicao,
porque o que se caracteriza e a COORDENACAO.

Nenhum navegador, portal, Gemini, registro, UAC ou planilha de cliente.
CNPJs, empresas e certificados ficticios.
"""
import pandas as pd
import pytest

from automation import app, eventos, planilha
from automation.captcha import ConfigCaptcha
from automation.login import AUTENTICADO, NAO_AUTENTICADO, ResultadoDoLogin
from automation.policy_certificado import ATIVADA, JA_ATIVA, ResultadoDaPolicy

# CHARACTERIZATION_TARGET_CHANGE (fatia 9B): o laco saiu de `main.processar` e
# foi para `automation/app.py`. As situacoes, a ordem dos eventos e as decisoes
# observaveis sao as mesmas; o que mudou e quem as executa e onde as capacidades
# sao substituidas — em `automation.app`, e nao mais nos adapters do `main`.
CONFIG = ConfigCaptcha(api_key="AIzaSy-SENTINELA-FICTICIA-0000")

CERT_ALFA = "ALFA FICTICIA LTDA:11111111000191"
CERT_BETA = "BETA FICTICIA SA:22222222000172"
CNPJ_1 = "11111111000191"
CNPJ_2 = "33333333000153"
CNPJ_3 = "44444444000114"

CERTS = {
    "cert alfa": {"subject_cn": CERT_ALFA, "serial": "0A01", "display": "CERT ALFA"},
    "cert beta": {"subject_cn": CERT_BETA, "serial": "0B02", "display": "CERT BETA"},
}


COLUNAS = ["CNPJ", "EMPRESA", "CERTIFICADO"]


def planilha_com(*linhas):
    """DataFrame no formato que `processar` consome: A=CNPJ, C=CERTIFICADO.

    As colunas existem sempre, mesmo sem linhas — `processar` le `df.columns[0]`
    antes do laco, e uma planilha real nunca chega sem cabecalho.
    """
    return pd.DataFrame(
        [{"CNPJ": c, "EMPRESA": "FICTICIA", "CERTIFICADO": cert} for c, cert in linhas],
        columns=COLUNAS,
    )


class SessaoFalsa:
    def __init__(self, marca="s1", registro=None):
        self.marca = marca
        self.playwright = f"pw-{marca}"
        self.contexto = f"ctx-{marca}"
        self.pagina = f"page-{marca}"
        self.registro = registro
        self.encerrada = False

    def encerrar(self):
        """Antes o fechamento era `main._fechar_navegador(pw, ctx, page)`; hoje o
        app pede a SESSAO que se encerre. O evento registrado e o mesmo."""
        self.encerrada = True
        if self.registro is not None:
            self.registro.anotar("fechar", self.pagina)


class Registro:
    """O que a orquestracao fez, na ordem."""

    def __init__(self):
        self.eventos = []

    def anotar(self, *evento):
        self.eventos.append(evento)

    def so(self, nome):
        return [e for e in self.eventos if e[0] == nome]


class PlanilhaInerte:
    """A planilha nao participa destes testes: aqui se caracteriza a COORDENACAO.

    `retomada` devolve sempre "nada feito" para que todo item siga o fluxo
    completo — era o que o `processar_cnpj` substituido fazia antes.
    """

    def __init__(self):
        self.estado = {}

    def retomada(self, caminho, cnpj, encerra_linha):
        return planilha.RetomadaDaLinha(False, False, encerrada=False)

    def precisa_gravar(self):
        return False

    def abrir(self, caminho):
        pass

    def descartar(self):
        pass

    def mapa_status(self, caminho):
        return {}


def executar(df, certs, caminho="planilha.xlsx", emissor=None):
    """Roda o laco sobre os itens do DataFrame, como `main.processar` fazia."""
    execucao = app._Execucao(PlanilhaInerte(), caminho, CONFIG, emissor)
    execucao.certificados = certs
    app._percorrer(execucao, planilha.itens_pendentes(df))


@pytest.fixture
def diario(monkeypatch):
    """Substitui TODAS as capacidades e registra a coordenacao."""
    reg = Registro()
    sessoes = []

    def abrir_sessao(certificado, auto_select, api_key):
        reg.anotar("login", certificado.subject_cn, auto_select)
        sessao = SessaoFalsa(f"s{len(sessoes) + 1}", registro=reg)
        sessoes.append(sessao)
        return ResultadoDoLogin(AUTENTICADO, sessao)

    def policy(cn):
        reg.anotar("policy", cn)
        return ResultadoDaPolicy(ATIVADA, tem_guardiao=True)

    monkeypatch.setattr(app.maquina, "abrir_sessao", abrir_sessao)
    monkeypatch.setattr(app.maquina, "garantir_policy_do_windows", policy)
    monkeypatch.setattr(app.navegador, "encerrar_no_portal", lambda page: None)
    monkeypatch.setattr(app.representacao, "recuperar_apos_recusa", lambda page: True)
    monkeypatch.setattr(
        app, "_processar_item",
        lambda execucao, item: reg.anotar("cnpj", item.cnpj, execucao.sessao.marca),
    )
    reg.sessoes = sessoes
    return reg


# ── B · caminho feliz ─────────────────────────────────────────────────────────

def test_b_uma_empresa_um_certificado(diario):
    executar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS)

    assert [e[0] for e in diario.eventos] == ["policy", "login", "cnpj", "fechar"]
    assert diario.so("login")[0][1] == CERT_ALFA


def test_c_varios_cnpjs_do_mesmo_certificado_reaproveitam_a_sessao(diario):
    """O motivo de existir do agrupamento por certificado: UM login serve todos."""
    executar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT ALFA"), (CNPJ_3, "CERT ALFA")),
        CERTS,
    )

    assert len(diario.so("login")) == 1
    assert len(diario.so("policy")) == 1
    assert {e[2] for e in diario.so("cnpj")} == {"s1"}, "todos na mesma sessao"
    assert len(diario.so("fechar")) == 1, "so o cleanup final"


def test_d_troca_de_certificado_fecha_a_sessao_e_refaz_policy_e_login(diario):
    executar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT BETA")),
        CERTS,
    )

    nomes = [e[0] for e in diario.eventos]
    assert nomes == ["policy", "login", "cnpj", "fechar", "policy", "login", "cnpj", "fechar"]
    assert [e[1] for e in diario.so("policy")] == [CERT_ALFA, CERT_BETA]
    assert [e[2] for e in diario.so("cnpj")] == ["s1", "s2"]


def test_s_a_policy_e_garantida_ANTES_do_login(diario):
    """A ordem importa: a flag de auto-selecao entra na linha de comando do
    Chrome, entao a policy tem de existir antes de o navegador subir."""
    executar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS)

    nomes = [e[0] for e in diario.eventos]
    assert nomes.index("policy") < nomes.index("login")


def test_i_o_resultado_da_policy_chega_ao_login_como_um_bool(diario, monkeypatch):
    """ResultadoDaPolicy fica na orquestracao; o login recebe so `auto_select`."""
    monkeypatch.setattr(
        app.maquina, "garantir_policy_do_windows",
        lambda cn: ResultadoDaPolicy(JA_ATIVA, tem_guardiao=False),
    )

    executar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS)

    assert diario.so("login")[0][2] is True, "JA_ATIVA tambem e confiavel"


def test_i_policy_indisponivel_ainda_tenta_o_login(diario, monkeypatch):
    """UAC negado nao impede o login — o fallback de janela nativa assume."""
    from automation.policy_certificado import ELEVACAO_RECUSADA

    monkeypatch.setattr(
        app.maquina, "garantir_policy_do_windows",
        lambda cn: ResultadoDaPolicy(ELEVACAO_RECUSADA),
    )

    executar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS)

    assert diario.so("login")[0][2] is False
    assert len(diario.so("cnpj")) == 1, "o CNPJ foi processado assim mesmo"


# ── E · login falha ───────────────────────────────────────────────────────────

def test_e_login_que_nao_autentica_pula_o_cnpj_sem_fechar_sessao(diario, monkeypatch):
    monkeypatch.setattr(
        app.maquina, "abrir_sessao",
        lambda cert, auto, chave: diario.anotar("login", cert.subject_cn, auto)
        or ResultadoDoLogin(NAO_AUTENTICADO),
    )

    executar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT ALFA")), CERTS
    )

    assert len(diario.so("login")) == 2, "tenta de novo no proximo CNPJ"
    assert diario.so("cnpj") == []
    assert diario.so("fechar") == [], "nao ha sessao para fechar"


# ── G · recusa do CNPJ ────────────────────────────────────────────────────────

def test_g_recusa_do_cnpj_mantem_a_sessao_para_o_proximo(diario, monkeypatch):
    """A recusa e do CNPJ, nao da sessao: o certificado segue autenticado."""
    def recusar_o_primeiro(execucao, item):
        cnpj, sessao = item.cnpj, execucao.sessao
        diario.anotar("cnpj", cnpj, sessao.marca)
        if cnpj == CNPJ_1:
            raise app._RecusaDoPortal
        return "concluido"

    monkeypatch.setattr(app, "_processar_item", recusar_o_primeiro)

    executar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT ALFA")), CERTS
    )

    assert len(diario.so("login")) == 1, "nao relogou"
    assert [e[2] for e in diario.so("cnpj")] == ["s1", "s1"], "mesma sessao"
    assert len(diario.so("fechar")) == 1, "so o cleanup final"


def test_g_recusa_com_sessao_irrecuperavel_fecha_e_reloga(diario, monkeypatch):
    monkeypatch.setattr(app.representacao, "recuperar_apos_recusa", lambda page: False)

    def recusar_o_primeiro(execucao, item):
        cnpj, sessao = item.cnpj, execucao.sessao
        diario.anotar("cnpj", cnpj, sessao.marca)
        if cnpj == CNPJ_1:
            raise app._RecusaDoPortal
        return "concluido"

    monkeypatch.setattr(app, "_processar_item", recusar_o_primeiro)

    executar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT ALFA")), CERTS
    )

    assert len(diario.so("login")) == 2, "relogou"
    assert [e[2] for e in diario.so("cnpj")] == ["s1", "s2"]


def test_g_a_recusa_nao_consome_retentativa(diario, monkeypatch):
    """Contraste com o erro tecnico: o CNPJ recusado e pulado, nao retentado."""
    def sempre_recusa(execucao, item):
        cnpj, sessao = item.cnpj, execucao.sessao
        diario.anotar("cnpj", cnpj, sessao.marca)
        raise app._RecusaDoPortal

    monkeypatch.setattr(app, "_processar_item", sempre_recusa)

    executar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS)

    assert len(diario.so("cnpj")) == 1, "uma vez so"


# ── H · I · anti-bot e nao-confirmado chegam como erro tecnico ────────────────

def test_h_anti_bot_e_nao_confirmado_entram_no_retry_do_cnpj(diario, monkeypatch):
    """Os dois desfechos viram exception no adapter e caem no `except Exception`
    do laco: fecham a sessao, relogam e consomem retentativa."""
    from automation.representacao import AntiBotEsgotado

    tentativas = []

    def falhar_uma_vez(execucao, item):
        cnpj, sessao = item.cnpj, execucao.sessao
        diario.anotar("cnpj", cnpj, sessao.marca)
        tentativas.append(cnpj)
        if len(tentativas) == 1:
            raise AntiBotEsgotado("esgotou")
        return "concluido"

    monkeypatch.setattr(app, "_processar_item", falhar_uma_vez)

    executar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS)

    assert len(diario.so("cnpj")) == 2, "retentou o MESMO CNPJ"
    assert len(diario.so("login")) == 2, "com sessao nova"
    assert [e[2] for e in diario.so("cnpj")] == ["s1", "s2"]


# ── O · P · retry do CNPJ ─────────────────────────────────────────────────────

def test_o_erro_tecnico_fecha_a_sessao_e_retenta_o_mesmo_cnpj(diario, monkeypatch):
    tentativas = []

    def falhar_uma_vez(execucao, item):
        tentativas.append(item.cnpj)
        diario.anotar("cnpj", item.cnpj, execucao.sessao.marca)
        if len(tentativas) == 1:
            raise RuntimeError("erro tecnico")

    monkeypatch.setattr(app, "_processar_item", falhar_uma_vez)

    executar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS)

    assert tentativas == [CNPJ_1, CNPJ_1]
    assert len(diario.so("fechar")) == 2, "uma no erro, uma no fim"


def test_p_esgotar_o_retry_pula_o_cnpj(diario, monkeypatch):
    def sempre_falha(execucao, item):
        cnpj, sessao = item.cnpj, execucao.sessao
        diario.anotar("cnpj", cnpj, sessao.marca)
        raise RuntimeError("erro tecnico")

    monkeypatch.setattr(app, "_processar_item", sempre_falha)

    executar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT ALFA")), CERTS
    )

    por_cnpj = [e[1] for e in diario.so("cnpj")]
    assert por_cnpj.count(CNPJ_1) == 2, "duas tentativas, e para"
    assert CNPJ_2 in por_cnpj, "o proximo CNPJ ainda e tentado"


def test_p_o_contador_de_retentativa_e_por_cnpj(diario, monkeypatch):
    """Dois CNPJs falhando nao somam num contador unico."""
    def sempre_falha(execucao, item):
        cnpj, sessao = item.cnpj, execucao.sessao
        diario.anotar("cnpj", cnpj, sessao.marca)
        raise RuntimeError("erro tecnico")

    monkeypatch.setattr(app, "_processar_item", sempre_falha)

    executar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT ALFA")), CERTS
    )

    por_cnpj = [e[1] for e in diario.so("cnpj")]
    assert por_cnpj.count(CNPJ_1) == por_cnpj.count(CNPJ_2) == 2


# ── Certificado ausente ───────────────────────────────────────────────────────

def test_certificado_nao_instalado_pula_as_linhas_sem_login(diario, capsys):
    """O aviso era um `print`; hoje e um EVENTO — e o app nao imprime."""
    codigos = []
    executar(planilha_com((CNPJ_1, "CERT INEXISTENTE")), CERTS,
             emissor=lambda e: codigos.append(e.codigo))

    assert diario.eventos == [], "nem policy, nem login, nem CNPJ"
    assert eventos.CERTIFICADO_NAO_INSTALADO in codigos
    assert capsys.readouterr().out == ""


def test_cnpj_invalido_e_ignorado_antes_de_qualquer_navegacao(diario):
    executar(planilha_com(("nan", "CERT ALFA")), CERTS)

    assert diario.so("cnpj") == []


# ── A · R · nada a fazer e cleanup ────────────────────────────────────────────

def test_a_planilha_sem_linhas_nao_faz_nada(diario):
    executar(planilha_com(), CERTS)

    assert diario.eventos == []


def test_a_na_pratica_o_app_nem_chega_a_logar_sem_pendencias():
    """O caminho real do "nada a fazer": sem itens pendentes o app retorna ANTES
    da policy e do login, e a planilha e fechada no `finally`."""
    import inspect

    fonte = inspect.getsource(app.executar)
    trecho = fonte[fonte.index("itens = planilha.itens_pendentes"):]

    assert "if not itens:" in trecho
    assert trecho.index("return") < trecho.index("_percorrer")
    assert "descartar()" in trecho


def test_r_o_cleanup_final_fecha_a_sessao_que_sobrou(diario):
    executar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS)

    assert diario.so("fechar")[-1] == ("fechar", "page-s1")


def test_r_sem_sessao_aberta_nao_ha_cleanup(diario, monkeypatch):
    monkeypatch.setattr(
        app.maquina, "abrir_sessao", lambda c, a, k: ResultadoDoLogin(NAO_AUTENTICADO)
    )

    executar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS)

    assert diario.so("fechar") == []


# ── K · L · M · N · a tabela de persistencia ─────────────────────────────────

class PlanilhaEspia(PlanilhaInerte):
    """As primitivas de celula e aba viram espioes; a camada semantica e a real."""

    def __init__(self, registro, retomada=None):
        super().__init__()
        self.registro = registro
        self._retomada = retomada or planilha.RetomadaDaLinha(False, False, False)
        self._rotulos = {planilha.COL_STATUS_DCTFWEB: "D",
                         planilha.COL_STATUS_PROCESSOS: "E"}

    def retomada(self, caminho, cnpj, encerra_linha):
        return self._retomada

    def escrever_status(self, cnpj, valor, coluna):
        self.registro.anotar(self._rotulos[coluna], valor)
        return True

    def anexar_debitos(self, dados):
        return self._anexar("aba_debitos", dados)

    def anexar_processos(self, dados):
        return self._anexar("aba_processos", dados)

    def _anexar(self, rotulo, dados):
        self.registro.anotar(rotulo, len(dados))
        return list(range(2, 2 + len(dados)))

    def precisa_gravar(self):
        return True

    def gravar(self):
        self.registro.anotar("gravar")

    def marcar_gravado(self):
        pass

    registrar_debitos = planilha.SessaoPlanilha.registrar_debitos
    registrar_processos = planilha.SessaoPlanilha.registrar_processos
    registrar_sem_debitos = planilha.SessaoPlanilha.registrar_sem_debitos
    registrar_sem_processos = planilha.SessaoPlanilha.registrar_sem_processos
    registrar_debitos_concluidos = planilha.SessaoPlanilha.registrar_debitos_concluidos
    registrar_recusa_do_portal = planilha.SessaoPlanilha.registrar_recusa_do_portal
    registrar_debitos_nao_compensaveis = (
        planilha.SessaoPlanilha.registrar_debitos_nao_compensaveis
    )


@pytest.fixture
def escritas():
    """Registra exatamente o que a orquestracao manda gravar, e quando.

    CHARACTERIZATION_TARGET_CHANGE (fatia 9B): a substituicao desceu dos
    wrappers de coluna do `main` — que sumiram — para as PRIMITIVAS da sessao de
    planilha. As gravacoes registradas, os valores e a ordem sao os mesmos; a
    diferenca e que agora o caminho semantico real e exercitado no meio.
    """
    return Registro()


def _execucao(escritas, retomada=None, sessao=None):
    execucao = app._Execucao(PlanilhaEspia(escritas, retomada), "p.xlsx", CONFIG, None)
    execucao.sessao = sessao or SessaoFalsa()
    return execucao


def verificar_pendencias(sessao, cnpj, caminho, escritas,
                         skip_dctfweb=False, skip_processo=False):
    """O antigo `main.verificar_pendencias`. Os `skip_*` nascem da retomada."""
    execucao = _execucao(escritas, sessao=sessao)
    app._consultar_situacao(
        execucao,
        planilha.ItemPendente(posicao=0, cnpj=cnpj, certificado="CERT"),
        planilha.RetomadaDaLinha(skip_dctfweb, skip_processo, encerrada=False),
    )


def processar_item(sessao, cnpj, escritas, retomada=None):
    """O antigo `main.processar_cnpj`. Devolve os codigos emitidos."""
    codigos = []
    execucao = app._Execucao(PlanilhaEspia(escritas, retomada), "p.xlsx", CONFIG,
                             lambda e: codigos.append(e.codigo))
    execucao.sessao = sessao
    app._processar_item(
        execucao, planilha.ItemPendente(posicao=0, cnpj=cnpj, certificado="CERT")
    )
    return codigos


def _retomada(val_d, val_e):
    """A retomada como a planilha a produziria para estes valores de D e E."""
    from automation.planilha import RetomadaDaLinha
    from automation.status_portal import status_encerra_linha

    return RetomadaDaLinha(
        dctfweb_feito=bool(val_d),
        processos_feitos=bool(val_e),
        encerrada=status_encerra_linha(val_d),
    )


def situacao(**kwargs):
    from automation.consulta_fiscal import COM_PENDENCIA, SituacaoFiscal

    return SituacaoFiscal(kwargs.pop("situacao", COM_PENDENCIA), **kwargs)


def extracao(n):
    from automation.consulta_fiscal import ExtracaoFiscal

    return ExtracaoFiscal(linhas=tuple({"cnpj": CNPJ_1} for _ in range(n)))


def test_k_sem_pendencia_grava_os_dois_status_e_nao_abre_aba(escritas, monkeypatch):
    from automation.consulta_fiscal import SEM_PENDENCIA

    monkeypatch.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(situacao=SEM_PENDENCIA))

    verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx", escritas)

    assert escritas.eventos == [("D", "Sem débitos"), ("E", "Sem Processos")]


def test_k_sem_botao_de_acao_e_nao_compensavel(escritas, monkeypatch):
    monkeypatch.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=False, tem_processo=False))

    verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx", escritas)

    assert escritas.eventos == [("D", "Débitos não compensáveis"), ("E", "Sem Processos")]


def test_k_dctfweb_grava_aba_e_depois_a_coluna_d(escritas, monkeypatch):
    """A ORDEM e o contrato: detalhe primeiro, status depois."""
    monkeypatch.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=True, tem_processo=False))
    monkeypatch.setattr(app.consulta_fiscal, "consultar_dctfweb",
                        lambda sessao, cnpj: extracao(3))

    verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx", escritas)

    assert escritas.eventos[:2] == [("aba_debitos", 3), ("D", "Concluído")]


def test_l_processos_grava_aba_e_depois_a_coluna_e(escritas, monkeypatch):
    monkeypatch.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=False, tem_processo=True))
    monkeypatch.setattr(app.consulta_fiscal, "consultar_processos",
                        lambda sessao, cnpj: extracao(2))

    verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx", escritas)

    assert ("aba_processos", 2) in escritas.eventos
    assert ("E", "Concluído") in escritas.eventos
    assert ("D", "Concluído") in escritas.eventos, "sem DCTFWeb, o Fiscal fecha o D"


def test_m_resumability_o_d_e_gravado_ANTES_de_os_processos_comecarem(
    escritas, monkeypatch
):
    """O invariante da fatia 4, agora no nivel da orquestracao.

    Se os Processos caem depois de o DCTFWeb terminar, a coluna D ja foi
    escrita — e a proxima execucao pula o DCTFWeb.
    """
    monkeypatch.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=True, tem_processo=True))
    monkeypatch.setattr(app.consulta_fiscal, "consultar_dctfweb",
                        lambda sessao, cnpj: extracao(1))

    def processos_caem(sessao, cnpj):
        raise RuntimeError("portal caiu no meio dos processos")

    monkeypatch.setattr(app.consulta_fiscal, "consultar_processos", processos_caem)

    with pytest.raises(RuntimeError):
        verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx", escritas)

    assert escritas.eventos == [("aba_debitos", 1), ("D", "Concluído")]
    assert not escritas.so("E"), "a coluna E nao foi tocada"


def test_m_o_gravar_acontece_uma_vez_por_cnpj_no_finally(escritas, monkeypatch):
    """Unico toque no disco por CNPJ, e ele acontece mesmo em falha."""
    monkeypatch.setattr(
        app.representacao, "representar",
        lambda *a, **k: app.representacao.ResultadoDaRepresentacao(
            app.representacao.REPRESENTADO
        ),
    )
    monkeypatch.setattr(app, "_consultar_situacao",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("caiu")))

    with pytest.raises(RuntimeError):
        processar_item(SessaoFalsa(), CNPJ_1, escritas)

    assert escritas.so("gravar") == [("gravar",)]


def test_l_skip_d_pula_o_dctfweb_e_so_faz_processos(escritas, monkeypatch):
    monkeypatch.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=True, tem_processo=True))
    monkeypatch.setattr(app.consulta_fiscal, "consultar_dctfweb",
                        lambda *a, **k: pytest.fail("nao devia consultar DCTFWeb"))
    monkeypatch.setattr(app.consulta_fiscal, "consultar_processos",
                        lambda sessao, cnpj: extracao(1))

    verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx", escritas, skip_dctfweb=True)

    assert not escritas.so("aba_debitos")
    assert ("E", "Concluído") in escritas.eventos
    assert not escritas.so("D"), "D ja estava preenchida"


def test_l_skip_e_pula_os_processos(escritas, monkeypatch):
    monkeypatch.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=True, tem_processo=True))
    monkeypatch.setattr(app.consulta_fiscal, "consultar_dctfweb",
                        lambda sessao, cnpj: extracao(2))
    monkeypatch.setattr(app.consulta_fiscal, "consultar_processos",
                        lambda *a, **k: pytest.fail("nao devia consultar Processos"))

    verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx", escritas, skip_processo=True)

    assert ("D", "Concluído") in escritas.eventos
    assert not escritas.so("E")


# ── Q · FISCAL_UNKNOWN_SEMANTICS ─────────────────────────────────────────────

def test_q_situacao_nao_reconhecida_nao_grava_nada(escritas, monkeypatch):
    """FISCAL_UNKNOWN_SEMANTICS: nada e gravado, entao a linha volta pendente na
    proxima execucao — e voltara para sempre enquanto o texto nao for
    reconhecido. Caracterizado, nao corrigido."""
    from automation.consulta_fiscal import NAO_RECONHECIDA

    monkeypatch.setattr(app.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(situacao=NAO_RECONHECIDA))

    verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx", escritas)

    assert escritas.eventos == [], "nada gravado — a linha volta"


# ── Skip pela leitura da planilha ────────────────────────────────────────────

def test_l_linha_ja_concluida_nao_navega(escritas, monkeypatch):
    monkeypatch.setattr(app.representacao, "representar",
                        lambda *a, **k: pytest.fail("nao devia representar"))

    codigos = processar_item(SessaoFalsa(), CNPJ_1, escritas,
                             _retomada("Concluído", "Concluído"))

    assert codigos == [eventos.ITEM_JA_CONCLUIDO]
    assert escritas.eventos == [], "nem gravar"


def test_l_status_terminal_em_d_encerra_sem_navegar(escritas, monkeypatch):
    monkeypatch.setattr(app.representacao, "representar",
                        lambda *a, **k: pytest.fail("nao devia representar"))

    codigos = processar_item(SessaoFalsa(), CNPJ_1, escritas,
                             _retomada("Procuração sem autorização", ""))

    assert codigos == [eventos.ITEM_JA_ENCERRADO]
