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


def test_2f_oserror_ao_ENUMERAR_marca_a_colmeia_ILEGIVEL(registro, monkeypatch):
    """ANTES: a enumeracao parava no primeiro `OSError`, seja ele qual fosse. Um
    erro de acesso no meio da lista era indistinguivel de "acabaram os valores":
    a colmeia saia marcada como LEGIVEL, com a lista TRUNCADA, e quem a lesse
    acreditava ter visto tudo. Era "nao consegui ler" virando "nao existe", no
    unico ponto onde o invariante da 12D.1 nao tinha sido aplicado.

    AGORA so `ERROR_NO_MORE_ITEMS` significa fim. Qualquer outro erro marca a
    colmeia inteira como ilegivel e descarta o que foi lido pela metade —
    entregar meia colmeia como se fosse a colmeia e a forma mais silenciosa de
    apagar essa distincao.
    """
    nossa(registro, "HKCU")
    falhar_enumerando(registro, monkeypatch, "HKCU", no_indice=3)

    hkcu = cert_windows.inventario_da_policy()[0]

    assert hkcu.legivel is False
    assert hkcu.regras == (), "nao entrega lista pela metade"


def test_2f_e_a_decisao_recusa_por_causa_disso(registro, monkeypatch):
    """A consequencia que interessa: uma leitura truncada deixaria de ver
    regras alheias e poderia autorizar EMPRESTAR sobre elas."""
    nossa(registro, "HKCU")
    falhar_enumerando(registro, monkeypatch, "HKCU", no_indice=3)

    decisao = decidir()

    assert decisao.decisao == policy_certificado.RECUSAR
    assert decisao.motivo == policy_certificado.COLMEIA_ILEGIVEL


def test_2f_o_valor_ilegivel_tambem_deixou_de_virar_ausente(registro, monkeypatch):
    """A mesma familia, um nivel abaixo: `QueryValueEx` falhando por acesso
    devolvia `_AUSENTE` — e ausente, para a escrita, significa "pode
    preencher"."""
    def negar_valor(chave, nome):
        raise PermissionError("acesso negado ao valor")

    nossa(registro, "HKCU")
    monkeypatch.setattr(registro, "QueryValueEx", negar_valor)
    chave = registro.OpenKeyEx("HKCU", CAMINHO, 0, registro.KEY_READ)

    assert cert_windows._valor_atual(chave, "1") is cert_windows._ILEGIVEL


def test_2f_e_valor_ilegivel_impede_a_escrita_em_qualquer_colmeia(registro,
                                                                  monkeypatch):
    """Fail-closed: o que nao conseguimos ler nao pode ser sobrescrito, e desde
    a 13A.1 uma colmeia em conflito impede a instalacao inteira."""
    def negar_valor(chave, nome):
        raise PermissionError("acesso negado ao valor")

    registro.dados["HKCU"][CAMINHO] = {}
    monkeypatch.setattr(registro, "QueryValueEx", negar_valor)

    assert cert_windows.definir_autoselect(CN_NOSSO) == cert_windows.NAO_INSTALADA
    assert registro.valores("HKCU", CAMINHO) == {}
    assert registro.valores("HKLM", CAMINHO) == {}, "nem a outra colmeia"


def test_2f_a_enumeracao_distingue_o_fim_do_erro(registro):
    """ANTES: `except OSError: break`, sem olhar qual erro era."""
    fonte = inspect.getsource(cert_windows._inventariar)

    assert "ERROR_NO_MORE_ITEMS" in fonte
    assert 'getattr(erro, "winerror", None)' in fonte
    assert "legivel=False" in fonte


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


def test_14_a_falha_de_ENUMERACAO_agora_e_fail_closed(registro, monkeypatch):
    """ANTES a lista truncada saia como legivel, e `policy_existe` a herdava.

    AGORA a colmeia e ilegivel, e a leitura responde que pode haver estado — o
    que impede devolver o host sobre o que nao foi visto.
    """
    registro.dados["HKCU"][CAMINHO] = {"1": "qualquer coisa"}
    falhar_enumerando(registro, monkeypatch, "HKCU", no_indice=0)

    assert cert_windows.inventario_da_policy()[0].legivel is False
    assert cert_windows.policy_existe() is True


def test_14_valor_ilegivel_impede_confirmar_que_o_nosso_saiu(registro,
                                                             monkeypatch):
    """`policy_owned_existe` le valor a valor, entao nao depende da enumeracao.
    Ele precisava da sua propria correcao — um valor ilegivel pode ser o nosso.
    """
    def negar_valor(chave, nome):
        raise PermissionError("acesso negado ao valor")

    registro.dados["HKCU"][CAMINHO] = {"1": "qualquer coisa"}
    monkeypatch.setattr(registro, "QueryValueEx", negar_valor)

    assert cert_windows.policy_owned_existe(CN_NOSSO) is True


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


# ── §17 · nenhum login comeca sem visibilidade suficiente ────────────────────

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


def _item():
    from automation.planilha import ItemPendente

    return ItemPendente(posicao=0, cnpj="11111111000191", certificado=CN_NOSSO)


