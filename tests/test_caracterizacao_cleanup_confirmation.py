"""A confirmacao de limpeza e o ciclo de vida de uma policy EMPRESTADA.

Escrito ANTES de qualquer mudanca da fatia 12D.1.

Duas perguntas diferentes, e a 12D deixou claro que elas nao sao a mesma:

  STARTUP   "posso usar / criar / preciso recusar?"  -> le e INTERPRETA regras
  LIMPEZA   "sobrou estado que deveria ter saido?"   -> so precisa saber se ha

A segunda foi respondida ate aqui pela primeira, e e isso que este arquivo
prende: `policy_existe()` pergunta "consigo extrair um CN?", e um estado que
existe mas nao pode ser interpretado responde "nao" — ou seja, responde
"limpo".

Nenhum teste toca o registro real, pede UAC, lanca processo elevado ou abre
Chrome. Todos os CNs sao ficticios.
"""
import json

import pytest
from registro_falso import RegistroFalso

import cert_windows
from automation import app, maquina, policy_certificado
from automation.planilha import ItemPendente

CN_A = "ALFA FICTICIA LTDA:11111111000191"
CN_B = "BETA FICTICIA SA:22222222000172"
CAMINHO = cert_windows.REG_PATH


@pytest.fixture(autouse=True)
def _guardiao_vivo(monkeypatch):
    """CHARACTERIZATION_TARGET_CHANGE da fatia 13A.4.

    O app passou a exigir que o PROCESSO guardiao esteja vivo antes de abrir
    sessao e antes de pedir limpeza. Estes testes sempre pressupuseram isso: nao
    havia outro estado possivel, e os dubles de controle daqui nem sao processos.
    Dize-lo explicitamente preserva o que cada assercao ja significava.
    """
    import cert_windows
    from automation import maquina, policy_certificado

    for modulo in (maquina, cert_windows):
        monkeypatch.setattr(modulo, "estado_do_guardiao",
                            lambda _c: policy_certificado.GUARDIAO_VIVO)
    monkeypatch.setattr(maquina, "encerrar_controle_do_guardiao", lambda _c: None)
    monkeypatch.setattr(cert_windows, "encerrar_controle", lambda _c: None)


@pytest.fixture
def registro(monkeypatch):
    falso = RegistroFalso()
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))
    return falso


def registro_com(monkeypatch, protegidas):
    falso = RegistroFalso(protegidas=protegidas)
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))
    return falso


def entrada(cn, url="https://[*.]gov.br"):
    return json.dumps({"pattern": url, "filter": {"SUBJECT": {"CN": cn}}})


def semear(registro, colmeia, cn):
    registro.dados[colmeia][CAMINHO] = {
        str(i): entrada(cn, url) for i, url in enumerate(cert_windows.CERT_URLS, 1)
    }


# ── §1 · o que `policy_existe` faz hoje ───────────────────────────────────────

def test_1_policy_existe_pergunta_por_ESTADO_e_nao_por_um_CN(registro):
    """ANTES: chamava `_ler_cn` em cada colmeia — valor "1", `json.loads`, campo
    filter.SUBJECT.CN. Nao enumerava valores e nao olhava a existencia da chave.
    Quem nao conseguisse interpretar um CN respondia "limpo".

    AGORA consome `inventario_da_policy`, que e a mesma leitura completa que a
    decisao de startup usa. Nenhum parser novo foi criado.
    """
    import inspect

    fonte = inspect.getsource(cert_windows.policy_existe)

    corpo = fonte[fonte.rindex('"""') + 3:]

    assert "inventario_da_policy()" in corpo
    assert "_ler_cn(" not in corpo, "a leitura por CN saiu do caminho da limpeza"


