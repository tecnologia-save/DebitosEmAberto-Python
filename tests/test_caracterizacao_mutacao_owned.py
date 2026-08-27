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


def test_2_agora_ele_CONFERE_antes_de_escrever(registro):
    """ANTES: nao perguntava o que existia — apagava por indice e escrevia por
    cima. AGORA le cada nome nosso antes de tocar em qualquer um."""
    fonte = inspect.getsource(cert_windows.definir_autoselect)

    assert "DeleteValue" not in fonte, "nenhuma remocao no caminho da escrita"
    assert "if _conflita(key, esperados):" in fonte
    assert "is _AUSENTE" in fonte, "so preenche o que falta"


def test_2_o_estado_owned_e_deterministico_e_dispensa_guardar_copia(registro):
    """E o que torna a comparacao possivel sem inventar marcador de dono: dado o
    CN, os valores saem sempre iguais."""
    assert cert_windows._valores_esperados(CN_A) == cert_windows._valores_esperados(CN_A)
    assert cert_windows._valores_esperados(CN_A) != cert_windows._valores_esperados(CN_B)
    assert set(cert_windows._valores_esperados(CN_A)) == {
        str(i) for i in range(1, QUANTAS + 1)
    }


def test_2_nome_ocupado_por_conteudo_alheio_faz_a_colmeia_inteira_ser_deixada(
    registro,
):
    """ANTES: `SetValueEx` escrevia por cima de 1..N e o valor externo sumia.

    AGORA a colmeia sai inteira — e sai inteira, e nao valor a valor, para nao
    deixar meia policy instalada: metade das URLs no nosso CN e metade no de
    outra pessoa seria pior do que nenhuma.
    """
    registro.dados["HKCU"][CAMINHO] = {"5": externo()}
    registro.protegidas = {"HKLM"}

    assert cert_windows.definir_autoselect(CN_A) is False

    assert registro.valores("HKCU", CAMINHO) == {"5": externo()}, "nada mudou"


def test_2_a_outra_colmeia_ainda_e_escrita(registro):
    """Conflito e por colmeia. HKLM limpo continua recebendo a nossa policy."""
    registro.dados["HKCU"][CAMINHO] = {"5": externo()}

    assert cert_windows.definir_autoselect(CN_A) is True

    assert registro.valores("HKCU", CAMINHO) == {"5": externo()}
    assert len(registro.valores("HKLM", CAMINHO)) == QUANTAS


def test_2_valor_nosso_que_ja_esta_certo_nao_e_reescrito(registro):
    """Idempotente: escrever duas vezes o mesmo CN nao produz escrita nenhuma na
    segunda."""
    cert_windows.definir_autoselect(CN_A)
    registro.operacoes.clear()

    assert cert_windows.definir_autoselect(CN_A) is True

    assert not [op for op in registro.operacoes if op[0] == "SetValueEx"]


# ── §4 · a janela entre a decisao e a escrita ─────────────────────────────────

def test_4_estado_externo_surgido_na_janela_NAO_e_mais_sobrescrito(registro):
    """POLICY_WRITE_TOCTOU_EXTERNAL_STATE_RISK, fechado no lado da escrita.

    ANTES: a decisao de startup via o host vazio e dizia CRIAR; entre a decisao e
    a escrita — noutro processo, depois de um prompt de UAC — alguem instalava
    uma regra no indice 1, e a nossa escrita a apagava sem olhar.

    AGORA a escrita confere, encontra conteudo que nao e o nosso, e nao escreve.
    """
    registro.dados["HKCU"][CAMINHO] = {"1": externo()}

    cert_windows.definir_autoselect(CN_A)

    assert registro.valores("HKCU", CAMINHO)["1"] == externo()


