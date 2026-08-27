"""A instalacao pode terminar com as colmeias discordando — e ser aceita.

Escrito ANTES de qualquer mudanca da fatia 13A.1.

A 13A fechou a mutacao destrutiva: nao apagamos mais o que nao e nosso. Mas ela
resolveu o conflito POR COLMEIA — "esta colmeia sai inteira, a outra continua
sendo escrita" — e e exatamente ai que este arquivo aponta:

    HKCU com estado incompativel  +  HKLM com a nossa policy

e um estado divergente que a NOSSA tentativa de instalacao produziu. Ele e
diferente do PARTIAL_POLICY_STATE historico, que era estado ENCONTRADO no
startup: este e estado CRIADO por nos, numa corrida.

E a confirmacao de ativacao nao o percebe, porque ela pergunta uma coisa fraca.

Nenhum teste toca o registro real, pede UAC, lanca processo elevado ou abre
Chrome. Todos os CNs sao ficticios.
"""
import inspect
import json

import pytest
from registro_falso import RegistroFalso

import cert_windows
from automation import policy_certificado

CN_NOSSO = "ALFA FICTICIA LTDA:11111111000191"
CN_ALHEIO = "BETA FICTICIA SA:22222222000172"
CAMINHO = cert_windows.REG_PATH
QUANTAS = len(cert_windows.CERT_URLS)


def _vivo(_controle):
    """O guardiao esta vivo — CHARACTERIZATION_TARGET_CHANGE da fatia 13A.4.

    O protocolo passou a exigir a vida do PROCESSO guardiao, e nao so a policy
    no registro. Estes testes sempre pressupuseram um guardiao vivo: nao havia
    outro estado possivel. Dize-lo explicitamente preserva exatamente o que cada
    assercao deste arquivo ja significava antes da fatia.
    """
    from automation.policy_certificado import GUARDIAO_VIVO

    return GUARDIAO_VIVO


@pytest.fixture
def registro(monkeypatch):
    falso = RegistroFalso()
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))
    return falso


def externo(cn=CN_ALHEIO, url="https://intranet.exemplo.invalido"):
    return json.dumps({"pattern": url, "filter": {"SUBJECT": {"CN": cn}}})


def nossa(registro, colmeia, cn=CN_NOSSO):
    registro.dados[colmeia][CAMINHO] = dict(cert_windows._valores_esperados(cn))


# ── §1 · conflito surge em HKCU depois da revalidacao ─────────────────────────

def test_1_a_revalidacao_ve_as_duas_colmeias_vazias(registro):
    """A pre-condicao do cenario: no instante da revalidacao, CRIAR."""
    decisao = policy_certificado.avaliar_estado_inicial(
        cert_windows.inventario_da_policy(), CN_NOSSO, tuple(cert_windows.CERT_URLS)
    )

    assert decisao.decisao == policy_certificado.CRIAR


def test_1a_hkcu_e_preservada(registro):
    """A: o valor externo que apareceu na janela nao e tocado. Isto e a 13A
    funcionando, e nao ha nada de errado com esta metade."""
    registro.dados["HKCU"][CAMINHO] = {"1": externo()}

    cert_windows.definir_autoselect(CN_NOSSO)

    assert registro.valores("HKCU", CAMINHO) == {"1": externo()}


def test_1b_hklm_NAO_recebe_mais_a_nossa_policy(registro):
    """CROSS_HIVE_PARTIAL_WRITE_RISK, fechado.

    ANTES: a outra metade era escrita assim mesmo, e a tentativa terminava com
    HKCU apontando para um certificado e HKLM para outro — divergencia que NOS
    criamos, e que a instalacao ainda declarava bem-sucedida.

    AGORA a conferencia e um passo anterior e vale para as duas colmeias: uma
    so em conflito ja impede a instalacao inteira.
    """
    registro.dados["HKCU"][CAMINHO] = {"1": externo()}

    assert cert_windows.definir_autoselect(CN_NOSSO) == cert_windows.NAO_INSTALADA

    assert registro.valores("HKLM", CAMINHO) == {}, "HKLM ficou intacta"
    assert registro.valores("HKCU", CAMINHO) == {"1": externo()}