def test_1_e_os_dois_confirmadores_de_limpeza_dependem_dela(registro):
    """O guardiao e o processo principal usam a MESMA pergunta.

    Fatia 13A: a pergunta mudou de "sobrou estado?" para "sobrou estado NOSSO?",
    e mudou nos dois ao mesmo tempo — se so um mudasse, um deles seguraria o
    host por causa de configuracao alheia que o outro decidiu preservar.
    """
    import inspect

    assert "policy_owned_existe(cn)" in inspect.getsource(
        cert_windows._limpar_confirmando
    )
    assert "cert_windows.policy_owned_ainda_existe(controle)" in inspect.getsource(
        maquina.liberar_policy_do_windows
    )


# ── §3 · os falsos "limpo" ────────────────────────────────────────────────────

def test_3a_valor_fora_do_indice_um_conta_como_estado(registro):
    """A. ANTES: `policy_existe()` devolvia False — havia estado, e a limpeza
    seria confirmada."""
    registro.dados["HKCU"][CAMINHO] = {"2": entrada(CN_A)}

    assert registro.tem("HKCU", CAMINHO), "o estado esta la"
    assert cert_windows.policy_existe() is True, "e a limpeza NAO e confirmada"


def test_3b_payload_malformado_conta_como_estado(registro):
    """B: JSON quebrado. ANTES devolvia False."""
    registro.dados["HKCU"][CAMINHO] = {"1": "{isto nao fecha"}

    assert cert_windows.policy_existe() is True


def test_3c_payload_desconhecido_conta_como_estado(registro):
    """C: REG_SZ com outra forma — nem pattern nem filter. ANTES: False."""
    registro.dados["HKLM"][CAMINHO] = {"1": json.dumps({"regra": "outra coisa"})}

    assert cert_windows.policy_existe() is True


def test_3d_uma_colmeia_vazia_e_a_outra_com_residuo_ilegivel(registro):
    """D: HKCU realmente removida, HKLM com estado que nao sabemos ler.

    ANTES a colmeia limpa mandava na resposta e a confirmacao dizia vazio."""
    registro.dados["HKLM"][CAMINHO] = {"1": "residuo ilegivel"}

    assert not registro.tem("HKCU", CAMINHO)
    assert registro.tem("HKLM", CAMINHO)
    assert cert_windows.policy_existe() is True, "uma basta para nao confirmar"


def test_3d_colmeia_ILEGIVEL_tambem_impede_a_confirmacao(registro, monkeypatch):
    """Ignorancia nao e ausencia. Se nao conseguimos ler a colmeia, nao podemos
    afirmar que ela esta vazia."""
    def negar(colmeia, caminho, reservado, acesso):
        if colmeia == "HKLM":
            raise PermissionError("acesso negado")
        raise FileNotFoundError(caminho)

    monkeypatch.setattr(registro, "OpenKeyEx", negar)

    assert cert_windows.policy_existe() is True


def test_3d_chave_vazia_ainda_e_estado_a_remover(registro):
    """ASSIMETRIA DELIBERADA com o startup: sem regras o Chrome nao seleciona
    nada, entao para a decisao inicial isto e "host limpo". Para a limpeza nao
    e: a chave e coisa que a nossa escrita cria e a nossa limpeza tem de tirar.
    """
    from automation import policy_certificado

    registro.dados["HKCU"][CAMINHO] = {}

    assert cert_windows.policy_existe() is True
    assert policy_certificado.avaliar_estado_inicial(
        cert_windows.inventario_da_policy(), CN_A, tuple(cert_windows.CERT_URLS)
    ).decisao == policy_certificado.CRIAR


def test_3d_com_residuo_LEGIVEL_a_confirmacao_ja_funciona_hoje(registro):
    """O contraste que mostra onde esta o defeito: se o residuo tem CN no valor
    "1", ele e detectado. O problema nunca foi "nao olha a outra colmeia"; e
    "so enxerga o que consegue interpretar"."""
    semear(registro, "HKLM", CN_A)

    assert cert_windows.policy_existe() is True


def test_3e_as_duas_realmente_vazias_confirmam(registro):
    """E: o caso normal continua funcionando."""
    assert cert_windows.policy_existe() is False
    assert not registro.tem("HKCU", CAMINHO) and not registro.tem("HKLM", CAMINHO)


