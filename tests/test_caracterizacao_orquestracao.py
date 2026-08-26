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

import main
from automation.login import AUTENTICADO, NAO_AUTENTICADO, ResultadoDoLogin
from automation.policy_certificado import ATIVADA, JA_ATIVA, ResultadoDaPolicy

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
    def __init__(self, marca="s1"):
        self.marca = marca
        self.playwright = f"pw-{marca}"
        self.contexto = f"ctx-{marca}"
        self.pagina = f"page-{marca}"


class Registro:
    """O que a orquestracao fez, na ordem."""

    def __init__(self):
        self.eventos = []

    def anotar(self, *evento):
        self.eventos.append(evento)

    def so(self, nome):
        return [e for e in self.eventos if e[0] == nome]


@pytest.fixture
def diario(monkeypatch):
    """Substitui TODAS as capacidades e registra a coordenacao."""
    reg = Registro()
    sessoes = []

    def autenticar(cn, serial, auto_select):
        reg.anotar("login", cn, auto_select)
        sessao = SessaoFalsa(f"s{len(sessoes) + 1}")
        sessoes.append(sessao)
        return ResultadoDoLogin(AUTENTICADO, sessao)

    def policy(cn):
        reg.anotar("policy", cn)
        return ResultadoDaPolicy(ATIVADA, tem_guardiao=True)

    def fechar(pw, ctx, page=None):
        reg.anotar("fechar", page)

    monkeypatch.setattr(main, "_autenticar", autenticar)
    monkeypatch.setattr(main.cert_windows, "iniciar_guarda_detalhado", policy)
    monkeypatch.setattr(main, "_fechar_navegador", fechar)
    monkeypatch.setattr(main, "atualizar_env_certificado", lambda cn: None)
    monkeypatch.setattr(main.representacao, "recuperar_apos_recusa", lambda page: True)
    monkeypatch.setattr(
        main, "processar_cnpj",
        lambda sessao, cnpj, caminho: reg.anotar("cnpj", cnpj, sessao.marca) or "concluido",
    )
    reg.sessoes = sessoes
    return reg


# ── B · caminho feliz ─────────────────────────────────────────────────────────

def test_b_uma_empresa_um_certificado(diario):
    main.processar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS, "planilha.xlsx")

    assert [e[0] for e in diario.eventos] == ["policy", "login", "cnpj", "fechar"]
    assert diario.so("login")[0][1] == CERT_ALFA


def test_c_varios_cnpjs_do_mesmo_certificado_reaproveitam_a_sessao(diario):
    """O motivo de existir do agrupamento por certificado: UM login serve todos."""
    main.processar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT ALFA"), (CNPJ_3, "CERT ALFA")),
        CERTS, "planilha.xlsx",
    )

    assert len(diario.so("login")) == 1
    assert len(diario.so("policy")) == 1
    assert {e[2] for e in diario.so("cnpj")} == {"s1"}, "todos na mesma sessao"
    assert len(diario.so("fechar")) == 1, "so o cleanup final"


def test_d_troca_de_certificado_fecha_a_sessao_e_refaz_policy_e_login(diario):
    main.processar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT BETA")),
        CERTS, "planilha.xlsx",
    )

    nomes = [e[0] for e in diario.eventos]
    assert nomes == ["policy", "login", "cnpj", "fechar", "policy", "login", "cnpj", "fechar"]
    assert [e[1] for e in diario.so("policy")] == [CERT_ALFA, CERT_BETA]
    assert [e[2] for e in diario.so("cnpj")] == ["s1", "s2"]


def test_s_a_policy_e_garantida_ANTES_do_login(diario):
    """A ordem importa: a flag de auto-selecao entra na linha de comando do
    Chrome, entao a policy tem de existir antes de o navegador subir."""
    main.processar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS, "planilha.xlsx")

    nomes = [e[0] for e in diario.eventos]
    assert nomes.index("policy") < nomes.index("login")


def test_i_o_resultado_da_policy_chega_ao_login_como_um_bool(diario, monkeypatch):
    """ResultadoDaPolicy fica na orquestracao; o login recebe so `auto_select`."""
    monkeypatch.setattr(
        main.cert_windows, "iniciar_guarda_detalhado",
        lambda cn: ResultadoDaPolicy(JA_ATIVA, tem_guardiao=False),
    )

    main.processar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS, "planilha.xlsx")

    assert diario.so("login")[0][2] is True, "JA_ATIVA tambem e confiavel"


