"""O que sobra do cleanup destrutivo, e o que ele estava escondendo.

Escrito ANTES de qualquer mudanca da fatia 13A.3.

Nenhum teste toca o registro real, pede UAC, lanca processo elevado ou abre
Chrome. Todos os CNs sao ficticios.

O dispatch de `--guard` vive dentro de `if __name__ == "__main__":`, entao o que
se caracteriza dele aqui e a FONTE. O que acontece dentro de `guardiao` e
caracterizado por comportamento, com `winreg` substituido.
"""
import inspect
import json
from pathlib import Path

import pytest
from registro_falso import RegistroFalso

import cert_windows
from automation import policy_certificado

RAIZ = Path(__file__).resolve().parent.parent
CN_NOSSO = "ALFA FICTICIA LTDA:11111111000191"
CN_ALHEIO = "BETA FICTICIA SA:22222222000172"
CAMINHO = cert_windows.REG_PATH
QUANTAS = len(cert_windows.CERT_URLS)


class Kernel32Falso:
    def __init__(self, handle=1):
        self.handle = handle
        self.fechados = []

    def OpenProcess(self, acesso, herdar, pid):
        return self.handle

    def OpenEventW(self, acesso, herdar, nome):
        return 0

    def WaitForSingleObject(self, h, ms):
        return 0

    def WaitForMultipleObjects(self, n, alvos, todos, ms):
        return 0

    def CloseHandle(self, h):
        self.fechados.append(h)
        return True


@pytest.fixture
def guardiao_isolado(monkeypatch):
    """`guardiao` com o Windows inteiro substituido."""
    falso = RegistroFalso()
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))
    monkeypatch.setattr(cert_windows, "_k32", Kernel32Falso())
    monkeypatch.setattr(cert_windows.exclusividade_host, "anexar", lambda: 99)
    monkeypatch.setattr(cert_windows.time, "sleep", lambda _s: None)
    return falso


def externo(cn=CN_ALHEIO):
    return json.dumps({"pattern": "https://intranet.exemplo.invalido",
                       "filter": {"SUBJECT": {"CN": cn}}})


# ── §1 · o escopo real do `except` do dispatch ───────────────────────────────

def _bloco_do_dispatch():
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    return fonte[fonte.index('if __name__ == "__main__":'):]


def test_1_o_try_cobre_o_parsing_E_a_chamada_do_guardiao():
    """Nao e um `except` em volta so da decodificacao: a chamada inteira de
    `guardiao` esta dentro dele."""
    bloco = _bloco_do_dispatch()
    trecho = bloco[:bloco.index("sys.exit(0)")]

    assert "int(sys.argv[2])" in trecho
    assert "base64.b64decode(sys.argv[3])" in trecho
    assert "guardiao(" in trecho
    assert trecho.index("try:") < trecho.index("guardiao(")


def test_1_e_o_except_chama_a_limpeza_DESTRUTIVA():
    """GUARD_DISPATCH_DESTRUCTIVE_CLEANUP: `limpar_autoselect` e `DeleteKey` da
    chave inteira, e nao a remocao por comparacao da fatia 13A."""
    bloco = _bloco_do_dispatch()
    trecho = bloco[:bloco.index("sys.exit(0)")]

    assert "limpar_autoselect()" in trecho
    assert "remover_autoselect_owned" not in trecho
    assert "winreg.DeleteKey" in inspect.getsource(cert_windows.limpar_autoselect)


def test_1_e_e_o_UNICO_deletekey_automatico_que_sobrou():
    """Dentro de `guardiao` a fatia 13A ja o tinha tirado. O `--clean` manual
    continua fora de escopo: la quem manda apagar tudo e uma pessoa."""
    assert "limpar_autoselect()" not in inspect.getsource(cert_windows.guardiao)

    bloco = _bloco_do_dispatch()
    assert bloco.count("limpar_autoselect()") == 2, "o dispatch e o --clean"


# ── §2 · o DeleteKey residual, reproduzido ───────────────────────────────────

def test_2_a_limpeza_do_dispatch_leva_o_valor_externo_junto(guardiao_isolado):
    """A prova do finding: a nossa policy instalada, um valor externo ao lado, e
    a limpeza do dispatch remove os dois."""
    cert_windows.definir_autoselect(CN_NOSSO)
    guardiao_isolado.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = externo()

    cert_windows.limpar_autoselect()

    assert not guardiao_isolado.tem("HKCU", CAMINHO), "a chave inteira saiu"


