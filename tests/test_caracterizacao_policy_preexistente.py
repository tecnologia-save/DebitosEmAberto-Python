"""O que a execucao faz com uma policy que ja estava no host — antes e depois.

A versao ANTERIOR deste arquivo, com o comportamento de antes da fatia 12D,
esta no commit que o introduziu. Cada teste que mudou de resultado diz aqui o
que afirmava antes.

Nenhum teste toca o registro real, pede UAC, lanca processo elevado ou abre
Chrome: `winreg` inteiro e substituido por `RegistroFalso`, e o guardiao entra
por parametro. Todos os CNs sao ficticios.

A pergunta que esta fatia responde
----------------------------------
"Uma execucao acabou de adquirir exclusividade do host. O que ela pode fazer com
uma policy que JA estava la antes dela?"

O lease da 12C prova que nenhuma OUTRA instancia protegida de DebitosEmAberto
esta concorrendo. Ele NAO prova quem escreveu a policy que ja estava no
registro: ela pode ter sido deixada por um administrador, por outra ferramenta,
por uma execucao anterior que morreu junto com o guardiao, ou por um reboot
antes da limpeza. O registro e persistente; o lease e efemero.

A resposta desta fatia e BORROW / CREATE / REFUSE.
"""
import json

import pytest
from registro_falso import RegistroFalso

import cert_windows
from automation import policy_certificado

CN_NOSSO = "ALFA FICTICIA LTDA:11111111000191"
CN_ALHEIO = "BETA FICTICIA SA:22222222000172"
CAMINHO = cert_windows.REG_PATH


@pytest.fixture
def registro(monkeypatch):
    falso = RegistroFalso()
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))
    return falso


def entrada(cn, url="https://[*.]gov.br"):
    """Um valor no formato exato que `definir_autoselect` escreve."""
    return json.dumps({"pattern": url, "filter": {"SUBJECT": {"CN": cn}}})


def semear(registro, colmeia, cn):
    """Uma policy completa e coerente, como a nossa propria escrita deixaria."""
    registro.dados[colmeia][CAMINHO] = {
        str(i): entrada(cn, url) for i, url in enumerate(cert_windows.CERT_URLS, 1)
    }


def protocolo(cn, lancamentos):
    """`garantir_policy` com a leitura real e um guardiao falso que ESCREVE.

    E por isso que os testes abaixo veem o efeito no registro, e nao apenas a
    decisao.
    """
    def lancar(pedido):
        lancamentos.append(pedido)
        cert_windows.definir_autoselect(pedido)
        return object()

    return policy_certificado.garantir_policy(
        cn,
        avaliar_inicio=lambda: policy_certificado.avaliar_estado_inicial(
            cert_windows.inventario_da_policy(), cn, tuple(cert_windows.CERT_URLS)
        ),
        lancar_guardiao=lancar,
        aguardar=lambda: None,
    )


# ── A · nenhuma policy: CRIAR ─────────────────────────────────────────────────

def test_a_host_limpo_nao_tem_cn_nenhum(registro):
    assert cert_windows.policy_cn() == ""
    assert cert_windows.policy_existe() is False


def test_a_host_limpo_lanca_guardiao_e_a_policy_passa_a_ser_nossa(registro):
    lancamentos = []

    resultado = protocolo(CN_NOSSO, lancamentos)

    assert lancamentos == [CN_NOSSO]
    assert resultado.situacao == policy_certificado.ATIVADA
    assert resultado.tem_guardiao is True, "e o guardiao a remove no fim"


def test_a_chave_existente_e_vazia_conta_como_host_limpo(registro):
    """Sem regras, o Chrome nao seleciona nada — e a nossa propria escrita cria
    a chave antes de preenche-la. Recusar aqui seria recusar o proprio rastro."""
    registro.dados["HKCU"][CAMINHO] = {}
    lancamentos = []

    assert protocolo(CN_NOSSO, lancamentos).situacao == policy_certificado.ATIVADA
    assert lancamentos == [CN_NOSSO]


# ── B · C · D · o mesmo CN ja escrito: EMPRESTAR ──────────────────────────────

def test_b_mesmo_cn_em_hkcu_e_EMPRESTADO_sem_lancar_guardiao(registro):
    semear(registro, "HKCU", CN_NOSSO)
    lancamentos = []

    resultado = protocolo(CN_NOSSO, lancamentos)

    assert lancamentos == [], "ninguem e lancado"
    assert resultado.situacao == policy_certificado.JA_ATIVA
    assert resultado.tem_guardiao is False, "e ninguem vai limpar"