def test_4_e_o_guardiao_REVALIDA_antes_de_escrever(registro):
    """ANTES: a autorizacao vinha da decisao do processo principal, tomada antes
    da elevacao, e entre ela e a escrita nao havia nenhuma segunda leitura.

    AGORA a revalidacao acontece ja elevado, a um passo da escrita: quem decide
    se ainda e seguro escrever e quem esta prestes a escrever.
    """
    fonte = inspect.getsource(cert_windows.guardiao)
    antes = fonte[: fonte.index("definir_autoselect(cn)")]

    assert "avaliar_estado_inicial(" in antes
    assert "inventario_da_policy()" in antes
    assert "abortando sem escrever" in antes


def test_4_sao_duas_camadas_e_a_segunda_e_a_garantia(registro):
    """A revalidacao encurta a janela; a escrita nao destrutiva a fecha. Mesmo
    que alguem escreva entre as duas, nada nosso passa por cima."""
    fonte = inspect.getsource(cert_windows.definir_autoselect)

    assert "if _conflita(key, esperados):" in fonte
    assert "DeleteValue" not in fonte


def test_4_a_janela_continua_existindo_e_fica_registrada(registro):
    """Entre `_conflita` e o `SetValueEx` ainda ha um intervalo. Ele deixou de
    ser um prompt de UAC e passou a ser um punhado de chamadas de registro —
    menor, e nao inexistente."""
    fonte = inspect.getsource(cert_windows.definir_autoselect)

    assert "janela" in fonte.lower(), "o docstring nomeia o que resta"


# ── §9 · §10 · §15 · a limpeza por comparacao ────────────────────────────────

def test_9_remove_o_que_e_nosso(registro):
    cert_windows.definir_autoselect(CN_A)

    cert_windows.remover_autoselect_owned(CN_A)

    assert registro.valores("HKCU", CAMINHO) == {}
    assert cert_windows.policy_owned_existe(CN_A) is False


def test_9_nunca_chama_DeleteKey(registro):
    """GUARDIAN_CLEANUP_DELETES_WHOLE_KEY: a chamada saiu do caminho owned."""
    cert_windows.definir_autoselect(CN_A)
    registro.operacoes.clear()

    cert_windows.remover_autoselect_owned(CN_A)

    assert not [op for op in registro.operacoes if op[0] == "DeleteKey"]
    # Marcador de CHAMADA: a propria docstring da funcao diz "nunca DeleteKey".
    fonte = inspect.getsource(cert_windows.remover_autoselect_owned)
    assert "winreg.DeleteKey(" not in fonte


def test_10_valor_extra_de_outra_origem_e_preservado(registro):
    """Nos escrevemos 1..N; alguem acrescentou outro. Ele fica."""
    cert_windows.definir_autoselect(CN_A)
    registro.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = externo()

    cert_windows.remover_autoselect_owned(CN_A)

    assert registro.valores("HKCU", CAMINHO) == {"RegraDaEmpresa": externo()}


def test_15_nome_nosso_sobrescrito_por_terceiro_e_preservado(registro):
    """Escrevemos `1`; alguem trocou o conteudo. O nosso ja nao esta la, e a
    posse daquele nome terminou na sobrescrita. Restaurar o nosso seria destruir
    o de outra pessoa."""
    cert_windows.definir_autoselect(CN_A)
    registro.dados["HKCU"][CAMINHO]["1"] = externo()

    cert_windows.remover_autoselect_owned(CN_A)

    assert registro.valores("HKCU", CAMINHO) == {"1": externo()}


def test_11_a_chave_pode_ficar_vazia_e_fica(registro):
    """Conferir que esta vazia e so entao apaga-la abriria uma corrida nova, por
    um ganho cosmetico. E a decisao de startup ja trata chave vazia como host
    limpo."""
    from automation import policy_certificado

    cert_windows.definir_autoselect(CN_A)
    cert_windows.remover_autoselect_owned(CN_A)

    assert registro.valores("HKCU", CAMINHO) == {}, "sem valores"
    assert CAMINHO in registro.dados["HKCU"], "e a chave continua existindo"
    assert policy_certificado.avaliar_estado_inicial(
        cert_windows.inventario_da_policy(), CN_A, tuple(cert_windows.CERT_URLS)
    ).decisao == policy_certificado.CRIAR