def test_i_policy_indisponivel_ainda_tenta_o_login(diario, monkeypatch):
    """UAC negado nao impede o login — o fallback de janela nativa assume."""
    from automation.policy_certificado import ELEVACAO_RECUSADA

    monkeypatch.setattr(
        main.cert_windows, "iniciar_guarda_detalhado",
        lambda cn: ResultadoDaPolicy(ELEVACAO_RECUSADA),
    )

    main.processar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS, "planilha.xlsx")

    assert diario.so("login")[0][2] is False
    assert len(diario.so("cnpj")) == 1, "o CNPJ foi processado assim mesmo"


# ── E · login falha ───────────────────────────────────────────────────────────

def test_e_login_que_nao_autentica_pula_o_cnpj_sem_fechar_sessao(diario, monkeypatch):
    monkeypatch.setattr(
        main, "_autenticar",
        lambda cn, serial, auto: diario.anotar("login", cn, auto) or ResultadoDoLogin(
            NAO_AUTENTICADO
        ),
    )

    main.processar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT ALFA")), CERTS, "planilha.xlsx"
    )

    assert len(diario.so("login")) == 2, "tenta de novo no proximo CNPJ"
    assert diario.so("cnpj") == []
    assert diario.so("fechar") == [], "nao ha sessao para fechar"


# ── G · recusa do CNPJ ────────────────────────────────────────────────────────

def test_g_recusa_do_cnpj_mantem_a_sessao_para_o_proximo(diario, monkeypatch):
    """A recusa e do CNPJ, nao da sessao: o certificado segue autenticado."""
    def recusar_o_primeiro(sessao, cnpj, caminho):
        diario.anotar("cnpj", cnpj, sessao.marca)
        if cnpj == CNPJ_1:
            raise main.FalhaPermanente("recusado", status_coluna_d="Procuração sem autorização")
        return "concluido"

    monkeypatch.setattr(main, "processar_cnpj", recusar_o_primeiro)

    main.processar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT ALFA")), CERTS, "planilha.xlsx"
    )

    assert len(diario.so("login")) == 1, "nao relogou"
    assert [e[2] for e in diario.so("cnpj")] == ["s1", "s1"], "mesma sessao"
    assert len(diario.so("fechar")) == 1, "so o cleanup final"


def test_g_recusa_com_sessao_irrecuperavel_fecha_e_reloga(diario, monkeypatch):
    monkeypatch.setattr(main.representacao, "recuperar_apos_recusa", lambda page: False)

    def recusar_o_primeiro(sessao, cnpj, caminho):
        diario.anotar("cnpj", cnpj, sessao.marca)
        if cnpj == CNPJ_1:
            raise main.FalhaPermanente("recusado")
        return "concluido"

    monkeypatch.setattr(main, "processar_cnpj", recusar_o_primeiro)

    main.processar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT ALFA")), CERTS, "planilha.xlsx"
    )

    assert len(diario.so("login")) == 2, "relogou"
    assert [e[2] for e in diario.so("cnpj")] == ["s1", "s2"]


def test_g_a_recusa_nao_consome_retentativa(diario, monkeypatch):
    """Contraste com o erro tecnico: o CNPJ recusado e pulado, nao retentado."""
    def sempre_recusa(sessao, cnpj, caminho):
        diario.anotar("cnpj", cnpj, sessao.marca)
        raise main.FalhaPermanente("recusado")

    monkeypatch.setattr(main, "processar_cnpj", sempre_recusa)

    main.processar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS, "planilha.xlsx")

    assert len(diario.so("cnpj")) == 1, "uma vez so"


# ── H · I · anti-bot e nao-confirmado chegam como erro tecnico ────────────────