def test_b_emprestar_nao_modifica_nada(registro):
    """§14: se e emprestada, nao se escreve, nao se lanca dono e nao se limpa."""
    semear(registro, "HKCU", CN_NOSSO)
    antes = registro.valores("HKCU", CAMINHO)

    protocolo(CN_NOSSO, [])

    assert registro.valores("HKCU", CAMINHO) == antes
    assert ("SetValueEx", "HKCU", "1") not in registro.operacoes
    assert not [op for op in registro.operacoes if op[0] == "DeleteKey"]


def test_c_mesmo_cn_so_em_hklm_tambem_e_emprestado(registro):
    """HKCU ausente. A unica colmeia com conteudo esta completa e coerente."""
    semear(registro, "HKLM", CN_NOSSO)

    assert protocolo(CN_NOSSO, []).situacao == policy_certificado.JA_ATIVA


def test_d_mesmo_cn_coerente_nas_duas_colmeias(registro):
    semear(registro, "HKCU", CN_NOSSO)
    semear(registro, "HKLM", CN_NOSSO)

    assert protocolo(CN_NOSSO, []).situacao == policy_certificado.JA_ATIVA


def test_b_emprestar_nao_e_saber_quem_escreveu(registro):
    """O ponto epistemico da fatia, e ele NAO foi resolvido — foi contornado.

    O estado semeado aqui e indistinguivel do que a nossa propria automacao
    escreveria. Ninguem neste processo sabe quem o escreveu: nao ha marcador de
    dono no payload. O que a fatia garante e que essa ignorancia nao autoriza
    destruir nada — emprestar nao modifica e nao limpa.
    """
    semear(registro, "HKCU", CN_NOSSO)

    for bruto in registro.valores("HKCU", CAMINHO).values():
        assert "DebitosEmAberto" not in bruto
        assert set(json.loads(bruto)) == {"pattern", "filter"}, "nao ha campo de dono"

    assert protocolo(CN_NOSSO, []).sera_limpa is False, "continua sendo de quem a fez"


# ── E · CN diferente: RECUSAR ─────────────────────────────────────────────────

def test_e_cn_diferente_RECUSA_em_vez_de_sobrescrever(registro):
    """ANTES: lancava o guardiao, sobrescrevia, e a execucao seguia com o CN
    novo. O CN alheio desaparecia sem que nada dele fosse guardado.

    AGORA: para antes de escrever. Uma policy apontando para outro certificado
    faria o Chrome autenticar como outra empresa — seguir seria pior que parar.
    """
    semear(registro, "HKCU", CN_ALHEIO)
    lancamentos = []

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel) as erro:
        protocolo(CN_NOSSO, lancamentos)

    assert erro.value.motivo == policy_certificado.OUTRO_CERTIFICADO
    assert lancamentos == []


def test_e_o_estado_alheio_fica_exatamente_como_estava(registro):
    """ANTES este teste se chamava `nada_e_guardado_do_estado_anterior` e
    afirmava que o CN alheio sumia dos valores. Nao ha mais o que guardar,
    porque nao ha mais o que destruir."""
    semear(registro, "HKCU", CN_ALHEIO)
    antes = registro.valores("HKCU", CAMINHO)

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        protocolo(CN_NOSSO, [])

    assert registro.valores("HKCU", CAMINHO) == antes


def test_e_e_nenhuma_limpeza_nossa_e_disparada(registro):
    """ANTES o ciclo era alheio -> nosso -> chave apagada, e o alheio nunca
    voltava. Recusar antes de escrever tambem significa nao ter o que limpar."""
    semear(registro, "HKCU", CN_ALHEIO)

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        protocolo(CN_NOSSO, [])

    assert cert_windows.policy_cn() == CN_ALHEIO
    assert not [op for op in registro.operacoes if op[0] == "DeleteKey"]


# ── F · colmeias divergentes: RECUSAR ─────────────────────────────────────────

def test_f_hkcu_esperado_e_hklm_diferente_agora_RECUSA(registro):
    """ANTES: a colmeia lida primeiro tinha o CN certo, entao `policy_cn`
    devolvia o CN certo e o protocolo declarava JA_ATIVA. HKLM nunca era
    consultado — e o Chrome pode justamente ler HKLM.

    AGORA as duas sao lidas, e a divergencia por si so basta para recusar. Isto
    e o PARTIAL_POLICY_STATE deixando de ser um risco silencioso.
    """
    semear(registro, "HKCU", CN_NOSSO)
    semear(registro, "HKLM", CN_ALHEIO)
    lancamentos = []

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel) as erro:
        protocolo(CN_NOSSO, lancamentos)

    assert erro.value.motivo == policy_certificado.COLMEIAS_DIVERGENTES
    assert lancamentos == []