def test_3f_deletekey_que_falha_numa_colmeia_e_detectado(registro, monkeypatch):
    """F: HKLM sem privilegio. `limpar_autoselect` engole o erro por colmeia, e
    e a CONFIRMACAO que precisa perceber. Com payload nosso, ela percebe."""
    falso = registro_com(monkeypatch, protegidas=("HKLM",))
    semear(falso, "HKCU", CN_A)
    semear(falso, "HKLM", CN_A)

    cert_windows.limpar_autoselect()

    assert not falso.tem("HKCU", CAMINHO)
    assert falso.tem("HKLM", CAMINHO), "ficou, e o erro foi engolido"
    assert cert_windows.policy_existe() is True, "mas a confirmacao pega"


def test_3f_a_mesma_falha_com_payload_ilegivel_tambem_e_detectada(
    registro, monkeypatch
):
    """O caso que dava nome ao finding: `DeleteKey` falha em HKLM e o que ficou
    nao e interpretavel.

    ANTES as duas fraquezas se somavam e a limpeza era dada como confirmada —
    CLEANUP_CONFIRMATION_FALSE_NEGATIVE. AGORA a existencia basta.
    """
    falso = registro_com(monkeypatch, protegidas=("HKLM",))
    falso.dados["HKLM"][CAMINHO] = {"1": "residuo ilegivel"}

    cert_windows.limpar_autoselect()

    assert falso.tem("HKLM", CAMINHO)
    assert cert_windows.policy_existe() is True


# ── §5 · o que isso custa aos invariantes da 12B.2 / 12C ──────────────────────

def test_5_o_processo_principal_NAO_declara_removida_a_policy_NOSSA_que_ficou(
    registro, monkeypatch
):
    """ANTES da 12D.1: `liberar_policy` devolvia True com estado ilegivel ainda
    escrito. ANTES da 13A: devolvia False com QUALQUER estado, inclusive alheio.

    AGORA a pergunta e a certa. Estado nosso que ficou -> False, e o app mantem
    a posse.
    """
    monkeypatch.setattr("time.sleep", lambda _s: None)
    semear(registro, "HKCU", CN_A)
    monkeypatch.setattr(cert_windows, "pedir_limpeza", lambda controle: None)

    assert maquina.liberar_policy_do_windows(_Controle(CN_A)) is False
    assert registro.tem("HKCU", CAMINHO)