def test_h_anti_bot_e_nao_confirmado_entram_no_retry_do_cnpj(diario, monkeypatch):
    """Os dois desfechos viram exception no adapter e caem no `except Exception`
    do laco: fecham a sessao, relogam e consomem retentativa."""
    from automation.representacao import AntiBotEsgotado

    tentativas = []

    def falhar_uma_vez(sessao, cnpj, caminho):
        diario.anotar("cnpj", cnpj, sessao.marca)
        tentativas.append(cnpj)
        if len(tentativas) == 1:
            raise AntiBotEsgotado("esgotou")
        return "concluido"

    monkeypatch.setattr(main, "processar_cnpj", falhar_uma_vez)

    main.processar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS, "planilha.xlsx")

    assert len(diario.so("cnpj")) == 2, "retentou o MESMO CNPJ"
    assert len(diario.so("login")) == 2, "com sessao nova"
    assert [e[2] for e in diario.so("cnpj")] == ["s1", "s2"]


# ── O · P · retry do CNPJ ─────────────────────────────────────────────────────

def test_o_erro_tecnico_fecha_a_sessao_e_retenta_o_mesmo_cnpj(diario, monkeypatch):
    tentativas = []

    def falhar_uma_vez(sessao, cnpj, caminho):
        tentativas.append(cnpj)
        diario.anotar("cnpj", cnpj, sessao.marca)
        if len(tentativas) == 1:
            raise RuntimeError("erro tecnico")
        return "concluido"

    monkeypatch.setattr(main, "processar_cnpj", falhar_uma_vez)

    main.processar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS, "planilha.xlsx")

    assert tentativas == [CNPJ_1, CNPJ_1]
    assert len(diario.so("fechar")) == 2, "uma no erro, uma no fim"


def test_p_esgotar_o_retry_pula_o_cnpj(diario, monkeypatch):
    def sempre_falha(sessao, cnpj, caminho):
        diario.anotar("cnpj", cnpj, sessao.marca)
        raise RuntimeError("erro tecnico")

    monkeypatch.setattr(main, "processar_cnpj", sempre_falha)

    main.processar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT ALFA")), CERTS, "planilha.xlsx"
    )

    por_cnpj = [e[1] for e in diario.so("cnpj")]
    assert por_cnpj.count(CNPJ_1) == 2, "duas tentativas, e para"
    assert CNPJ_2 in por_cnpj, "o proximo CNPJ ainda e tentado"


def test_p_o_contador_de_retentativa_e_por_cnpj(diario, monkeypatch):
    """Dois CNPJs falhando nao somam num contador unico."""
    def sempre_falha(sessao, cnpj, caminho):
        diario.anotar("cnpj", cnpj, sessao.marca)
        raise RuntimeError("erro tecnico")

    monkeypatch.setattr(main, "processar_cnpj", sempre_falha)

    main.processar(
        planilha_com((CNPJ_1, "CERT ALFA"), (CNPJ_2, "CERT ALFA")), CERTS, "planilha.xlsx"
    )

    por_cnpj = [e[1] for e in diario.so("cnpj")]
    assert por_cnpj.count(CNPJ_1) == por_cnpj.count(CNPJ_2) == 2


# ── Certificado ausente ───────────────────────────────────────────────────────

def test_certificado_nao_instalado_pula_as_linhas_sem_login(diario, capsys):
    main.processar(planilha_com((CNPJ_1, "CERT INEXISTENTE")), CERTS, "planilha.xlsx")

    assert diario.eventos == [], "nem policy, nem login, nem CNPJ"
    assert "não está instalado" in capsys.readouterr().out


def test_cnpj_invalido_e_ignorado_antes_de_qualquer_navegacao(diario):
    main.processar(planilha_com(("nan", "CERT ALFA")), CERTS, "planilha.xlsx")

    assert diario.so("cnpj") == []


# ── A · R · nada a fazer e cleanup ────────────────────────────────────────────

def test_a_planilha_sem_linhas_nao_faz_nada(diario):
    main.processar(planilha_com(), CERTS, "planilha.xlsx")

    assert diario.eventos == []


def test_a_na_pratica_main_nem_chega_a_chamar_processar_sem_pendencias():
    """O caminho real do "nada a fazer": `main()` fecha a planilha e retorna
    ANTES de descobrir certificado, policy ou login."""
    import pathlib

    fonte = (pathlib.Path(main.__file__)).read_text(encoding="utf-8-sig")
    trecho = fonte[fonte.index("if df.empty:"):]
    trecho = trecho[: trecho.index("resumo = planilha.certificados_dos_itens")]

    assert "fechar_planilha()" in trecho
    assert "return" in trecho