def test_f_hkcu_diferente_e_hklm_esperado_tambem_recusa(registro):
    """ANTES a ordem inversa decidia o oposto — lancava guardiao e sobrescrevia
    — porque so a primeira colmeia nao vazia era consultada. Hoje as duas
    ordens dao o mesmo resultado, que e o que "divergente" deveria significar."""
    semear(registro, "HKCU", CN_ALHEIO)
    semear(registro, "HKLM", CN_NOSSO)

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel) as erro:
        protocolo(CN_NOSSO, [])

    assert erro.value.motivo == policy_certificado.COLMEIAS_DIVERGENTES


def test_f_recusar_dispensa_saber_qual_colmeia_o_chrome_prefere(registro):
    """A razao de a precedencia HKCU/HKLM nao ser necessaria para a seguranca:
    quando as duas discordam, nao se escolhe entre elas — para-se.

    A prova e a INDEPENDENCIA DE ORDEM. Inverter as colmeias nao muda a decisao,
    e uma decisao que nao depende da ordem tambem nao depende de qual delas o
    Chrome le primeiro.
    """
    avaliar = policy_certificado.avaliar_estado_inicial
    padroes = tuple(cert_windows.CERT_URLS)
    semear(registro, "HKCU", CN_ALHEIO)
    semear(registro, "HKLM", CN_NOSSO)
    colmeias = cert_windows.inventario_da_policy()

    direta = avaliar(colmeias, CN_NOSSO, padroes)
    invertida = avaliar(tuple(reversed(colmeias)), CN_NOSSO, padroes)

    assert direta == invertida
    assert direta.decisao == policy_certificado.RECUSAR


# ── G · uma colmeia ausente ───────────────────────────────────────────────────

def test_g_uma_colmeia_ausente_com_a_outra_coerente_e_emprestavel(registro):
    """Ausencia nao e divergencia. Escrever nas duas e o desejado; ter escrito
    so numa e o comum (HKLM sem elevacao), e nao ha nada de conflitante nisso."""
    semear(registro, "HKCU", CN_NOSSO)

    assert not registro.tem("HKLM", CAMINHO)
    assert protocolo(CN_NOSSO, []).situacao == policy_certificado.JA_ATIVA


def test_g_a_escrita_parcial_real_produz_exatamente_esse_estado(monkeypatch):
    """E o caso comum, nao um caso de laboratorio: sem elevacao, HKLM recusa."""
    falso = RegistroFalso(protegidas=("HKLM",))
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))

    cert_windows.definir_autoselect(CN_NOSSO)

    assert falso.tem("HKCU", CAMINHO)
    assert not falso.tem("HKLM", CAMINHO)


# ── H · payload malformado: RECUSAR ───────────────────────────────────────────

def test_h_payload_malformado_continua_invisivel_para_a_leitura_por_CN(registro):
    """`_ler_cn` nao mudou, e continua tratando o ilegivel como ausente — ele
    responde "qual CN o Chrome vai aplicar", e a essa pergunta "nenhum" e a
    resposta certa.

    O que mudou e quem depende dele: a DECISAO de startup (12D) e a CONFIRMACAO
    de limpeza (12D.1) sairam as duas de cima dessa leitura.
    """
    registro.dados["HKCU"][CAMINHO] = {"1": "isto nao e json"}

    assert cert_windows._ler_cn("HKCU") == ""
    assert cert_windows.policy_cn() == ""
    assert cert_windows.policy_existe() is True, "mas o estado esta la"


def test_h_malformado_agora_RECUSA_em_vez_de_sobrescrever(registro):
    """ANTES a ausencia aparente virava licenca para escrever: o protocolo achava
    o host limpo e o guardiao escrevia por cima de uma configuracao que ninguem
    tinha entendido.

    AGORA o inventario ve que ha conteudo e que nao sabemos le-lo, e isso e
    motivo de parada — nao de sobrescrita.
    """
    registro.dados["HKCU"][CAMINHO] = {"1": "isto nao e json"}
    lancamentos = []

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel) as erro:
        protocolo(CN_NOSSO, lancamentos)

    assert erro.value.motivo == policy_certificado.CONTEUDO_NAO_RECONHECIDO
    assert lancamentos == []