def test_1_o_conflito_e_avaliado_em_TODAS_antes_de_escrever(registro):
    """ANTES: a decisao acontecia dentro do laco de escrita, e um `continue`
    pulava so a colmeia da vez — a outra era escrita assim mesmo.

    AGORA a conferencia e um passo separado e anterior: uma colmeia em conflito
    ja impede a instalacao inteira.
    """
    fonte = inspect.getsource(cert_windows.definir_autoselect)
    corpo = fonte[fonte.rindex(chr(34) * 3) + 3:]

    assert "_conflita_na_colmeia(raiz, esperados)" in corpo
    assert corpo.index("if conflitantes:") < corpo.index("CreateKeyEx")
    assert "return NAO_INSTALADA" in corpo


# ── §2 · o inverso: conflito em HKLM ──────────────────────────────────────────

def test_2_conflito_em_hklm_tambem_impede_a_instalacao_inteira(registro):
    """Mesmo finding, na outra ordem — e o mesmo desfecho.

    ANTES: HKCU recebia a nossa policy e HKLM ficava com a alheia.
    """
    registro.dados["HKLM"][CAMINHO] = {"1": externo()}

    assert cert_windows.definir_autoselect(CN_NOSSO) == cert_windows.NAO_INSTALADA

    assert registro.valores("HKCU", CAMINHO) == {}, "HKCU ficou intacta"
    assert cert_windows._ler_cn("HKLM") == CN_ALHEIO


def test_2_agora_ha_postcondition_depois_da_escrita(registro):
    """ANTES ninguem relia o estado final para conferir se ficou coerente.

    AGORA o passo 3 rele as duas colmeias e exige que o resultado seja
    exatamente a nossa policy — a mesma semantica forte da 12D.
    """
    fonte = inspect.getsource(cert_windows.definir_autoselect)
    corpo = fonte[fonte.rindex(chr(34) * 3) + 3:]

    assert "_coerente(cn)" in corpo
    assert corpo.index("CreateKeyEx") < corpo.index("_coerente(cn)")
    verificacao = inspect.getsource(cert_windows._coerente)
    assert "inventario_da_policy()" in verificacao
    assert "policy_certificado.EMPRESTAR" in verificacao


# ── §3 · nao e o PARTIAL_POLICY_STATE historico ──────────────────────────────

def test_3_o_estado_divergente_aqui_foi_criado_por_nos(registro):
    """O historico era estado ENCONTRADO no startup, e a 12D o recusa. Este e
    estado que a nossa propria instalacao produziu depois de ter decidido que o
    host estava limpo — a 12D nao tem como recusa-lo, porque ele ainda nao
    existia quando ela olhou."""
    decisao_inicial = policy_certificado.avaliar_estado_inicial(
        cert_windows.inventario_da_policy(), CN_NOSSO, tuple(cert_windows.CERT_URLS)
    )
    assert decisao_inicial.decisao == policy_certificado.CRIAR, "host limpo"

    registro.dados["HKCU"][CAMINHO] = {"1": externo()}
    cert_windows.definir_autoselect(CN_NOSSO)

    depois = policy_certificado.avaliar_estado_inicial(
        cert_windows.inventario_da_policy(), CN_NOSSO, tuple(cert_windows.CERT_URLS)
    )
    # ANTES este estado era COLMEIAS_DIVERGENTES, e a divergencia era nossa.
    # AGORA o host continua com o estado alheio e mais nada: recusamos por causa
    # DELE, e nao por causa de uma metade que tivessemos instalado.
    assert depois.decisao == policy_certificado.RECUSAR
    # Uma regra so, e nao as sete: para a leitura forte isso e configuracao que
    # ela nao sabe ler inteira, e nao "outro certificado".
    assert depois.motivo == policy_certificado.CONTEUDO_NAO_RECONHECIDO
    assert cert_windows.policy_owned_existe(CN_NOSSO) is False, "nada nosso ficou"


# ── §4 · como ATIVADA e confirmada hoje ──────────────────────────────────────