def test_5_mas_estado_ALHEIO_que_ficou_nao_prende_o_host(registro, monkeypatch):
    """O outro lado, e o motivo de a 13A ter mexido aqui: preservar uma regra
    que nao e nossa nao pode virar bloqueio eterno do host."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    registro.dados["HKCU"][CAMINHO] = {"RegraDaEmpresa": entrada(CN_B)}
    monkeypatch.setattr(cert_windows, "pedir_limpeza", lambda controle: None)

    assert maquina.liberar_policy_do_windows(_Controle(CN_A)) is True
    assert registro.tem("HKCU", CAMINHO), "e a regra alheia continua la"


def test_5_o_guardiao_encerra_o_lifecycle_como_sucesso(monkeypatch):
    """Consequencia direta no HOST_RELEASE_SAFE: `_limpar_confirmando` devolve
    True, o guardiao sai do laco, fecha o lease — e devolve o host com estado
    ainda instalado.

    O falso positivo aparecia quando a remocao FALHAVA — que e exatamente o caso
    que a confirmacao existe para pegar.

    Fatia 13A: o que ele remove sao os valores OWNED, e o que ele confirma e a
    ausencia DELES. A colmeia protegida recusa a abertura para escrita, nada
    sai, e ele nao confirma.
    """
    monkeypatch.setattr("time.sleep", lambda _s: None)
    falso = registro_com(monkeypatch, protegidas=("HKCU",))
    semear(falso, "HKCU", CN_A)

    assert cert_windows._limpar_confirmando(CN_A) is False
    assert falso.tem("HKCU", CAMINHO), "o estado ficou, e ele NAO confirmou"


# ── §6 · §8 · BORROWED e a troca de certificado ───────────────────────────────

class _PlanilhaInerte:
    def salvar(self, *a, **k):
        pass

    def descartar(self):
        pass

    def mapa_status(self, *a, **k):
        return {}


class _Controle:
    def __init__(self, cn):
        self.cn = cn


@pytest.fixture
def maquina_real(registro, monkeypatch):
    """O caminho inteiro sobre o registro falso: decisao, guardiao e limpeza.

    Nada de duble de `garantir_policy_do_windows` aqui — o assunto e justamente
    o que a decisao real faz numa troca de certificado.
    """
    monkeypatch.setattr("time.sleep", lambda _s: None)

    def lancar(cn):
        cert_windows.definir_autoselect(cn)
        return _Controle(cn)

    monkeypatch.setattr(cert_windows, "_lancar_guardiao", lancar)
    # O guardiao de mentira limpa como o de verdade passou a limpar (13A):
    # compare-and-delete dos valores owned, e nunca a chave inteira.
    monkeypatch.setattr(
        cert_windows, "pedir_limpeza",
        lambda controle: cert_windows.remover_autoselect_owned(controle.cn),
    )
    return registro


def execucao(monkeypatch):
    from automation.captcha import ConfigCaptcha

    monkeypatch.setattr(app.maquina, "abrir_sessao", lambda *a, **k: None)
    ex = app._Execucao(_PlanilhaInerte(), "p.xlsx", ConfigCaptcha(api_key="x"), None)
    # As chaves sao os nomes como a planilha os escreve: e por elas que
    # `buscar_certificado` resolve, e a identidade e o subject_cn.
    ex.certificados = {
        CN_A: {"subject_cn": CN_A, "serial": "0A01"},
        CN_B: {"subject_cn": CN_B, "serial": "0B02"},
    }
    return ex


def item(cn, posicao=0):
    return ItemPendente(posicao=posicao, cnpj="11111111000191", certificado=cn, linha=posicao + 2)


def test_8a_host_vazio_com_dois_certificados_continua_funcionando(
    maquina_real, monkeypatch
):
    """A: EMPTY -> criamos A, limpamos, criamos B. E o caso normal, e provar que
    a 12D nao o quebrou e metade do motivo desta fatia existir."""
    ex = execucao(monkeypatch)

    assert ex.trocar_certificado(item(CN_A)) is True
    assert cert_windows.policy_cn() == CN_A
    primeiro = ex.controle_da_policy
    assert primeiro is not None, "e nosso"

    assert ex.trocar_certificado(item(CN_B, 1)) is True
    assert cert_windows.policy_cn() == CN_B
    assert ex.controle_da_policy is not primeiro, "guardiao novo, o antigo saiu"


def test_8b_borrowed_com_um_certificado_so_funciona_sem_modificar(
    maquina_real, monkeypatch
):
    """B: policy preexistente exatamente compativel, planilha so com CN_A."""
    semear(maquina_real, "HKCU", CN_A)
    antes = maquina_real.valores("HKCU", CAMINHO)
    ex = execucao(monkeypatch)

    assert ex.trocar_certificado(item(CN_A)) is True

    assert ex.controle_da_policy is None, "emprestada: nao e nossa"
    assert maquina_real.valores("HKCU", CAMINHO) == antes, "e nada foi tocado"


def test_8c_borrowed_e_depois_outro_certificado_RECUSA(maquina_real, monkeypatch):
    """C: e a consequencia que precisa ficar explicita.

    A execucao comeca emprestando a policy de CN_A. Quando a planilha pede CN_B,
    `liberar_policy` e no-op (nao ha controle), a policy de CN_A continua la, e a
    validacao de startup a ve como configuracao de outra pessoa.
    """
    semear(maquina_real, "HKCU", CN_A)
    ex = execucao(monkeypatch)
    ex.trocar_certificado(item(CN_A))

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel) as erro:
        ex.trocar_certificado(item(CN_B, 1))

    assert erro.value.motivo == policy_certificado.OUTRO_CERTIFICADO


def test_8c_e_a_policy_emprestada_permanece_intacta(maquina_real, monkeypatch):
    """A recusa nao destroi o que nao e nosso — nem mesmo na saida."""
    semear(maquina_real, "HKCU", CN_A)
    antes = maquina_real.valores("HKCU", CAMINHO)
    ex = execucao(monkeypatch)
    ex.trocar_certificado(item(CN_A))

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        ex.trocar_certificado(item(CN_B, 1))

    assert maquina_real.valores("HKCU", CAMINHO) == antes


def test_6_liberar_policy_e_no_op_quando_a_policy_e_emprestada(
    maquina_real, monkeypatch
):
    """O elo da corrente: sem controle, nao ha o que pedir — e por isso a policy
    de CN_A ainda esta escrita quando CN_B e pedido."""
    semear(maquina_real, "HKCU", CN_A)
    ex = execucao(monkeypatch)
    ex.trocar_certificado(item(CN_A))

    ex.liberar_policy()

    assert cert_windows.policy_cn() == CN_A, "continua exatamente onde estava"


def test_6_o_processamento_restante_para(maquina_real, monkeypatch):
    """Nao e "pula o item": a excecao sobe por `trocar_certificado` e sai de
    `executar`. Fail-closed, e intencional."""
    import inspect

    fonte = inspect.getsource(app._Execucao.trocar_certificado)

    assert "except" not in fonte, "ninguem a converte em evento"


# ── §11 · o cleanup remove a chave inteira ────────────────────────────────────

def test_11_valor_externo_que_aparece_durante_a_execucao_NAO_sai_mais_junto(
    maquina_real, monkeypatch
):
    """GUARDIAN_CLEANUP_DELETES_WHOLE_KEY, fechado pela fatia 13A.

    ANTES: a validacao de startup protegia o que existia ANTES da nossa
    mutacao, e depois que passavamos a possuir a chave `limpar_autoselect`
    continuava sendo `DeleteKey` — levando junto o que aparecesse no meio do
    caminho.

    AGORA a limpeza owned compara valor a valor. O detalhe do que sobrevive
    esta em `test_13a_valor_externo_que_aparece_durante_a_execucao_SOBREVIVE`;
    aqui fica o contraste com o que este teste afirmava.
    """
    ex = execucao(monkeypatch)
    ex.trocar_certificado(item(CN_A))

    # Software externo acrescenta uma regra a chave que agora e nossa.
    maquina_real.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = entrada(CN_B)

    ex.liberar_policy()

    assert maquina_real.tem("HKCU", CAMINHO), "a regra externa ficou"


def test_11_o_host_lease_nao_protege_contra_software_externo(maquina_real):
    """O lease so exclui outras instancias de DebitosEmAberto. Qualquer outro
    processo com privilegio escreve na mesma chave."""
    import inspect

    from automation import exclusividade_host

    fonte = inspect.getsource(exclusividade_host)

    assert "Software\\Policies" not in fonte
    assert "winreg" not in fonte, "o lease nao sabe da existencia do registro"


# ── §10 · a ferramenta de saneamento que ja existe ────────────────────────────

def test_10_o_clean_e_uma_limpeza_cega_das_duas_colmeias():
    """`cert_windows.py --clean` chama `limpar_autoselect` e mais nada: tenta as
    duas colmeias, engole o erro de cada uma e nao confirma nada."""
    fonte = (
        __import__("pathlib").Path(cert_windows.__file__)
    ).read_text(encoding="utf-8")
    trecho = fonte[fonte.index('sys.argv[1] == "--clean"'):]

    assert "limpar_autoselect()" in trecho[:200]
    assert "policy_existe" not in trecho[:200], "nao confirma"


def test_10_e_sem_elevacao_ele_nao_alcanca_hklm(monkeypatch):
    """Por isso ele nao pode ser documentado como saneamento completo."""
    falso = registro_com(monkeypatch, protegidas=("HKLM",))
    semear(falso, "HKCU", CN_A)
    semear(falso, "HKLM", CN_A)

    cert_windows.limpar_autoselect()

    assert not falso.tem("HKCU", CAMINHO)
    assert falso.tem("HKLM", CAMINHO), "HKLM sobrevive"


# ── §5 · a cadeia inteira, do residuo ate o lease ─────────────────────────────

def test_5_cadeia_o_app_mantem_a_posse_quando_sobrou_estado(registro, monkeypatch):
    """Elo 1: limpeza pedida -> residuo existe -> `liberar_policy` devolve False
    -> o app NAO larga o controle. A policy continua sendo desta execucao."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    semear(registro, "HKCU", CN_A)
    monkeypatch.setattr(cert_windows, "pedir_limpeza", lambda controle: None)

    ex = execucao(monkeypatch)
    ex.controle_da_policy = _Controle(CN_A)
    ex.liberar_policy()

    assert ex.controle_da_policy is not None, "continua nossa"


