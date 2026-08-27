"""Ausente e ilegivel sao coisas diferentes — e num ponto ainda nao sao.

Escrito ANTES de qualquer mudanca da fatia 13A.2.

O invariante ja aprovado na 12D.1 diz: ignorancia nao e ausencia. Este arquivo
percorre os seis estados possiveis de uma colmeia e mostra onde o codigo cumpre
isso e onde nao cumpre.

    A. chave inexistente
    B. chave existente e vazia
    C. chave existente e legivel
    D. payload malformado
    E. PermissionError ao ABRIR
    F. OSError generico ao ENUMERAR      <- e aqui que a distincao se perde

Nenhum teste toca o registro real, pede UAC, lanca processo elevado ou abre
Chrome. Todos os CNs sao ficticios.
"""
import inspect

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


def nossa(registro, colmeia, cn=CN_NOSSO):
    registro.dados[colmeia][CAMINHO] = dict(cert_windows._valores_esperados(cn))


def negar_abertura(registro, monkeypatch, colmeia_negada):
    """PermissionError ao ABRIR aquela colmeia; a outra abre normalmente."""
    original = registro.OpenKeyEx

    def abrir(colmeia, caminho, reservado, acesso):
        if colmeia == colmeia_negada:
            raise PermissionError("acesso negado")
        return original(colmeia, caminho, reservado, acesso)

    monkeypatch.setattr(registro, "OpenKeyEx", abrir)


def falhar_enumerando(registro, monkeypatch, colmeia_falha, no_indice=1):
    """OSError generico no meio da enumeracao — nao e 'acabaram os valores'."""
    original = registro.EnumValue

    def enumerar(chave, indice):
        if chave.colmeia == colmeia_falha and indice >= no_indice:
            raise PermissionError("acesso negado ao valor")
        return original(chave, indice)

    monkeypatch.setattr(registro, "EnumValue", enumerar)


def decidir(cn=CN_NOSSO):
    return policy_certificado.avaliar_estado_inicial(
        cert_windows.inventario_da_policy(), cn, tuple(cert_windows.CERT_URLS)
    )


# ── §2 · os seis estados, como o inventario os representa hoje ───────────────

def test_2a_chave_inexistente(registro):
    """ABSENT: existe=False, legivel=True, sem regras."""
    hkcu = cert_windows.inventario_da_policy()[0]

    assert (hkcu.existe, hkcu.legivel, hkcu.regras) == (False, True, ())


def test_2b_chave_existente_e_vazia(registro):
    registro.dados["HKCU"][CAMINHO] = {}

    hkcu = cert_windows.inventario_da_policy()[0]

    assert (hkcu.existe, hkcu.legivel, hkcu.regras) == (True, True, ())


def test_2c_chave_existente_e_legivel(registro):
    nossa(registro, "HKCU")

    hkcu = cert_windows.inventario_da_policy()[0]

    assert hkcu.existe is True and hkcu.legivel is True
    assert {r.nome for r in hkcu.regras} == {str(i) for i in range(1, QUANTAS + 1)}
    assert all(r.reconhecida for r in hkcu.regras)


def test_2d_payload_malformado(registro):
    """MALFORMED e diferente de UNREADABLE: lemos o valor, e nao entendemos."""
    registro.dados["HKCU"][CAMINHO] = {"1": "isto nao e json"}

    hkcu = cert_windows.inventario_da_policy()[0]

    assert hkcu.existe is True and hkcu.legivel is True, "lemos, sim"
    assert hkcu.regras[0].reconhecida is False, "so nao entendemos"


def test_2e_permission_error_ao_ABRIR_e_marcado_ilegivel(registro, monkeypatch):
    """E aqui o codigo faz a coisa certa: nao converte em ausencia."""
    negar_abertura(registro, monkeypatch, "HKLM")

    hklm = cert_windows.inventario_da_policy()[1]

    assert hklm.existe is True and hklm.legivel is False


def test_2f_oserror_ao_ENUMERAR_e_lido_como_FIM_DOS_VALORES(registro, monkeypatch):
    """O defeito.

    A enumeracao para no primeiro `OSError`, seja ele qual for. Um erro de
    acesso no meio da lista e indistinguivel de "acabaram os valores": a colmeia
    sai marcada como LEGIVEL, com a lista TRUNCADA — e quem le acredita que viu
    tudo.

    E "nao consegui ler" virando "nao existe", exatamente o que o invariante da
    12D.1 proibe. Ele foi aplicado na abertura da chave e nao na leitura dos
    valores.
    """
    nossa(registro, "HKCU")
    falhar_enumerando(registro, monkeypatch, "HKCU", no_indice=3)

    hkcu = cert_windows.inventario_da_policy()[0]

    assert hkcu.legivel is True, "diz que leu"
    assert len(hkcu.regras) == 3, "e viu tres dos sete"