def test_2_enquanto_a_remocao_owned_o_preservaria(guardiao_isolado):
    """O contraste com o que a fatia 13A instalou no resto do ciclo de vida."""
    cert_windows.definir_autoselect(CN_NOSSO)
    guardiao_isolado.dados["HKCU"][CAMINHO]["RegraDaEmpresa"] = externo()

    cert_windows.remover_autoselect_owned(CN_NOSSO)

    assert guardiao_isolado.valores("HKCU", CAMINHO) == {"RegraDaEmpresa": externo()}


# ── §4 · §5 · falhas ANTES de haver posse ────────────────────────────────────

def test_5_falha_no_attach_do_lease_nao_escreve_nada(guardiao_isolado,
                                                     monkeypatch):
    """Fase A. Se `anexar` levantar, a excecao sobe ao dispatch — e nesse
    momento nao ha mutacao nossa nenhuma."""
    guardiao_isolado.dados["HKCU"][CAMINHO] = {"1": externo()}
    monkeypatch.setattr(cert_windows.exclusividade_host, "anexar",
                        lambda: (_ for _ in ()).throw(OSError("sem lease")))

    with pytest.raises(OSError):
        cert_windows.guardiao(4242, CN_NOSSO)

    assert guardiao_isolado.valores("HKCU", CAMINHO) == {"1": externo()}


def test_5_e_a_limpeza_do_dispatch_apagaria_esse_estado_externo(guardiao_isolado):
    """E aqui esta o dano: o dispatch nao sabe que nada foi escrito, e apaga
    assim mesmo. Estado que nunca foi nosso, removido por uma falha nossa."""
    guardiao_isolado.dados["HKCU"][CAMINHO] = {"1": externo()}

    cert_windows.limpar_autoselect()

    assert not guardiao_isolado.tem("HKCU", CAMINHO)


def test_5_falha_na_revalidacao_tambem_e_pre_posse(guardiao_isolado, monkeypatch):
    """Fase C. A revalidacao roda antes da escrita."""
    fonte = inspect.getsource(cert_windows.guardiao)
    antes_da_escrita = fonte[: fonte.index("definir_autoselect(cn)")]

    assert "avaliar_estado_inicial(" in antes_da_escrita
    assert "definir_autoselect" not in antes_da_escrita.replace(
        "definir_autoselect(cn)", ""
    )


# ── §11 · excecao DURANTE a escrita ──────────────────────────────────────────

def test_11_excecao_no_meio_da_escrita_deixa_estado_owned_parcial(
    guardiao_isolado, monkeypatch
):
    """O que a 13A.1 nao cobre.

    A compensacao dela roda quando `definir_autoselect` DECIDE que o estado
    final nao e coerente. Uma excecao Python inesperada no meio da escrita pula
    a compensacao inteira: a funcao nem chega ao passo 3.
    """
    original = guardiao_isolado.SetValueEx
    escritas = {"n": 0}

    def falhar_no_terceiro(chave, nome, reservado, tipo, valor):
        escritas["n"] += 1
        if escritas["n"] > 3:
            raise MemoryError("falha inesperada no meio da escrita")
        return original(chave, nome, reservado, tipo, valor)

    monkeypatch.setattr(guardiao_isolado, "SetValueEx", falhar_no_terceiro)

    with pytest.raises(MemoryError):
        cert_windows.definir_autoselect(CN_NOSSO)

    assert len(guardiao_isolado.valores("HKCU", CAMINHO)) == 3, "meia policy"
    assert cert_windows.policy_owned_existe(CN_NOSSO) is True


def test_11_e_o_guardiao_engole_essa_excecao_e_vai_embora(
    guardiao_isolado, monkeypatch
):
    """`except Exception: pass` faz `escrita` continuar NAO_INSTALADA, e o
    guardiao devolve os handles e sai — deixando estado nosso instalado e sem
    dono."""
    def explodir(cn):
        raise MemoryError("falha inesperada")

    monkeypatch.setattr(cert_windows, "definir_autoselect", explodir)
    guardiao_isolado.dados["HKCU"][CAMINHO] = dict(
        list(cert_windows._valores_esperados(CN_NOSSO).items())[:3]
    )

    cert_windows.guardiao(4242, CN_NOSSO)

    assert cert_windows.policy_owned_existe(CN_NOSSO) is True, "sobrou nosso"
    fonte = inspect.getsource(cert_windows.guardiao)
    assert "except Exception:  # noqa: BLE001" in fonte


# ── §6 · §7 · a policy orfa ──────────────────────────────────────────────────