def test_4_a_confirmacao_usa_a_LEITURA_FORTE(registro):
    """ANTES: `garantir_policy` comparava `ler_cn_atual()` com o CN pedido, e
    quem entrava ali era `policy_cn` — primeira colmeia nao vazia, valor "1".

    AGORA a mesma `avaliar_inicio` que decide o comeco confirma o fim, e ela le
    as duas colmeias inteiras. `ler_cn_atual` saiu do protocolo.
    """
    protocolo = inspect.getsource(policy_certificado.garantir_policy)
    # So o CORPO: a docstring conta a historia e cita o nome antigo.
    corpo = protocolo[protocolo.rindex(chr(34) * 3) + 3:]

    assert "ler_cn_atual" not in corpo
    assert "atual = avaliar_inicio()" in corpo
    assert "if atual.decisao == EMPRESTAR:" in corpo


def test_4_policy_cn_le_uma_colmeia_so_e_um_valor_so(registro):
    """`policy_cn` nao mudou, e continua fraco — o que mudou e que ele deixou de
    ser quem confirma. Ele sobrou para diagnostico, onde "qual CN o Chrome vai
    aplicar" e exatamente a pergunta certa."""
    fonte = inspect.getsource(cert_windows.policy_cn)

    assert "_ler_cn(raiz)" in fonte
    assert "inventario_da_policy" not in fonte
    assert "avaliar_estado_inicial" not in fonte

    leitura = inspect.getsource(cert_windows._ler_cn)
    assert 'QueryValueEx(key, "1")' in leitura


def test_4_basta_encontrar_o_CN_em_algum_lugar(registro):
    """A fraqueza continua existindo em `policy_cn`: com HKCU ilegivel para
    `_ler_cn`, a resposta vem de HKLM e ninguem pergunta o que ha em HKCU. O que
    a 13A.1 fez foi tirar essa leitura do caminho da confirmacao."""
    registro.dados["HKCU"][CAMINHO] = {"5": externo()}
    nossa(registro, "HKLM")

    assert cert_windows._ler_cn("HKCU") == "", "invisivel para a leitura fraca"
    assert cert_windows.policy_cn() == CN_NOSSO, "e a resposta e 'a policy e nossa'"


# ── §5 · POLICY_ACTIVATION_FALSE_POSITIVE ────────────────────────────────────

def _garantir(cn, registro, lancamentos, intruso=None):
    """`garantir_policy` com a leitura real e um guardiao falso que escreve.

    `intruso` e o terceiro entrando na JANELA: o host esta limpo quando a
    decisao e tomada, e o estado alheio so aparece depois — que e a unica forma
    de reproduzir o defeito. Semear antes faria a 12D recusar no startup, e o
    caminho de confirmacao nunca seria exercitado.
    """
    def lancar(pedido):
        lancamentos.append(pedido)
        if intruso is not None:
            registro.dados["HKCU"][CAMINHO] = dict(intruso)
        cert_windows.definir_autoselect(pedido)
        return object()

    return policy_certificado.garantir_policy(
        cn,
        avaliar_inicio=lambda: policy_certificado.avaliar_estado_inicial(
            cert_windows.inventario_da_policy(), cn, tuple(cert_windows.CERT_URLS)
        ),
        lancar_guardiao=lancar,
        aguardar=lambda: None,
        estado_do_guardiao=_vivo,
    )


def test_5_estado_divergente_NAO_e_mais_confirmado_como_ATIVADA(registro):
    """POLICY_ACTIVATION_FALSE_POSITIVE, fechado.

    ANTES: HKCU com estado alheio num nome que a leitura fraca nao alcanca, HKLM
    com a nossa policy; `policy_cn()` descia para HKLM, encontrava o nosso CN, e
    o protocolo declarava ATIVADA. O login recebia "pode confiar", e o Chrome
    podia estar lendo HKCU.

    AGORA a confirmacao usa a leitura forte, ve o estado alheio, e recusa.
    """
    lancamentos = []

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        _garantir(CN_NOSSO, registro, lancamentos, intruso={"5": externo()})

    assert lancamentos == [CN_NOSSO], "o guardiao chegou a subir"


def test_5_a_leitura_FORTE_ja_veria_o_problema(registro):
    """A informacao existe desde a 12D. Ela so nao e consultada na confirmacao."""
    registro.dados["HKCU"][CAMINHO] = {"5": externo()}
    nossa(registro, "HKLM")

    decisao = policy_certificado.avaliar_estado_inicial(
        cert_windows.inventario_da_policy(), CN_NOSSO, tuple(cert_windows.CERT_URLS)
    )

    assert decisao.decisao == policy_certificado.RECUSAR