def test_r_o_cleanup_final_fecha_a_sessao_que_sobrou(diario):
    main.processar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS, "planilha.xlsx")

    assert diario.so("fechar")[-1] == ("fechar", "page-s1")


def test_r_sem_sessao_aberta_nao_ha_cleanup(diario, monkeypatch):
    monkeypatch.setattr(
        main, "_autenticar", lambda cn, s, a: ResultadoDoLogin(NAO_AUTENTICADO)
    )

    main.processar(planilha_com((CNPJ_1, "CERT ALFA")), CERTS, "planilha.xlsx")

    assert diario.so("fechar") == []


# ── K · L · M · N · a tabela de persistencia ─────────────────────────────────

@pytest.fixture
def escritas(monkeypatch):
    """Registra exatamente o que a orquestracao manda gravar, e quando.

    CHARACTERIZATION_TARGET_CHANGE (fatia 9B): a substituicao desceu dos
    wrappers de coluna do `main` — que sumiram — para as PRIMITIVAS da sessao de
    planilha. As gravacoes registradas, os valores e a ordem sao os mesmos; a
    diferenca e que agora o caminho semantico real e exercitado no meio.
    """
    from automation import planilha as _planilha

    reg = Registro()
    rotulos = {_planilha.COL_STATUS_DCTFWEB: "D", _planilha.COL_STATUS_PROCESSOS: "E"}

    def anexar(rotulo):
        def anexar_dados(dados):
            reg.anotar(rotulo, len(dados))
            return list(range(2, 2 + len(dados)))
        return anexar_dados

    monkeypatch.setattr(main, "_wb_sessao", lambda c: None)
    monkeypatch.setattr(main._SESSAO, "escrever_status",
                        lambda cnpj, v, coluna: reg.anotar(rotulos[coluna], v) or True)
    monkeypatch.setattr(main._SESSAO, "anexar_debitos", anexar("aba_debitos"))
    monkeypatch.setattr(main._SESSAO, "anexar_processos", anexar("aba_processos"))
    monkeypatch.setattr(main, "salvar_planilha", lambda: reg.anotar("gravar"))
    return reg


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

    monkeypatch.setattr(main.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(situacao=SEM_PENDENCIA))

    assert main.verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx") == "sem_pendencia"
    assert escritas.eventos == [("D", "Sem débitos"), ("E", "Sem Processos")]


def test_k_sem_botao_de_acao_e_nao_compensavel(escritas, monkeypatch):
    monkeypatch.setattr(main.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=False, tem_processo=False))

    assert main.verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx") == "nao_compensavel"
    assert escritas.eventos == [("D", "Débitos não compensáveis"), ("E", "Sem Processos")]


def test_k_dctfweb_grava_aba_e_depois_a_coluna_d(escritas, monkeypatch):
    """A ORDEM e o contrato: detalhe primeiro, status depois."""
    monkeypatch.setattr(main.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=True, tem_processo=False))
    monkeypatch.setattr(main.consulta_fiscal, "consultar_dctfweb",
                        lambda sessao, cnpj: extracao(3))

    main.verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx")

    assert escritas.eventos[:2] == [("aba_debitos", 3), ("D", "Concluído")]


def test_l_processos_grava_aba_e_depois_a_coluna_e(escritas, monkeypatch):
    monkeypatch.setattr(main.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=False, tem_processo=True))
    monkeypatch.setattr(main.consulta_fiscal, "consultar_processos",
                        lambda sessao, cnpj: extracao(2))

    main.verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx")

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
    monkeypatch.setattr(main.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=True, tem_processo=True))
    monkeypatch.setattr(main.consulta_fiscal, "consultar_dctfweb",
                        lambda sessao, cnpj: extracao(1))

    def processos_caem(sessao, cnpj):
        raise RuntimeError("portal caiu no meio dos processos")

    monkeypatch.setattr(main.consulta_fiscal, "consultar_processos", processos_caem)

    with pytest.raises(RuntimeError):
        main.verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx")

    assert escritas.eventos == [("aba_debitos", 1), ("D", "Concluído")]
    assert not escritas.so("E"), "a coluna E nao foi tocada"


