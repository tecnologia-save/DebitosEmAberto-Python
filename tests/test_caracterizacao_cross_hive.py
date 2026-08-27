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


def test_1b_hklm_recebe_a_nossa_policy(registro):
    """B: e a outra metade e escrita assim mesmo.

    CROSS_HIVE_PARTIAL_WRITE_RISK: a tentativa termina com HKCU apontando para
    um certificado e HKLM para outro — divergencia que NOS criamos.
    """
    registro.dados["HKCU"][CAMINHO] = {"1": externo()}

    assert cert_windows.definir_autoselect(CN_NOSSO) is True, "diz que instalou"

    assert len(registro.valores("HKLM", CAMINHO)) == QUANTAS
    assert cert_windows._ler_cn("HKCU") == CN_ALHEIO
    assert cert_windows._ler_cn("HKLM") == CN_NOSSO


def test_1_o_conflito_e_avaliado_colmeia_a_colmeia(registro):
    """A raiz: `definir_autoselect` decide dentro do laco das colmeias, e um
    `continue` pula so a colmeia da vez."""
    fonte = inspect.getsource(cert_windows.definir_autoselect)
    corpo = fonte[fonte.rindex('"""') + 3:]

    assert "for rotulo, raiz in _COLMEIAS:" in corpo
    assert "if _conflita(key, esperados):" in corpo
    assert "continue" in corpo, "pula a colmeia, e nao a instalacao"


# ── §2 · o inverso: conflito em HKLM ──────────────────────────────────────────

def test_2_conflito_em_hklm_deixa_hkcu_com_o_nosso_estado(registro):
    """Mesmo finding, na outra ordem."""
    registro.dados["HKLM"][CAMINHO] = {"1": externo()}

    assert cert_windows.definir_autoselect(CN_NOSSO) is True

    assert len(registro.valores("HKCU", CAMINHO)) == QUANTAS
    assert cert_windows._ler_cn("HKLM") == CN_ALHEIO


def test_2_nao_ha_postcondition_nenhuma_depois_da_escrita(registro):
    """Ninguem rele o estado final para conferir se ele ficou coerente."""
    fonte = inspect.getsource(cert_windows.definir_autoselect)
    corpo = fonte[fonte.rindex('"""') + 3:]

    assert "inventario_da_policy" not in corpo
    assert "avaliar_estado_inicial" not in corpo


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
    assert depois.decisao == policy_certificado.RECUSAR
    assert depois.motivo == policy_certificado.COLMEIAS_DIVERGENTES


# ── §4 · como ATIVADA e confirmada hoje ──────────────────────────────────────

def test_4_a_confirmacao_usa_policy_cn(registro):
    """`garantir_policy` compara `ler_cn_atual()` com o CN pedido, e quem entra
    ali e `policy_cn`."""
    protocolo = inspect.getsource(policy_certificado.garantir_policy)

    assert "if ler_cn_atual() == cn:" in protocolo
    assert "ler_cn_atual=policy_cn," in inspect.getsource(
        cert_windows.iniciar_guarda_detalhado
    )


def test_4_policy_cn_le_uma_colmeia_so_e_um_valor_so(registro):
    """Primeira colmeia nao vazia, valor "1", CN extraido. Nao usa
    `inventario_da_policy`, nao usa `avaliar_estado_inicial`, e nao exige
    coerencia nenhuma."""
    fonte = inspect.getsource(cert_windows.policy_cn)

    assert "_ler_cn(raiz)" in fonte
    assert "inventario_da_policy" not in fonte
    assert "avaliar_estado_inicial" not in fonte

    leitura = inspect.getsource(cert_windows._ler_cn)
    assert 'QueryValueEx(key, "1")' in leitura


def test_4_basta_encontrar_o_CN_em_algum_lugar(registro):
    """E a consequencia: com HKCU ilegivel para `_ler_cn`, a resposta vem de
    HKLM — e ninguem pergunta o que ha em HKCU."""
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
        ler_cn_atual=cert_windows.policy_cn,
        lancar_guardiao=lancar,
        aguardar=lambda: None,
    )


def test_5_estado_divergente_e_confirmado_como_ATIVADA(registro):
    """POLICY_ACTIVATION_FALSE_POSITIVE — IDENTITY_SAFETY_DEFECT.

    HKCU tem estado alheio num nome que a leitura fraca nao alcanca; HKLM tem a
    nossa policy. `policy_cn()` desce para HKLM, encontra o nosso CN, e o
    protocolo declara ATIVADA. O Chrome pode ler HKCU.
    """
    lancamentos = []

    resultado = _garantir(CN_NOSSO, registro, lancamentos,
                          intruso={"5": externo()})

    assert resultado.situacao == policy_certificado.ATIVADA
    assert resultado.confiavel is True, "e o login recebe 'pode confiar'"
    assert cert_windows._ler_cn("HKCU") == "", "com estado alheio em HKCU"


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


def test_9_a_tentativa_parcial_deixa_estado_nosso_instalado(registro):
    """E ele fica sem que ninguem tenha decidido que devia ficar."""
    registro.dados["HKCU"][CAMINHO] = {"1": externo()}

    cert_windows.definir_autoselect(CN_NOSSO)

    assert cert_windows.policy_owned_existe(CN_NOSSO) is True


# ── §19 · o caminho normal, que nao pode regredir ────────────────────────────

def test_19_host_vazio_instala_nas_duas_colmeias(registro):
    assert cert_windows.definir_autoselect(CN_NOSSO) is True

    for colmeia in ("HKCU", "HKLM"):
        assert len(registro.valores(colmeia, CAMINHO)) == QUANTAS


def test_19_uma_colmeia_indisponivel_por_permissao_continua_aceita(monkeypatch):
    """Contrato ANTERIOR, e ele nao e divergencia: HKLM sem elevacao nunca
    chega a existir, e o estado final tem uma colmeia so — coerente."""
    falso = RegistroFalso(protegidas=("HKLM",))
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))

    assert cert_windows.definir_autoselect(CN_NOSSO) is True

    decisao = policy_certificado.avaliar_estado_inicial(
        cert_windows.inventario_da_policy(), CN_NOSSO, tuple(cert_windows.CERT_URLS)
    )
    assert decisao.decisao == policy_certificado.EMPRESTAR, "coerente, e nao parcial"