def test_6_uma_excecao_POS_escrita_sobe_ao_dispatch(guardiao_isolado,
                                                    monkeypatch):
    """Fase E. O `finally` de `guardiao` fecha handles — e nao limpa a policy.
    A excecao segue para o dispatch com a policy JA instalada."""
    fonte = inspect.getsource(cert_windows.guardiao)
    trecho = fonte[fonte.index("finally:"):]

    assert "CloseHandle" in trecho
    assert "limpar" not in trecho, "o finally nao limpa policy"

    def explodir(n, alvos, todos, ms):
        raise MemoryError("falha inesperada na espera")

    k32 = Kernel32Falso()
    k32.WaitForSingleObject = lambda h, ms: (_ for _ in ()).throw(
        MemoryError("falha inesperada na espera")
    )
    monkeypatch.setattr(cert_windows, "_k32", k32)

    with pytest.raises(MemoryError):
        cert_windows.guardiao(4242, CN_NOSSO)

    assert cert_windows.policy_owned_existe(CN_NOSSO) is True


def test_7_o_parent_aceita_essa_policy_como_ATIVADA(guardiao_isolado):
    """GUARDIAN_FAILURE_ORPHANED_POLICY_ACCEPTANCE_RISK.

    O guardiao escreveu e morreu. O processo principal rele o estado, encontra
    exatamente a policy que pediu, e conclui ATIVADA — com `tem_guardiao=True`
    para um guardiao que ja nao existe.

    A aparicao da policy prova que ele PASSOU pelo attach. Nao prova que ele
    continua vivo, e o protocolo nao tem outro sinal.
    """
    def lancar_e_morrer(cn):
        cert_windows.definir_autoselect(cn)
        return object()          # o controle de um processo que ja morreu

    resultado = policy_certificado.garantir_policy(
        CN_NOSSO,
        avaliar_inicio=lambda: policy_certificado.avaliar_estado_inicial(
            cert_windows.inventario_da_policy(), CN_NOSSO,
            tuple(cert_windows.CERT_URLS)
        ),
        lancar_guardiao=lancar_e_morrer,
        aguardar=lambda: None,
    )

    assert resultado.situacao == policy_certificado.ATIVADA
    assert resultado.tem_guardiao is True
    assert resultado.confiavel is True, "e o login recebe 'pode confiar'"


def test_7_e_hoje_quem_impede_isso_e_o_DELETEKEY(guardiao_isolado):
    """§8, medido: o desenho atual depende do efeito colateral destrutivo.

    Com a limpeza do dispatch, a policy some antes de o parent a observar, o
    polling estoura e o desfecho vira NAO_APARECEU. Isso NAO e confirmacao de
    ciclo de vida — e um `DeleteKey` sendo usado como prova de liveness.
    """
    cert_windows.definir_autoselect(CN_NOSSO)
    assert cert_windows.policy_owned_existe(CN_NOSSO) is True

    cert_windows.limpar_autoselect()          # o que o dispatch faz hoje

    assert cert_windows.policy_owned_existe(CN_NOSSO) is False


def test_7_mas_a_limpeza_do_dispatch_engole_falhas_por_colmeia(monkeypatch):
    """E nem sequer e confiavel nisso: `limpar_autoselect` engole o erro de cada
    colmeia. Sem privilegio em HKLM, a policy fica — e a orfandade acontece
    mesmo hoje."""
    falso = RegistroFalso(protegidas=("HKLM",))
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))
    falso.dados["HKLM"][CAMINHO] = dict(cert_windows._valores_esperados(CN_NOSSO))

    cert_windows.limpar_autoselect()

    assert cert_windows.policy_owned_existe(CN_NOSSO) is True, "sobrou, em silencio"


# ── §13 · o lease do host nao e afetado por isso ─────────────────────────────

def test_13_o_parent_mantem_o_proprio_lease_mesmo_com_o_guardiao_morto():
    """CONCURRENCY SAFETY e GUARDIAN LIFECYCLE SAFETY sao coisas diferentes.

    A morte do guardiao nao devolve o host enquanto o parent vive — isso protege
    contra uma SEGUNDA execucao. E nao impede a execucao ATUAL de abrir o
    navegador com uma policy orfa.
    """
    from automation import exclusividade_host

    # Comportamental: `liberar` fecha UM handle — o desta parte da execucao — e
    # nao tem como alcancar o do guardiao. Vale o inverso tambem: o guardiao
    # morrer nao fecha o do parent.
    fechados = []
    controle = exclusividade_host.ControleDaExclusividade(handle=7)

    exclusividade_host.liberar(controle, fechar=fechados.append)

    assert fechados == [7], "so o proprio"


# ── §16 · o `--clean` manual fica fora ───────────────────────────────────────

def test_16_o_clean_manual_continua_intocado():
    bloco = _bloco_do_dispatch()
    trecho = bloco[bloco.index('sys.argv[1] == "--clean"'):]

    assert "limpar_autoselect()" in trecho[:200]
