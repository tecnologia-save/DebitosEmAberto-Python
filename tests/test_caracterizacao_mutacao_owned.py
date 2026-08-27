"""O que a nossa escrita e a nossa limpeza fazem com estado que nao e nosso.

Escrito ANTES de qualquer mudanca da fatia 13A.

A fatia 12D fechou o que existia ANTES da nossa mutacao: `avaliar_estado_inicial`
recusa configuracao alheia em vez de sobrescreve-la. O que ela nao cobre e a
janela que se abre DEPOIS da decisao:

    decisao CRIAR  ──janela──>  guardiao elevado escreve  ──execucao──>  limpeza

Nessa janela o host lease nao protege nada. Ele exclui outras execucoes de
DebitosEmAberto; nao exclui administrador, GPO, Chrome management nem outra
ferramenta. Este arquivo prende o que acontece hoje quando alguem escreve na
mesma chave nesse intervalo.

Nenhum teste toca o registro real, pede UAC, lanca processo elevado ou abre
Chrome. Todos os CNs sao ficticios.
"""
import inspect
import json

import pytest
from registro_falso import RegistroFalso

import cert_windows

CN_A = "ALFA FICTICIA LTDA:11111111000191"
CN_B = "BETA FICTICIA SA:22222222000172"
CAMINHO = cert_windows.REG_PATH
QUANTAS = len(cert_windows.CERT_URLS)


@pytest.fixture
def registro(monkeypatch):
    falso = RegistroFalso()
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))
    return falso


def externo(marca="https://intranet.exemplo.invalido"):
    """Um valor que NAO e nosso: outra ferramenta, GPO, administrador."""
    return json.dumps({"pattern": marca, "filter": {"SUBJECT": {"CN": CN_B}}})


# ── §2 · o que `definir_autoselect` faz com o que ja esta la ──────────────────

def test_2_cria_a_chave_quando_ela_nao_existe(registro):
    cert_windows.definir_autoselect(CN_A)

    assert ("CreateKeyEx", "HKCU", CAMINHO) in registro.operacoes
    assert registro.tem("HKCU", CAMINHO)


def test_2_nao_enumera_nada_antes_de_escrever(registro):
    """Ele nao pergunta o que existe. Apaga por indice e escreve por cima."""
    fonte = inspect.getsource(cert_windows.definir_autoselect)

    assert "EnumValue" not in fonte
    assert "inventario_da_policy" not in fonte
    assert "DeleteValue" in fonte


def test_2_apaga_1_2_3_ate_o_primeiro_buraco(registro):
    """A limpeza previa e um laco por indice que para na primeira ausencia."""
    registro.dados["HKCU"][CAMINHO] = {"1": "x", "2": "y", "5": externo()}

    cert_windows.definir_autoselect(CN_A)

    apagados = [op for op in registro.operacoes if op[0] == "SetValueEx"]
    assert apagados, "escreveu"
    # "1" e "2" foram apagados; "3" falhou e interrompeu o laco.
    assert set(registro.valores("HKCU", CAMINHO)) == {
        str(i) for i in range(1, QUANTAS + 1)
    }


def test_2_set_value_sobrescreve_os_nomes_de_1_a_N(registro):
    """Mesmo o que o laco de apagar nao alcancou: `SetValueEx` escreve por cima
    de 1..N, e um valor externo nesse intervalo desaparece de qualquer jeito."""
    registro.dados["HKCU"][CAMINHO] = {"5": externo()}

    cert_windows.definir_autoselect(CN_A)

    lido = json.loads(registro.valores("HKCU", CAMINHO)["5"])
    assert lido["filter"]["SUBJECT"]["CN"] == CN_A, "o valor externo se foi"
    assert "intranet" not in json.dumps(registro.valores("HKCU", CAMINHO))


def test_2_valor_fora_do_intervalo_sobrevive(registro):
    """Nome acima de N: o laco para antes e o `SetValueEx` nao o alcanca."""
    registro.dados["HKCU"][CAMINHO] = {"99": externo()}

    cert_windows.definir_autoselect(CN_A)

    assert registro.valores("HKCU", CAMINHO)["99"] == externo()


def test_2_nome_nao_numerico_sobrevive(registro):
    registro.dados["HKCU"][CAMINHO] = {"RegraDaEmpresa": externo()}

    cert_windows.definir_autoselect(CN_A)

    assert "RegraDaEmpresa" in registro.valores("HKCU", CAMINHO)


def test_2_a_ordem_e_hkcu_e_depois_hklm(registro):
    cert_windows.definir_autoselect(CN_A)

    criacoes = [op for op in registro.operacoes if op[0] == "CreateKeyEx"]
    assert [op[1] for op in criacoes] == ["HKCU", "HKLM"]


def test_2_uma_colmeia_indisponivel_nao_interrompe_a_outra(monkeypatch):
    falso = RegistroFalso(protegidas=("HKLM",))
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))

    cert_windows.definir_autoselect(CN_A)

    assert falso.tem("HKCU", CAMINHO)
    assert not falso.tem("HKLM", CAMINHO)