def test_5_com_CN_alheio_legivel_o_desfecho_ja_e_seguro(registro):
    """O contraste que mostra que o defeito e da LEITURA, e nao da decisao: se o
    estado alheio estiver no valor "1" e for interpretavel, `policy_cn` devolve
    o CN errado, o polling estoura e a reavaliacao recusa."""
    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        _garantir(CN_NOSSO, registro, [], intruso={"1": externo()})


# ── §8 · §9 · nao ha all-or-refuse nem compensacao ───────────────────────────

def test_8_nao_existe_compensacao_de_escrita_parcial(registro):
    """Depois de escrever numa colmeia, nada desfaz isso se a outra impedir."""
    fonte = inspect.getsource(cert_windows.definir_autoselect)

    assert "remover_autoselect_owned" not in fonte
    assert "compensar" not in fonte.lower()


def test_9_a_tentativa_recusada_nao_deixa_estado_nosso(registro):
    """ANTES a tentativa parcial deixava a nossa metade instalada, sem que
    ninguem tivesse decidido que devia ficar."""
    registro.dados["HKCU"][CAMINHO] = {"1": externo()}

    cert_windows.definir_autoselect(CN_NOSSO)

    assert cert_windows.policy_owned_existe(CN_NOSSO) is False


def test_9_compensacao_quando_o_conflito_surge_DURANTE_a_escrita(registro,
                                                                 monkeypatch):
    """O caso que a conferencia previa nao alcanca: no passo 1 as duas colmeias
    estao limpas, e o terceiro escreve enquanto escrevemos.

    O passo 3 percebe, e a compensacao remove o que ESTA tentativa instalou.
    """
    original = cert_windows._valor_atual
    intrometido = {"feito": False}

    def durante(key, nome):
        # Depois que a primeira colmeia recebe o primeiro valor, o terceiro
        # escreve na outra.
        if not intrometido["feito"] and key.colmeia == "HKLM":
            intrometido["feito"] = True
            registro.dados["HKCU"][CAMINHO]["99"] = externo()
        return original(key, nome)

    monkeypatch.setattr(cert_windows, "_valor_atual", durante)

    resultado = cert_windows.definir_autoselect(CN_NOSSO)

    assert resultado == cert_windows.NAO_INSTALADA
    assert cert_windows.policy_owned_existe(CN_NOSSO) is False, "compensou"
    assert registro.valores("HKCU", CAMINHO) == {"99": externo()}, "o alheio ficou"


def test_9_a_compensacao_que_nao_confirma_devolve_RESIDUO_OWNED(registro,
                                                                monkeypatch):
    """§10: a tentativa nao "falhou e pronto". Se sobrou estado nosso, o
    guardiao continua sendo o dono dele, e o host continua fail-closed."""
    monkeypatch.setattr(cert_windows, "_coerente", lambda cn: False)
    monkeypatch.setattr(cert_windows, "_remover_owned_da_colmeia",
                        lambda raiz, esperados: False)

    resultado = cert_windows.definir_autoselect(CN_NOSSO)

    assert resultado == cert_windows.RESIDUO_OWNED
    assert cert_windows.policy_owned_existe(CN_NOSSO) is True


# ── §19 · o caminho normal, que nao pode regredir ────────────────────────────

def test_19_host_vazio_instala_nas_duas_colmeias(registro):
    assert cert_windows.definir_autoselect(CN_NOSSO) == cert_windows.INSTALADA

    for colmeia in ("HKCU", "HKLM"):
        assert len(registro.valores(colmeia, CAMINHO)) == QUANTAS


def test_19_uma_colmeia_indisponivel_por_permissao_continua_aceita(monkeypatch):
    """Contrato ANTERIOR, e ele nao e divergencia: HKLM sem elevacao nunca
    chega a existir, e o estado final tem uma colmeia so — coerente."""
    falso = RegistroFalso(protegidas=("HKLM",))
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))

    assert cert_windows.definir_autoselect(CN_NOSSO) == cert_windows.INSTALADA

    decisao = policy_certificado.avaliar_estado_inicial(
        cert_windows.inventario_da_policy(), CN_NOSSO, tuple(cert_windows.CERT_URLS)
    )
    assert decisao.decisao == policy_certificado.EMPRESTAR, "coerente, e nao parcial"


