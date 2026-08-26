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

def test_1_policy_existe_pergunta_por_um_CN_e_nao_por_estado(registro):
    """Ela chama `_ler_cn` em cada colmeia: valor "1", `json.loads`, e o campo
    filter.SUBJECT.CN. Nao usa `inventario_da_policy`, nao enumera valores e nao
    olha a existencia da chave."""
    import inspect

    fonte = inspect.getsource(cert_windows.policy_existe)

    assert "_ler_cn" in fonte
    assert "inventario_da_policy" not in fonte
    assert "OpenKeyEx" not in fonte, "nao pergunta se a chave existe"


def test_1_e_os_dois_confirmadores_de_limpeza_dependem_dela(registro):
    """O guardiao e o processo principal usam a MESMA pergunta."""
    import inspect

    assert "policy_existe()" in inspect.getsource(cert_windows._limpar_confirmando)
    assert "cert_windows.policy_existe" in inspect.getsource(
        maquina.liberar_policy_do_windows
    )


# ── §3 · os falsos "limpo" ────────────────────────────────────────────────────

def test_3a_valor_fora_do_indice_um_e_lido_como_ausencia(registro):
    """A: HKCU tem so o valor "2". Ha estado, e a confirmacao diz vazio."""
    registro.dados["HKCU"][CAMINHO] = {"2": entrada(CN_A)}

    assert registro.tem("HKCU", CAMINHO), "o estado esta la"
    assert cert_windows.policy_existe() is False, "e a limpeza seria confirmada"


def test_3b_payload_malformado_e_lido_como_ausencia(registro):
    """B: JSON quebrado."""
    registro.dados["HKCU"][CAMINHO] = {"1": "{isto nao fecha"}

    assert cert_windows.policy_existe() is False


def test_3c_payload_desconhecido_e_lido_como_ausencia(registro):
    """C: REG_SZ com outra forma — nem pattern nem filter."""
    registro.dados["HKLM"][CAMINHO] = {"1": json.dumps({"regra": "outra coisa"})}

    assert cert_windows.policy_existe() is False


def test_3d_uma_colmeia_vazia_e_a_outra_com_residuo_ilegivel(registro):
    """D: HKCU realmente removida, HKLM com estado que nao sabemos ler."""
    registro.dados["HKLM"][CAMINHO] = {"1": "residuo ilegivel"}

    assert not registro.tem("HKCU", CAMINHO)
    assert registro.tem("HKLM", CAMINHO)
    assert cert_windows.policy_existe() is False, "confirma vazio com residuo"


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


def test_3f_a_mesma_falha_com_payload_ilegivel_passa_batida(registro, monkeypatch):
    """E aqui as duas fraquezas se somam: `DeleteKey` falha em HKLM, o que ficou
    nao e interpretavel, e a limpeza e dada como confirmada."""
    falso = registro_com(monkeypatch, protegidas=("HKLM",))
    falso.dados["HKLM"][CAMINHO] = {"1": "residuo ilegivel"}

    cert_windows.limpar_autoselect()

    assert falso.tem("HKLM", CAMINHO)
    assert cert_windows.policy_existe() is False, "CLEANUP_CONFIRMATION_FALSE_NEGATIVE"


# ── §5 · o que isso custa aos invariantes da 12B.2 / 12C ──────────────────────

def test_5_o_processo_principal_declara_a_policy_removida(registro, monkeypatch):
    """`liberar_policy` devolve True e o app larga o controle — a policy deixa
    de ser de alguem, com estado ainda escrito."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    registro.dados["HKCU"][CAMINHO] = {"2": entrada(CN_A)}
    monkeypatch.setattr(cert_windows, "pedir_limpeza", lambda controle: None)

    assert maquina.liberar_policy_do_windows(object()) is True
    assert registro.tem("HKCU", CAMINHO), "e o estado continua la"


def test_5_o_guardiao_encerra_o_lifecycle_como_sucesso(monkeypatch):
    """Consequencia direta no HOST_RELEASE_SAFE: `_limpar_confirmando` devolve
    True, o guardiao sai do laco, fecha o lease — e devolve o host com estado
    ainda instalado.

    O guardiao chama `limpar_autoselect` ANTES de conferir, e `DeleteKey` leva a
    chave inteira: com privilegio, o residuo ilegivel sai de qualquer jeito. O
    falso positivo aparece quando a remocao FALHA — que e exatamente o caso que
    a confirmacao existe para pegar.
    """
    monkeypatch.setattr("time.sleep", lambda _s: None)
    falso = registro_com(monkeypatch, protegidas=("HKCU",))
    falso.dados["HKCU"][CAMINHO] = {"2": entrada(CN_A)}

    assert cert_windows._limpar_confirmando(lambda _m: None) is True
    assert falso.tem("HKCU", CAMINHO), "confirmou, e o estado ficou"


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
    monkeypatch.setattr(
        cert_windows, "pedir_limpeza", lambda controle: cert_windows.limpar_autoselect()
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
    return ItemPendente(posicao=posicao, cnpj="11111111000191", certificado=cn)


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

def test_11_valor_externo_que_aparece_durante_a_execucao_sai_junto(
    maquina_real, monkeypatch
):
    """GUARDIAN_CLEANUP_DELETES_WHOLE_KEY.

    A validacao de startup protege o que existia ANTES da nossa mutacao. Depois
    que passamos a possuir a chave, `limpar_autoselect` continua sendo
    `DeleteKey` — e leva o que apareceu no meio do caminho.
    """
    ex = execucao(monkeypatch)
    ex.trocar_certificado(item(CN_A))

    # Software externo acrescenta uma regra a chave que agora e nossa.
    maquina_real.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = entrada(CN_B)

    ex.liberar_policy()

    assert not maquina_real.tem("HKCU", CAMINHO), "a regra externa foi junto"


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
