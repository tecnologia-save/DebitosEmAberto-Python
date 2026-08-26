"""Como o caminho ATUAL reage a uma policy que ja estava no host.

Escrito ANTES de qualquer mudanca da fatia 12D. Nenhum teste toca o registro
real, pede UAC, lanca processo elevado ou abre Chrome: `winreg` inteiro e
substituido por `RegistroFalso`, e o guardiao entra por parametro.

Todos os CNs sao ficticios.

A pergunta que esta fatia faz
-----------------------------
"Uma execucao acabou de adquirir exclusividade do host. O que ela pode fazer com
uma policy que JA estava la antes dela?"

O lease da 12C prova que nenhuma OUTRA instancia protegida de DebitosEmAberto
esta concorrendo. Ele NAO prova quem escreveu a policy que ja estava no
registro: ela pode ter sido deixada por um administrador, por outra ferramenta,
por uma execucao anterior que morreu junto com o guardiao, ou por um reboot
antes da limpeza. O registro e persistente; o lease e efemero.
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


def protocolo(cn, registro, lancamentos):
    """`garantir_policy` com a leitura real e um guardiao falso.

    O guardiao falso ESCREVE, como o de verdade escreveria — e por isso os
    testes abaixo veem o efeito destrutivo, e nao apenas a decisao.
    """
    def lancar(pedido):
        lancamentos.append(pedido)
        cert_windows.definir_autoselect(pedido)
        return object()

    return policy_certificado.garantir_policy(
        cn, ler_cn_atual=cert_windows.policy_cn,
        lancar_guardiao=lancar, aguardar=lambda: None,
    )


# ── A · nenhuma policy ────────────────────────────────────────────────────────

def test_a_host_limpo_nao_tem_cn_nenhum(registro):
    assert cert_windows.policy_cn() == ""
    assert cert_windows.policy_existe() is False


def test_a_host_limpo_lanca_guardiao_e_a_policy_passa_a_ser_nossa(registro):
    lancamentos = []

    resultado = protocolo(CN_NOSSO, registro, lancamentos)

    assert lancamentos == [CN_NOSSO]
    assert resultado.situacao == policy_certificado.ATIVADA
    assert resultado.tem_guardiao is True


# ── B · C · D · o mesmo CN ja escrito ─────────────────────────────────────────

def test_b_mesmo_cn_em_hkcu_e_aceito_sem_lancar_guardiao(registro):
    semear(registro, "HKCU", CN_NOSSO)
    lancamentos = []

    resultado = protocolo(CN_NOSSO, registro, lancamentos)

    assert lancamentos == [], "ninguem e lancado"
    assert resultado.situacao == policy_certificado.JA_ATIVA
    assert resultado.tem_guardiao is False, "e ninguem vai limpar"


def test_c_mesmo_cn_so_em_hklm_tambem_e_aceito(registro):
    """HKCU ausente. `policy_cn` desce para a segunda colmeia e devolve o CN."""
    semear(registro, "HKLM", CN_NOSSO)

    assert cert_windows.policy_cn() == CN_NOSSO
    assert protocolo(CN_NOSSO, registro, []).situacao == policy_certificado.JA_ATIVA


def test_d_mesmo_cn_coerente_nas_duas_colmeias(registro):
    semear(registro, "HKCU", CN_NOSSO)
    semear(registro, "HKLM", CN_NOSSO)

    assert protocolo(CN_NOSSO, registro, []).situacao == policy_certificado.JA_ATIVA


def test_b_aceitar_nao_e_saber_quem_escreveu(registro):
    """O ponto epistemico da fatia.

    O estado semeado aqui e indistinguivel do que a nossa propria automacao
    escreveria — mesmas URLs, mesmo formato, mesmo CN. E mesmo assim ninguem
    neste processo sabe quem o escreveu: nao ha marcador de dono no payload.
    """
    semear(registro, "HKCU", CN_NOSSO)

    for bruto in registro.valores("HKCU", CAMINHO).values():
        assert "DebitosEmAberto" not in bruto
        assert set(json.loads(bruto)) == {"pattern", "filter"}, "nao ha campo de dono"


# ── E · CN diferente: a substituicao destrutiva ───────────────────────────────

def test_e_cn_diferente_lanca_guardiao_e_sobrescreve(registro):
    semear(registro, "HKCU", CN_ALHEIO)
    lancamentos = []

    resultado = protocolo(CN_NOSSO, registro, lancamentos)

    assert lancamentos == [CN_NOSSO]
    assert resultado.situacao == policy_certificado.ATIVADA
    lido = json.loads(registro.valores("HKCU", CAMINHO)["1"])
    assert lido["filter"]["SUBJECT"]["CN"] == CN_NOSSO, "o CN alheio se foi"


def test_e_nada_e_guardado_do_estado_anterior(registro):
    """PREEXISTING_POLICY_DESTRUCTIVE_REPLACEMENT, o nucleo.

    Nenhum snapshot, nenhuma copia, nenhum registro do que havia antes: o CN
    alheio existe so enquanto nao for sobrescrito.
    """
    semear(registro, "HKCU", CN_ALHEIO)

    protocolo(CN_NOSSO, registro, [])

    for bruto in registro.valores("HKCU", CAMINHO).values():
        assert CN_ALHEIO not in bruto


def test_e_e_a_limpeza_apaga_a_chave_inteira_sem_restaurar(registro):
    """O ciclo completo: alheio -> nosso -> limpo. O alheio nunca volta."""
    semear(registro, "HKCU", CN_ALHEIO)
    protocolo(CN_NOSSO, registro, [])

    cert_windows.limpar_autoselect()

    assert not registro.tem("HKCU", CAMINHO), "a chave inteira saiu"
    assert cert_windows.policy_cn() == "", "e o CN alheio nao foi restaurado"


# ── F · colmeias divergentes ──────────────────────────────────────────────────

def test_f_hkcu_esperado_e_hklm_diferente_passa_como_ja_ativa(registro):
    """PARTIAL_POLICY_STATE em forma de decisao.

    A colmeia lida primeiro tem o CN certo, entao `policy_cn` devolve o CN certo
    e o protocolo declara JA_ATIVA. HKLM nunca e consultado — e o Chrome pode
    justamente ler HKLM.
    """
    semear(registro, "HKCU", CN_NOSSO)
    semear(registro, "HKLM", CN_ALHEIO)
    lancamentos = []

    resultado = protocolo(CN_NOSSO, registro, lancamentos)

    assert resultado.situacao == policy_certificado.JA_ATIVA
    assert lancamentos == []
    assert cert_windows._ler_cn("HKLM") == CN_ALHEIO, "e continua la, intocado"


def test_f_hkcu_diferente_e_hklm_esperado_lanca_guardiao(registro):
    """A ordem inversa da anterior decide o oposto — pelo mesmo motivo."""
    semear(registro, "HKCU", CN_ALHEIO)
    semear(registro, "HKLM", CN_NOSSO)
    lancamentos = []

    assert protocolo(CN_NOSSO, registro, lancamentos).situacao == (
        policy_certificado.ATIVADA
    )
    assert lancamentos == [CN_NOSSO]


def test_f_a_decisao_e_do_primeiro_nao_vazio_e_so_dele(registro):
    semear(registro, "HKCU", CN_NOSSO)
    semear(registro, "HKLM", CN_ALHEIO)

    assert cert_windows.policy_cn() == CN_NOSSO
    assert cert_windows.policy_existe() is True, "e nao diz de qual colmeia"


# ── G · uma colmeia ausente quando ambas eram esperadas ───────────────────────

def test_g_uma_colmeia_ausente_e_indistinguivel_de_coerente(registro):
    """Escrever nas duas e o desejado; ter escrito so numa e o comum (HKLM sem
    elevacao). O protocolo nao consegue diferenciar os dois casos."""
    semear(registro, "HKCU", CN_NOSSO)

    assert cert_windows.policy_cn() == CN_NOSSO
    assert not registro.tem("HKLM", CAMINHO)
    assert protocolo(CN_NOSSO, registro, []).situacao == policy_certificado.JA_ATIVA


def test_g_a_escrita_parcial_real_produz_exatamente_esse_estado(monkeypatch):
    """E o caso comum, nao um caso de laboratorio: sem elevacao, HKLM recusa."""
    falso = RegistroFalso(protegidas=("HKLM",))
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))

    cert_windows.definir_autoselect(CN_NOSSO)

    assert falso.tem("HKCU", CAMINHO)
    assert not falso.tem("HKLM", CAMINHO)


# ── H · payload malformado ────────────────────────────────────────────────────

def test_h_payload_malformado_e_lido_como_ausencia(registro):
    registro.dados["HKCU"][CAMINHO] = {"1": "isto nao e json"}

    assert cert_windows._ler_cn("HKCU") == ""
    assert cert_windows.policy_existe() is False, "existe no registro, some na leitura"


def test_h_malformado_leva_a_sobrescrever_sem_perceber(registro):
    """A ausencia aparente vira licenca para escrever: o protocolo acha o host
    limpo e o guardiao escreve por cima de uma configuracao que ele nao
    entendeu."""
    registro.dados["HKCU"][CAMINHO] = {"1": "isto nao e json"}
    lancamentos = []

    assert protocolo(CN_NOSSO, registro, lancamentos).situacao == (
        policy_certificado.ATIVADA
    )
    assert lancamentos == [CN_NOSSO]


def test_h_json_valido_sem_o_campo_cn_tambem_le_como_vazio(registro):
    registro.dados["HKCU"][CAMINHO] = {"1": json.dumps({"pattern": "https://x"})}

    assert cert_windows._ler_cn("HKCU") == ""


def test_h_o_cn_fora_do_valor_um_e_invisivel(registro):
    """`_ler_cn` le SO o valor "1". Uma policy legitima escrita por outra
    ferramenta, com o mesmo formato mas comecando em outro indice, nao existe
    para nos."""
    registro.dados["HKCU"][CAMINHO] = {"2": entrada(CN_ALHEIO)}

    assert cert_windows._ler_cn("HKCU") == ""
    assert cert_windows.policy_existe() is False
    assert registro.tem("HKCU", CAMINHO), "mas a chave esta la, com conteudo"


def test_h_e_por_isso_a_limpeza_pode_ser_confirmada_sem_a_chave_sair(registro):
    """Consequencia direta: `policy_existe` e o confirmador da 12B.2. Uma chave
    cheia de valores que ele nao le e uma limpeza confirmada que nao limpou."""
    registro.dados["HKCU"][CAMINHO] = {"2": entrada(CN_ALHEIO)}

    assert cert_windows.policy_existe() is False, "confirmaria a remocao"
    assert registro.valores("HKCU", CAMINHO), "com a chave intacta"


# ── I · valores extras ────────────────────────────────────────────────────────

def test_i_valor_fora_da_sequencia_sobrevive_a_nossa_escrita(registro):
    semear(registro, "HKCU", CN_NOSSO)
    registro.dados["HKCU"][CAMINHO]["99"] = entrada(CN_ALHEIO, "https://intranet")

    cert_windows.definir_autoselect(CN_NOSSO)

    assert registro.valores("HKCU", CAMINHO)["99"] == entrada(
        CN_ALHEIO, "https://intranet"
    )


def test_i_nome_nao_numerico_tambem_sobrevive(registro):
    """A limpeza percorre "1", "2", "3"... e para no primeiro ausente. Um valor
    chamado de outra coisa nunca e alcancado."""
    semear(registro, "HKCU", CN_NOSSO)
    registro.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = entrada(CN_ALHEIO)

    cert_windows.definir_autoselect(CN_NOSSO)

    assert "RegraDaEmpresa" in registro.valores("HKCU", CAMINHO)


def test_i_mas_a_limpeza_final_leva_tudo(registro):
    """A assimetria que interessa: a ESCRITA preserva os extras, a LIMPEZA nao.
    `DeleteKey` remove a chave inteira, com o que era nosso e o que nao era."""
    semear(registro, "HKCU", CN_NOSSO)
    registro.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = entrada(CN_ALHEIO)

    cert_windows.limpar_autoselect()

    assert not registro.tem("HKCU", CAMINHO), "a regra alheia foi junto"


def test_i_um_extra_reconhecivel_nao_muda_decisao_nenhuma(registro):
    """Hoje a decisao le um valor so. Valores adicionais nao entram nela."""
    semear(registro, "HKCU", CN_NOSSO)
    registro.dados["HKCU"][CAMINHO]["99"] = entrada(CN_ALHEIO)

    assert protocolo(CN_NOSSO, registro, []).situacao == policy_certificado.JA_ATIVA


# ── J · o comportamento destrutivo, de ponta a ponta ──────────────────────────

def test_j_nenhum_caminho_atual_pergunta_de_quem_e_a_policy():
    """Nem a escrita, nem a leitura, nem a limpeza tem o conceito de dono."""
    with open(cert_windows.__file__, encoding="utf-8") as arquivo:
        codigo = arquivo.read()

    trecho = codigo[codigo.index("def definir_autoselect"):codigo.index("def is_admin")]
    for marca in ("dono", "owner", "preexist", "ownership"):
        assert marca not in trecho.lower(), f"nao ha nocao de {marca} hoje"


def test_j_a_execucao_seguinte_herda_o_que_a_anterior_deixou(registro):
    """Startup apos crash duplo: processo e guardiao morreram, o lease efemero
    sumiu com eles, e a policy persistente ficou. A execucao seguinte adquire o
    lease normalmente e encontra este estado — sem nenhuma forma de saber se ele
    era nosso ou de outra pessoa."""
    semear(registro, "HKCU", CN_ALHEIO)

    assert cert_windows.policy_cn() == CN_ALHEIO
    resultado = protocolo(CN_NOSSO, registro, [])

    assert resultado.situacao == policy_certificado.ATIVADA, "escreve por cima"
    assert resultado.tem_guardiao is True