# ── §12 · §14 · §18 · a confirmacao owned ─────────────────────────────────────

def test_12_estado_externo_NAO_conta_como_estado_nosso(registro):
    """A distincao que a fatia inteira existe para fazer."""
    registro.dados["HKCU"][CAMINHO] = {"RegraDaEmpresa": externo()}

    assert cert_windows.policy_existe() is True, "ha estado no host"
    assert cert_windows.policy_owned_existe(CN_A) is False, "e nao e nosso"


def test_12_estado_nosso_conta(registro):
    cert_windows.definir_autoselect(CN_A)

    assert cert_windows.policy_owned_existe(CN_A) is True
    assert cert_windows.policy_owned_existe(CN_B) is False, "de outro CN, nao"


def test_12_colmeia_ilegivel_continua_fail_closed(registro, monkeypatch):
    """UNREADABLE_HIVE_BLOCKS_HOST_RELEASE nao foi resolvido aqui: ignorancia
    sobre uma colmeia pode esconder estado nosso."""
    def negar(colmeia, caminho, reservado, acesso):
        if colmeia == "HKLM":
            raise PermissionError("acesso negado")
        raise FileNotFoundError(caminho)

    monkeypatch.setattr(registro, "OpenKeyEx", negar)

    assert cert_windows.policy_owned_existe(CN_A) is True


def test_12_o_controle_do_guardiao_carrega_o_CN_e_nao_o_mostra(registro):
    """ANTES: `ControleDoGuardiao` guardava so o canal, e nada permitia
    reconstruir o estado escrito por aquele guardiao.

    AGORA carrega o CN — que e tudo o que a comparacao precisa, e menos do que
    guardar copia dos payloads. Fora do `repr`, porque um CN identifica a
    empresa. DEFESA ADICIONAL, nao garantia: acesso ao atributo continua expondo,
    e por isso so `cert_windows` o le.
    """
    controle = cert_windows.ControleDoGuardiao(evento=1, nome="canal", cn=CN_A)

    assert controle.cn == CN_A
    assert CN_A not in repr(controle)
    assert "11111111000191" not in repr(controle)


def test_14_a_confirmacao_ignora_o_externo_e_enxerga_o_nosso(registro):
    """§14: CLEANUP_CONFIRMED quando nenhum valor ainda existente e nosso — e
    nao quando a chave sumiu, nem quando nao sobrou nada de ninguem."""
    cert_windows.definir_autoselect(CN_A)
    registro.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = externo()

    assert cert_windows._limpar_confirmando(CN_A, lambda _m: None) is True
    assert registro.valores("HKCU", CAMINHO) == {"RegraDaEmpresa": externo()}


def test_18_fail_closed_nao_significa_bloquear_por_estado_alheio(registro):
    """A distincao obrigatoria do §18: nao liberar enquanto o NOSSO permanece;
    e nao ficar preso porque preservamos o de outra pessoa."""
    registro.dados["HKCU"][CAMINHO] = {"RegraDaEmpresa": externo()}

    assert cert_windows._limpar_confirmando(CN_A, lambda _m: None) is True


def test_17_o_crash_path_usa_a_MESMA_regra(registro):
    """Nao ha uma limpeza para o caminho normal e outra para o crash: os dois
    passam por `_limpar_confirmando`, e ela e a nao destrutiva."""
    guarda = inspect.getsource(cert_windows.guardiao)

    assert guarda.count("_limpar_confirmando(cn, _log)") == 2
    assert "limpar_autoselect()" not in guarda, "o DeleteKey saiu do guardiao"


def test_26_o_clean_manual_continua_apagando_tudo(registro):
    """`limpar_autoselect` nao foi tocada: ela e a ferramenta do operador, e la
    quem manda apagar a chave inteira e uma pessoa."""
    cert_windows.definir_autoselect(CN_A)
    registro.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = externo()

    cert_windows.limpar_autoselect()

    assert not registro.tem("HKCU", CAMINHO)