def test_5_cadeia_o_guardiao_nao_sai_do_laco(registro, monkeypatch):
    """Elo 2: `_limpar_confirmando` devolve False, e o `break` que leva ao
    `finally` — onde o lease e fechado por ultimo — esta atras dele."""
    import inspect

    monkeypatch.setattr("time.sleep", lambda _s: None)
    falso = registro_com(monkeypatch, protegidas=("HKCU",))
    semear(falso, "HKCU", CN_A)

    assert cert_windows._limpar_confirmando(CN_A) is False

    fonte = inspect.getsource(cert_windows.guardiao)
    assert "if _limpar_confirmando(cn):" in fonte
    assert fonte.index("if _limpar_confirmando(cn):") < fonte.index("break")
    assert fonte.index("break") < fonte.index("finally:")


def test_5_cadeia_HOST_RELEASE_SAFE_continua_valendo(registro, monkeypatch):
    """Elo 3, e o invariante da 12C: o host so e devolvido depois de limpeza
    CONFIRMADA — e a confirmacao agora exige ausencia real, nao ausencia de CN
    interpretavel. A fatia 12D.1 fortalece o predicado sem mexer no lease.
    """
    import inspect

    from automation import exclusividade_host

    monkeypatch.setattr("time.sleep", lambda _s: None)
    falso = registro_com(monkeypatch, protegidas=("HKCU",))
    semear(falso, "HKCU", CN_A)

    assert cert_windows._limpar_confirmando(CN_A) is False

    # E o lease continua sem saber nada de policy: nada nele mudou.
    fonte = inspect.getsource(exclusividade_host)
    for chamada in ("policy_existe(", "limpar_autoselect(", "inventario_da_policy(",
                    "import winreg", "import cert_windows"):
        assert chamada not in fonte, f"o lease passou a depender de {chamada}"