def test_17_o_caminho_BORROW_nao_abre_sessao_com_colmeia_ilegivel(
    registro, monkeypatch
):
    """Obrigatorio, porque BORROW nao tem revalidacao do guardiao.

    A policy de HKCU e exatamente a que precisamos. Sem a 13A.2 isso bastaria:
    emprestar, nao escrever nada, e liberar o login na hora — com HKLM podendo
    conter qualquer coisa que o Chrome fosse ler.
    """
    monkeypatch.setattr("time.sleep", lambda _s: None)
    nossa(registro, "HKCU")
    negar_abertura(registro, monkeypatch, "HKLM")
    aberturas = []
    ex = _execucao(monkeypatch, aberturas)

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel) as erro:
        ex.trocar_certificado(_item())

    assert erro.value.motivo == policy_certificado.COLMEIA_ILEGIVEL
    assert aberturas == [], "nenhuma sessao foi aberta"


def test_17_e_nada_foi_escrito_nem_removido(registro, monkeypatch):
    """Recusar por ignorancia tambem significa nao mexer no que nao se viu."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    nossa(registro, "HKCU")
    antes = registro.valores("HKCU", CAMINHO)
    negar_abertura(registro, monkeypatch, "HKLM")
    ex = _execucao(monkeypatch, [])

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        ex.trocar_certificado(_item())

    assert registro.valores("HKCU", CAMINHO) == antes


def test_17_o_caminho_CREATE_tambem_nao_abre_sessao(registro, monkeypatch):
    """O outro caminho, pelo mesmo motivo: host aparentemente vazio, mas com uma
    colmeia que nao sabemos ler."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    negar_abertura(registro, monkeypatch, "HKLM")
    aberturas = []
    ex = _execucao(monkeypatch, aberturas)

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        ex.trocar_certificado(_item())

    assert aberturas == []


def test_17_com_as_duas_colmeias_legiveis_o_caminho_normal_segue(registro,
                                                                 monkeypatch):
    """O contraste: a recusa e por ignorancia, e nao por rigor gratuito."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    nossa(registro, "HKCU")
    aberturas = []
    ex = _execucao(monkeypatch, aberturas)

    assert ex.trocar_certificado(_item()) is True
    assert ex.controle_da_policy is None, "emprestada, e nao nossa"


# ── §15 · a redacao da garantia ──────────────────────────────────────────────

def test_15_a_garantia_e_sobre_instalacoes_ACEITAS(registro):
    """Nao e "a automacao nao pode produzir divergencia": residuo owned parcial
    PODE ficar se a compensacao falhar. O que se garante e que ele nunca e
    aceito como instalacao valida."""
    fonte = inspect.getsource(cert_windows.definir_autoselect)

    assert "ACEITA COMO VALIDA" in fonte
    assert "RESIDUO_OWNED" in fonte


# ── §0 · HISTORICAL_ENUMERATION_TRUNCATION_AMBIGUITY ─────────────────────────

def test_0_truncagem_DEPOIS_dos_nossos_valores_esconderia_o_alheio(registro,
                                                                   monkeypatch):
    """A pergunta que a caracterizacao da 13A.2 NAO tinha feito.

    Ela truncou nos indices 3 e 0 — nunca no 7. E o caso do 7 e o unico que
    produzia um inventario que PARECIA exatamente compativel:

        valores 1..7 lidos, todos nossos
        -> erro de enumeracao antes de revelar o "99" alheio
        -> inventario com o conjunto de nomes exato e os payloads certos
        -> EMPRESTAR, sobre uma colmeia que tem uma regra a mais

    Logo `UNREADABLE_HIVE_BORROW_IDENTITY_RISK` ERA alcancavel por esse caminho,
    e o relatorio da 13A.2 afirmou o contrario. A afirmacao valia para a
    abertura da chave, e nao para a leitura dos valores.

    AGORA a truncagem marca a colmeia como ilegivel, e ilegivel recusa.
    """
    nossa(registro, "HKCU")
    registro.dados["HKCU"][CAMINHO]["99"] = "regra alheia, invisivel na truncagem"
    falhar_enumerando(registro, monkeypatch, "HKCU", no_indice=QUANTAS)

    hkcu = cert_windows.inventario_da_policy()[0]

    assert hkcu.legivel is False, "e nao 'legivel com 1..7'"
    assert decidir().decisao == policy_certificado.RECUSAR


def test_0_e_sem_a_correcao_o_inventario_teria_parecido_compativel(registro,
                                                                   monkeypatch):
    """A prova do contrafactual, sem restaurar o codigo antigo: os sete valores
    que a truncagem teria entregado SAO exatamente os nossos, e um inventario
    com eles e so eles e EMPRESTAR."""
    truncado = policy_certificado.ColmeiaDaPolicy(
        "HKCU", existe=True,
        regras=tuple(
            policy_certificado.RegraDaPolicy(str(i), padrao=url, cn=CN_NOSSO)
            for i, url in enumerate(cert_windows.CERT_URLS, 1)
        ),
    )

    decisao = policy_certificado.avaliar_estado_inicial(
        (truncado, policy_certificado.ColmeiaDaPolicy("HKLM")),
        CN_NOSSO, tuple(cert_windows.CERT_URLS),
    )

    assert decisao.decisao == policy_certificado.EMPRESTAR, (
        "e por isso a truncagem era perigosa: o que ela entregava passava"
    )