# ── §4 · a janela entre a decisao e a escrita ─────────────────────────────────

def test_4_estado_externo_surgido_na_janela_e_SOBRESCRITO(registro):
    """POLICY_WRITE_TOCTOU_EXTERNAL_STATE_RISK.

    A decisao de startup viu o host vazio e disse CRIAR. Entre a decisao e a
    escrita — que acontece noutro processo, depois de uma elevacao de UAC —
    alguem instalou uma regra no indice 1. A nossa escrita a apaga sem olhar.
    """
    registro.dados["HKCU"][CAMINHO] = {"1": externo()}

    cert_windows.definir_autoselect(CN_A)

    lido = json.loads(registro.valores("HKCU", CAMINHO)["1"])
    assert lido["filter"]["SUBJECT"]["CN"] == CN_A


def test_4_e_o_guardiao_nao_revalida_nada_antes_de_escrever(registro):
    """A autorizacao vem da decisao do processo principal, tomada antes da
    elevacao. Entre ela e esta linha nao ha nenhuma segunda leitura."""
    fonte = inspect.getsource(cert_windows.guardiao)
    antes = fonte[: fonte.index("definir_autoselect(cn)")]

    assert "avaliar_estado_inicial" not in antes
    assert "inventario_da_policy" not in antes


def test_4_a_janela_e_real_porque_a_escrita_mora_noutro_processo(registro):
    """Nao e uma janela teorica de microssegundos: entre a decisao e a escrita
    ha um `ShellExecuteExW` com verbo runas, um prompt de UAC e a subida de um
    processo novo."""
    fonte = inspect.getsource(cert_windows._lancar_guardiao)

    assert "_runas(" in fonte
    assert "--guard" in fonte


# ── §3 · o que a limpeza faz com o que nao e nosso ────────────────────────────

def test_3_a_limpeza_apaga_a_chave_inteira(registro):
    cert_windows.definir_autoselect(CN_A)

    cert_windows.limpar_autoselect()

    assert not registro.tem("HKCU", CAMINHO)
    assert ("DeleteKey", "HKCU", CAMINHO) in registro.operacoes


def test_3_e_leva_junto_o_valor_externo(registro):
    """GUARDIAN_CLEANUP_DELETES_WHOLE_KEY, medido: a escrita PRESERVA o valor
    fora do intervalo, e a limpeza o remove."""
    cert_windows.definir_autoselect(CN_A)
    registro.dados["HKCU"][CAMINHO]["99"] = externo()

    cert_windows.limpar_autoselect()

    assert not registro.tem("HKCU", CAMINHO), "a regra alheia foi junto"


def test_3_a_limpeza_nao_compara_nada(registro):
    """Nao ha leitura antes da remocao: `DeleteKey` e incondicional."""
    fonte = inspect.getsource(cert_windows.limpar_autoselect)

    assert "DeleteKey" in fonte
    assert "DeleteValue" not in fonte
    assert "QueryValueEx" not in fonte and "EnumValue" not in fonte


def test_3_o_guardiao_usa_essa_limpeza_no_caminho_normal_e_no_de_crash(registro):
    fonte = inspect.getsource(cert_windows)
    helper = fonte[fonte.index("def _limpar_confirmando"):fonte.index("def guardiao(")]

    assert "limpar_autoselect()" in helper
    assert "_limpar_confirmando(_log)" in inspect.getsource(cert_windows.guardiao)


# ── §12 · a confirmacao de hoje nao distingue nosso de alheio ─────────────────

def test_12_a_confirmacao_pergunta_por_estado_QUALQUER(registro):
    """Depois da 12D.1 ela e forte — e forte demais para o lifecycle OWNED:
    um valor externo que decidissemos preservar prenderia o host para sempre."""
    registro.dados["HKCU"][CAMINHO] = {"RegraDaEmpresa": externo()}

    assert cert_windows.policy_existe() is True, "e nada disso e nosso"


def test_12_nao_existe_nenhuma_nocao_de_valor_OWNED_hoje(registro):
    fonte = inspect.getsource(cert_windows)

    # Marcadores de CHAMADA, e nao a palavra solta: "owned" ja aparece numa
    # frase de log da fatia 12C, e assertiva de texto que bate em prosa e o
    # erro que este projeto ja cometeu vezes demais.
    for marca in ("policy_owned_existe(", "remover_autoselect_owned(",
                  "_valores_esperados("):
        assert marca not in fonte, f"nao ha {marca} ainda"


def test_12_e_o_controle_do_guardiao_nao_carrega_com_o_que_comparar(registro):
    """`ControleDoGuardiao` guarda o canal, e nada que permita reconstruir o
    estado que aquele guardiao escreveu."""
    assert cert_windows.ControleDoGuardiao.__slots__ == ("evento", "nome")