# ── §12 · nenhum login comeca sobre estado divergente ────────────────────────

class _PlanilhaInerte:
    def salvar(self, *a, **k):
        pass

    def descartar(self):
        pass

    def mapa_status(self, *a, **k):
        return {}


def _execucao(monkeypatch, aberturas):
    from automation import app
    from automation.captcha import ConfigCaptcha

    monkeypatch.setattr(app.maquina, "abrir_sessao",
                        lambda *a, **k: aberturas.append(a))
    ex = app._Execucao(_PlanilhaInerte(), "p.xlsx", ConfigCaptcha(api_key="x"), None)
    ex.certificados = {CN_NOSSO: {"subject_cn": CN_NOSSO, "serial": "0A01"}}
    return ex


def _item(cn):
    from automation.planilha import ItemPendente

    return ItemPendente(posicao=0, cnpj="11111111000191", certificado=cn)


def test_12_nenhum_login_comeca_quando_a_escrita_encontra_conflito(
    registro, monkeypatch
):
    """§12: nao basta emitir evento. A operacao para ANTES da autenticacao."""
    monkeypatch.setattr("time.sleep", lambda _s: None)

    def lancar(cn):
        registro.dados["HKCU"][CAMINHO] = {"1": externo()}
        cert_windows.definir_autoselect(cn)
        return type("C", (), {"cn": cn})()

    monkeypatch.setattr(cert_windows, "_lancar_guardiao", lancar)
    aberturas = []
    ex = _execucao(monkeypatch, aberturas)

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        ex.trocar_certificado(_item(CN_NOSSO))

    assert aberturas == [], "nenhuma sessao foi aberta"
    assert ex.sessao is None


def test_12_e_a_recusa_sobe_sem_virar_evento(registro):
    """`trocar_certificado` nao tem `except`: a condicao e do host, e nao um
    item que se pule."""
    import inspect

    from automation import app

    assert "except" not in inspect.getsource(app._Execucao.trocar_certificado)


# ── §19 · o caminho normal com dois certificados ─────────────────────────────

def test_19_multicert_em_host_vazio_continua_funcionando(registro, monkeypatch):
    """CN_A instala completo, sai completo, CN_B instala completo. A 13A.1 nao
    pode ter transformado o caminho normal em recusa."""
    assert cert_windows.definir_autoselect(CN_NOSSO) == cert_windows.INSTALADA
    assert cert_windows._coerente(CN_NOSSO) is True

    cert_windows.remover_autoselect_owned(CN_NOSSO)
    assert cert_windows.policy_owned_existe(CN_NOSSO) is False

    assert cert_windows.definir_autoselect(CN_ALHEIO) == cert_windows.INSTALADA
    assert cert_windows._coerente(CN_ALHEIO) is True


def test_19_a_instalacao_completa_cobre_as_duas_colmeias(registro):
    cert_windows.definir_autoselect(CN_NOSSO)

    for colmeia in ("HKCU", "HKLM"):
        assert len(registro.valores(colmeia, CAMINHO)) == QUANTAS


# ── §17 · content match nao e proveniencia ───────────────────────────────────

def test_17_reasercao_externa_identica_e_indistinguivel(registro):
    """IDENTICAL_EXTERNAL_POLICY_REASSERTION_AMBIGUITY, registrado e nao
    corrigido.

    Um payload identico ao nosso e nosso para todos os efeitos da comparacao —
    e nao ha como saber se fomos nos que o escrevemos. Distinguir exigiria
    marcador de dono, que a 12D avaliou e recusou.
    """
    registro.dados["HKCU"][CAMINHO] = dict(
        cert_windows._valores_esperados(CN_NOSSO)
    )

    assert cert_windows.policy_owned_existe(CN_NOSSO) is True, "e nao escrevemos"

    fonte = inspect.getsource(cert_windows.policy_owned_existe)
    assert "IDENTICAL_EXTERNAL_POLICY_REASSERTION_AMBIGUITY" in fonte