def test_m_o_gravar_acontece_uma_vez_por_cnpj_no_finally(escritas, monkeypatch):
    """Unico toque no disco por CNPJ, e ele acontece mesmo em falha."""
    monkeypatch.setattr(main, "retomada_da_linha", lambda c, cnpj: _retomada("", ""))
    monkeypatch.setattr(
        main.representacao, "representar",
        lambda *a, **k: main.representacao.ResultadoDaRepresentacao(
            main.representacao.REPRESENTADO
        ),
    )
    monkeypatch.setattr(main, "verificar_pendencias",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("caiu")))

    with pytest.raises(RuntimeError):
        main.processar_cnpj(SessaoFalsa(), CNPJ_1, "p.xlsx")

    assert escritas.so("gravar") == [("gravar",)]


def test_l_skip_d_pula_o_dctfweb_e_so_faz_processos(escritas, monkeypatch):
    monkeypatch.setattr(main.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=True, tem_processo=True))
    monkeypatch.setattr(main.consulta_fiscal, "consultar_dctfweb",
                        lambda *a, **k: pytest.fail("nao devia consultar DCTFWeb"))
    monkeypatch.setattr(main.consulta_fiscal, "consultar_processos",
                        lambda sessao, cnpj: extracao(1))

    main.verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx", skip_dctfweb=True)

    assert not escritas.so("aba_debitos")
    assert ("E", "Concluído") in escritas.eventos
    assert not escritas.so("D"), "D ja estava preenchida"


def test_l_skip_e_pula_os_processos(escritas, monkeypatch):
    monkeypatch.setattr(main.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(tem_dctfweb=True, tem_processo=True))
    monkeypatch.setattr(main.consulta_fiscal, "consultar_dctfweb",
                        lambda sessao, cnpj: extracao(2))
    monkeypatch.setattr(main.consulta_fiscal, "consultar_processos",
                        lambda *a, **k: pytest.fail("nao devia consultar Processos"))

    main.verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx", skip_processo=True)

    assert ("D", "Concluído") in escritas.eventos
    assert not escritas.so("E")


# ── Q · FISCAL_UNKNOWN_SEMANTICS ─────────────────────────────────────────────

def test_q_situacao_nao_reconhecida_nao_grava_nada(escritas, monkeypatch):
    """FISCAL_UNKNOWN_SEMANTICS: nada e gravado, entao a linha volta pendente na
    proxima execucao — e voltara para sempre enquanto o texto nao for
    reconhecido. Caracterizado, nao corrigido."""
    from automation.consulta_fiscal import NAO_RECONHECIDA

    monkeypatch.setattr(main.consulta_fiscal, "ler_situacao",
                        lambda sessao: situacao(situacao=NAO_RECONHECIDA))

    assert main.verificar_pendencias(SessaoFalsa(), CNPJ_1, "p.xlsx") == "desconhecido"
    assert escritas.eventos == []


# ── Skip pela leitura da planilha ────────────────────────────────────────────

def test_l_linha_ja_concluida_nao_navega(escritas, monkeypatch):
    monkeypatch.setattr(main, "retomada_da_linha",
                        lambda c, cnpj: _retomada("Concluído", "Concluído"))
    monkeypatch.setattr(main.representacao, "representar",
                        lambda *a, **k: pytest.fail("nao devia representar"))

    assert main.processar_cnpj(SessaoFalsa(), CNPJ_1, "p.xlsx") == "ja_processado"
    assert escritas.eventos == [], "nem gravar"


def test_l_status_terminal_em_d_encerra_sem_navegar(escritas, monkeypatch):
    monkeypatch.setattr(
        main, "retomada_da_linha",
        lambda c, cnpj: _retomada("Procuração sem autorização", ""),
    )
    monkeypatch.setattr(main.representacao, "representar",
                        lambda *a, **k: pytest.fail("nao devia representar"))

    assert main.processar_cnpj(SessaoFalsa(), CNPJ_1, "p.xlsx") == "ja_processado"