def test_5_com_o_host_realmente_limpo_a_cadeia_fecha(registro, monkeypatch):
    """O outro lado: sem residuo, a limpeza confirma, o app larga o controle e o
    guardiao pode encerrar. O caminho normal nao regrediu."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    semear(registro, "HKCU", CN_A)
    monkeypatch.setattr(
        cert_windows, "pedir_limpeza", lambda controle: cert_windows.limpar_autoselect()
    )

    ex = execucao(monkeypatch)
    ex.controle_da_policy = _Controle(CN_A)
    ex.liberar_policy()

    assert ex.controle_da_policy is None
    assert cert_windows.policy_owned_existe(CN_A) is False


# ── 13A · §18 · §19 · o host volta quando o NOSSO sai ────────────────────────

def test_13a_o_guardiao_encerra_com_estado_externo_restante(maquina_real,
                                                            monkeypatch):
    """§19 K: estado externo residual NAO viola "nenhum state OWNED continua".

    O guardiao confirma, sai do laco e cai no `finally`, onde o lease e fechado
    por ultimo. Preservar a regra alheia deixou de ser motivo para segurar o
    host.
    """
    ex = execucao(monkeypatch)
    ex.trocar_certificado(item(CN_A))
    maquina_real.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = entrada(CN_B)

    ex.liberar_policy()

    assert ex.controle_da_policy is None, "confirmado: o host pode voltar"
    assert "RegraDaEmpresa" in maquina_real.valores("HKCU", CAMINHO)


def test_13a_e_NAO_encerra_enquanto_o_nosso_continua(maquina_real, monkeypatch):
    """§19 J, o outro lado: o que segura o host e o nosso, e so ele."""
    monkeypatch.setattr(cert_windows, "pedir_limpeza", lambda controle: None)
    ex = execucao(monkeypatch)
    ex.trocar_certificado(item(CN_A))

    ex.liberar_policy()

    assert ex.controle_da_policy is not None
    assert cert_windows.policy_owned_existe(CN_A) is True


def test_13a_valor_externo_que_aparece_durante_a_execucao_SOBREVIVE(
    maquina_real, monkeypatch
):
    """GUARDIAN_CLEANUP_DELETES_WHOLE_KEY, fechado.

    ANTES este mesmo cenario terminava com a chave inteira apagada e a regra
    externa junto. A validacao de startup nao o cobria: ela protege o que
    existia ANTES da nossa mutacao, e este valor apareceu depois.
    """
    ex = execucao(monkeypatch)
    ex.trocar_certificado(item(CN_A))

    maquina_real.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = entrada(CN_B)

    ex.liberar_policy()

    assert maquina_real.valores("HKCU", CAMINHO) == {"RegraDaEmpresa": entrada(CN_B)}


# ── 13A · a troca de certificado quando a NOSSA policy nao saiu ───────────────

def test_13a_troca_de_certificado_para_se_a_nossa_policy_nao_saiu(
    maquina_real, monkeypatch
):
    """ANTES: a liberacao falhava, a policy de CN_A continuava instalada, e o
    guardiao de CN_B escrevia por cima dela — inclusive por cima da nossa.

    AGORA a escrita nao sobrescreve nada, e insistir daria uma de duas saidas
    ruins: metade da policy trocada, ou o Chrome auto-selecionando o certificado
    ANTERIOR. Fail-closed, e a condicao e do host — nao se pula o item.
    """
    ex = execucao(monkeypatch)
    ex.trocar_certificado(item(CN_A))
    monkeypatch.setattr(cert_windows, "pedir_limpeza", lambda controle: None)

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel) as erro:
        ex.trocar_certificado(item(CN_B, 1))

    assert erro.value.motivo == policy_certificado.POLICY_ANTERIOR_NAO_REMOVIDA


def test_13a_e_a_policy_anterior_fica_intacta(maquina_real, monkeypatch):
    """Parar tambem significa nao mexer no que ficou: o guardiao de CN_A
    continua sendo o responsavel por ela."""
    ex = execucao(monkeypatch)
    ex.trocar_certificado(item(CN_A))
    monkeypatch.setattr(cert_windows, "pedir_limpeza", lambda controle: None)
    antes = maquina_real.valores("HKCU", CAMINHO)

    with pytest.raises(policy_certificado.ConfiguracaoDeHostIncompativel):
        ex.trocar_certificado(item(CN_B, 1))

    assert maquina_real.valores("HKCU", CAMINHO) == antes
    assert ex.controle_da_policy is not None, "continua nossa, e continua vigiada"


def test_13a_o_erro_da_troca_nao_identifica_ninguem():
    """§21 da 12D continua valendo para o motivo novo."""
    texto = str(policy_certificado.ConfiguracaoDeHostIncompativel(
        policy_certificado.POLICY_ANTERIOR_NAO_REMOVIDA
    ))

    for sentinela in (CN_A, CN_B, CAMINHO, "HKLM", "11111111000191"):
        assert sentinela not in texto