def test_2f_a_enumeracao_nao_distingue_o_fim_do_erro(registro):
    fonte = inspect.getsource(cert_windows._inventariar)

    assert "except OSError:\n                break" in fonte
    assert "winerror" not in fonte, "nao ha nenhuma distincao de codigo de erro"


# ── §3 · a decisao de startup com uma colmeia ilegivel ───────────────────────

def test_3_hkcu_compativel_e_hklm_ilegivel(registro, monkeypatch):
    """O caso critico do §4: BORROW nao escreve, nao lanca guardiao elevado, e
    libera o login na hora. Se a outra colmeia nao pode ser lida, ninguem vai
    ter uma segunda chance de olhar."""
    nossa(registro, "HKCU")
    negar_abertura(registro, monkeypatch, "HKLM")

    decisao = decidir()

    assert decisao.decisao == policy_certificado.RECUSAR
    assert decisao.motivo == policy_certificado.COLMEIA_ILEGIVEL


def test_3_o_inverso_tambem_recusa(registro, monkeypatch):
    nossa(registro, "HKLM")
    negar_abertura(registro, monkeypatch, "HKCU")

    assert decidir().decisao == policy_certificado.RECUSAR


def test_3_a_recusa_por_ilegibilidade_vem_ANTES_de_tudo(registro, monkeypatch):
    """Mesmo com a outra colmeia perfeita. Ignorancia sobre uma basta."""
    fonte = inspect.getsource(policy_certificado.avaliar_estado_inicial)
    corpo = fonte[fonte.rindex(chr(34) * 3) + 3:]

    assert corpo.index("not colmeia.legivel") < corpo.index("com_conteudo")


def test_4_UNREADABLE_HIVE_BORROW_IDENTITY_RISK_nao_existe(registro, monkeypatch):
    """A pergunta do §4, respondida: nao, EMPRESTAR nao acontece com uma colmeia
    ilegivel. O principio fail-closed ja estava aplicado aqui."""
    nossa(registro, "HKCU")
    negar_abertura(registro, monkeypatch, "HKLM")

    assert decidir().decisao != policy_certificado.EMPRESTAR


def test_5_host_vazio_com_a_outra_colmeia_ilegivel_nao_autoriza_CRIAR(
    registro, monkeypatch
):
    """§5: a colmeia legivel esta vazia, e a outra nao sabemos. Nao e EMPTY."""
    negar_abertura(registro, monkeypatch, "HKLM")

    assert decidir().decisao == policy_certificado.RECUSAR


# ── §13 · §14 · as duas leituras de ciclo de vida ────────────────────────────

def test_13_policy_existe_ja_e_fail_closed(registro, monkeypatch):
    negar_abertura(registro, monkeypatch, "HKLM")

    assert cert_windows.policy_existe() is True


def test_14_policy_owned_existe_ja_e_fail_closed(registro, monkeypatch):
    """Essencial para o host release: colmeia ilegivel nao prova que o nosso
    estado saiu."""
    negar_abertura(registro, monkeypatch, "HKLM")

    assert cert_windows.policy_owned_existe(CN_NOSSO) is True


def test_14_mas_a_falha_de_ENUMERACAO_engana_as_duas(registro, monkeypatch):
    """A mesma raiz do §2 F, agora no ciclo de vida: `policy_owned_existe` le
    valor a valor pelo nome, entao ele nao depende da enumeracao — mas
    `policy_existe`, que consome o inventario, herda a lista truncada.
    """
    registro.dados["HKCU"][CAMINHO] = {}
    falhar_enumerando(registro, monkeypatch, "HKCU", no_indice=0)

    hkcu = cert_windows.inventario_da_policy()[0]
    assert hkcu.legivel is True, "e nao deveria"


# ── §16 · a pos-condicao da instalacao ───────────────────────────────────────

def test_16_hkcu_nossa_e_hklm_ilegivel_nao_e_instalacao_coerente(
    registro, monkeypatch
):
    nossa(registro, "HKCU")
    negar_abertura(registro, monkeypatch, "HKLM")

    assert cert_windows._coerente(CN_NOSSO) is False


def test_16_hkcu_nossa_e_hklm_comprovadamente_ausente_e_coerente(registro):
    """O contraste: ausencia comprovada continua sendo aceita, e este e o caso
    normal de quem nao conseguiu elevacao."""
    nossa(registro, "HKCU")

    assert cert_windows._coerente(CN_NOSSO) is True


# ── §20 · o que a 13A fez nao pode ter sido desfeito ─────────────────────────

def test_20_a_escrita_continua_recusando_colmeia_ilegivel(registro, monkeypatch):
    negar_abertura(registro, monkeypatch, "HKLM")

    assert cert_windows.definir_autoselect(CN_NOSSO) == cert_windows.NAO_INSTALADA
    assert registro.valores("HKCU", CAMINHO) == {}, "nem a colmeia legivel"


def test_20_o_caminho_normal_continua_funcionando(registro):
    assert cert_windows.definir_autoselect(CN_NOSSO) == cert_windows.INSTALADA
    assert decidir().decisao == policy_certificado.EMPRESTAR