def test_h_o_cn_fora_do_valor_um_deixou_de_ser_invisivel(registro):
    """ANTES: `_ler_cn` lia SO o valor "1", entao uma policy legitima escrita por
    outra ferramenta comecando em outro indice simplesmente nao existia para
    nos — e era sobrescrita.

    AGORA o inventario enumera a colmeia inteira, e o que ele ve basta para
    recusar."""
    registro.dados["HKCU"][CAMINHO] = {"2": entrada(CN_ALHEIO)}

    assert cert_windows._ler_cn("HKCU") == "", "a leitura antiga continua cega"

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        protocolo(CN_NOSSO, [])


# ── I · valores extras: RECUSAR ───────────────────────────────────────────────

def test_i_regra_a_mais_agora_RECUSA(registro):
    """ANTES: a decisao lia um valor so, entao valores adicionais nao entravam
    nela — a execucao seguia e a nossa escrita preservava o extra por acidente
    (a limpeza para no primeiro indice ausente).

    AGORA sao motivo de recusa. §15: nao sabemos se uma regra adicional afeta as
    nossas URLs, e sem saber a resposta segura e parar, nunca apagar.
    """
    semear(registro, "HKCU", CN_NOSSO)
    registro.dados["HKCU"][CAMINHO]["99"] = entrada(CN_ALHEIO, "https://intranet")

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel) as erro:
        protocolo(CN_NOSSO, [])

    assert erro.value.motivo == policy_certificado.REGRAS_ADICIONAIS


def test_i_nome_nao_numerico_tambem_recusa(registro):
    semear(registro, "HKCU", CN_NOSSO)
    registro.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = entrada(CN_ALHEIO)

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        protocolo(CN_NOSSO, [])


def test_i_e_o_extra_nao_e_apagado(registro):
    """§15, literal: nao apagar valores extras. Recusar nao remove nada."""
    semear(registro, "HKCU", CN_NOSSO)
    registro.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = entrada(CN_ALHEIO)

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        protocolo(CN_NOSSO, [])

    assert "RegraDaEmpresa" in registro.valores("HKCU", CAMINHO)


def test_i_a_limpeza_continua_levando_tudo_e_por_isso_nao_a_alcancamos(registro):
    """`limpar_autoselect` sempre apagou a chave INTEIRA, com o que era nosso e o
    que nao era. Isso nao mudou — o que mudou e que ela so e alcancada por uma
    policy que esta execucao instalou."""
    semear(registro, "HKCU", CN_NOSSO)
    registro.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = entrada(CN_ALHEIO)

    cert_windows.limpar_autoselect()

    assert not registro.tem("HKCU", CAMINHO), "quando chamada, ainda leva tudo"


# ── J · posse dentro da execucao ──────────────────────────────────────────────

def test_j_nem_a_nossa_propria_policy_e_sobrescrita(registro):
    """ANTES (12D): existia `policy_ja_e_nossa`. Dentro de uma execucao a posse
    era demonstravel — quem chamava detinha o controle do guardiao que escreveu
    a policy anterior — e isso autorizava a escrita a passar por cima dela.

    AGORA (13A) ninguem passa por cima de nada. A escrita e nao destrutiva, o
    que sai sai por comparacao no momento da remocao, e a porta desapareceu:
    quando a remocao anterior nao confirma, quem para e o chamador, antes de
    pedir policy nova.
    """
    import inspect

    parametros = list(
        inspect.signature(policy_certificado.garantir_policy).parameters
    )

    assert "policy_ja_e_nossa" not in parametros
    assert parametros == ["cn", "avaliar_inicio", "lancar_guardiao", "aguardar"]


def test_j_a_execucao_seguinte_a_um_crash_duplo_para_em_vez_de_herdar(registro):
    """ANTES: processo e guardiao morriam, o lease efemero sumia com eles, a
    policy persistente ficava, e a execucao seguinte adquiria o lease e escrevia
    por cima — sem forma nenhuma de saber se aquele estado era nosso.

    AGORA ela para. HOST_LEASE_RECOVERY_GAP nao e fechado tornando o lease
    persistente; e fechado tratando com seguranca o estado que sobrevive a ele.
    """
    semear(registro, "HKCU", CN_ALHEIO)

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        protocolo(CN_NOSSO, [])


def test_j_um_crash_duplo_com_o_MESMO_certificado_nao_trava_o_host(registro):
    """O outro lado da moeda, e o que impede a regra de ser inutilizavel: se o
    que sobrou aponta para o certificado desta execucao, ela empresta e segue."""
    semear(registro, "HKCU", CN_NOSSO)

    assert protocolo(CN_NOSSO, []).situacao == policy_certificado.JA_ATIVA
