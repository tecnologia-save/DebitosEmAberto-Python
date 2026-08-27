"""O protocolo da policy, exercitado pela sua propria API.

O criterio de sucesso da fatia 7A: as tres primitivas do Windows entram por
parametro, entao o protocolo inteiro roda sem registro, sem UAC e sem processo
elevado. Estes testes rodariam identicos em Linux.

CNs ficticios.
"""
import pytest

from automation.policy_certificado import (
    ATIVADA,
    ELEVACAO_RECUSADA,
    INTERVALO_SONDAGEM_S,
    JA_ATIVA,
    NAO_APARECEU,
    SONDAGENS,
    ResultadoDaPolicy,
    garantir_policy,
)

CN_A = "ALFA FICTICIA LTDA:11111111000191"
CN_B = "BETA FICTICIA SA:22222222000172"


def _vivo(_controle):
    """O guardiao esta vivo — CHARACTERIZATION_TARGET_CHANGE da fatia 13A.4.

    O protocolo passou a exigir a vida do PROCESSO guardiao, e nao so a policy
    no registro. Estes testes sempre pressupuseram um guardiao vivo: nao havia
    outro estado possivel. Dize-lo explicitamente preserva exatamente o que cada
    assercao deste arquivo ja significava antes da fatia.
    """
    from automation.policy_certificado import GUARDIAO_VIVO

    return GUARDIAO_VIVO


class Maquina:
    """O estado do Windows, de mentira: o CN atual da policy e o que o UAC faz."""

    def __init__(self, cn_inicial="", elevacao=0, guardiao_escreve=True, demora=0):
        self.cn = cn_inicial
        self.elevacao = elevacao
        self.guardiao_escreve = guardiao_escreve
        self.demora = demora          # sondagens antes da policy aparecer
        self.lancamentos: list[str] = []
        self.esperas = 0

    def ler(self):
        return self.cn

    def lancar(self, cn):
        """CHARACTERIZATION_TARGET_CHANGE (fatia 12B.2): o lancamento devolve o
        CONTROLE do guardiao — ou `None` quando o UAC recusa. Antes era um `int`,
        e um inteiro nao permite PEDIR nada ao processo elevado depois."""
        self.lancamentos.append(cn)
        if self.elevacao == 0 and self.guardiao_escreve and self.demora == 0:
            self.cn = cn
        self._pendente = cn
        return _ControleFalso(cn) if self.elevacao == 0 else None

    def aguardar(self):
        self.esperas += 1
        if self.guardiao_escreve and self.demora and self.esperas >= self.demora:
            self.cn = self._pendente


class _ControleFalso:
    """Token opaco. O protocolo nunca o inspeciona — so o carrega."""

    def __init__(self, cn):
        self.cn = cn


def _decisao_de_antes(ler, cn):
    """A regra de startup ANTERIOR a fatia 12D, preservada aqui de proposito.

    O que este arquivo observa e o ciclo de vida do guardiao e da posse, e nao a
    validacao de estado preexistente — que tem os seus proprios testes. Manter a
    decisao antiga faz cada assercao abaixo continuar significando exatamente o
    que significava: "o CN visivel ja e o pedido" -> usa sem lancar ninguem.
    """
    from automation.policy_certificado import CRIAR, EMPRESTAR, DecisaoDeStartup

    return lambda: DecisaoDeStartup(EMPRESTAR if ler() == cn else CRIAR)


def pedir(maquina, cn=CN_A):
    return garantir_policy(cn, _decisao_de_antes(maquina.ler, cn),
                           maquina.lancar, maquina.aguardar, _vivo)


# ── Os quatro desfechos ───────────────────────────────────────────────────────

def test_policy_ativada_por_guardiao_desta_execucao():
    maquina = Maquina()

    resultado = pedir(maquina)

    assert resultado.situacao == ATIVADA
    assert resultado.confiavel is True
    assert resultado.sera_limpa is True
    assert maquina.lancamentos == [CN_A]


def test_policy_ja_ativa_nao_lanca_guardiao():
    """POLICY_STALE_OWNERSHIP_GAP, agora dizivel pelo tipo."""
    maquina = Maquina(cn_inicial=CN_A)

    resultado = pedir(maquina)

    assert resultado.situacao == JA_ATIVA
    assert resultado.confiavel is True, "o Chrome vai auto-selecionar"
    assert resultado.sera_limpa is False, "e ninguem desta execucao vai remover"
    assert maquina.lancamentos == []
    assert maquina.esperas == 0


def test_elevacao_recusada():
    maquina = Maquina(elevacao=-1)

    resultado = pedir(maquina)

    assert resultado.situacao == ELEVACAO_RECUSADA
    assert resultado.confiavel is False
    assert resultado.sera_limpa is False
    assert maquina.esperas == 0, "nao adianta sondar se nada foi lancado"


def test_guardiao_lancado_mas_policy_nunca_aparece():
    maquina = Maquina(guardiao_escreve=False)

    resultado = pedir(maquina)

    assert resultado.situacao == NAO_APARECEU
    assert resultado.confiavel is False
    assert resultado.sera_limpa is True, "o guardiao existe e vai limpar o que escreveu"
    assert maquina.esperas == SONDAGENS


# ── A propriedade de seguranca: esperar pelo CN, nao pela existencia ──────────

def test_policy_de_outro_cn_nao_conta_como_ja_ativa():
    """O ponto que torna seguro trocar de certificado no meio da execucao.

    Se bastasse a policy EXISTIR, isto devolveria True de imediato e o Chrome
    abriria com o certificado de outra empresa.
    """
    maquina = Maquina(cn_inicial=CN_B)

    resultado = pedir(maquina, CN_A)

    assert resultado.situacao == ATIVADA
    assert maquina.lancamentos == [CN_A], "lancou guardiao novo"


def test_guardiao_que_escreve_o_cn_errado_nao_e_aceito():
    maquina = Maquina(cn_inicial=CN_B, guardiao_escreve=False)

    resultado = pedir(maquina, CN_A)

    assert resultado.confiavel is False
    assert maquina.ler() == CN_B, "a policy errada continua la, e nao foi aceita"


# ── Sondagem ──────────────────────────────────────────────────────────────────

def test_a_policy_que_demora_a_aparecer_e_aceita():
    maquina = Maquina(demora=5)

    resultado = pedir(maquina)

    assert resultado.situacao == ATIVADA
    assert maquina.esperas == 5


def test_o_orcamento_de_sondagem_e_o_do_original():
    assert SONDAGENS == 60
    assert INTERVALO_SONDAGEM_S == 0.5


def test_a_sondagem_para_assim_que_encontra():
    maquina = Maquina(demora=1)

    pedir(maquina)

    assert maquina.esperas == 1, "nao gasta as 60"


# ── O resultado ───────────────────────────────────────────────────────────────

def test_o_resultado_e_imutavel():
    import dataclasses

    resultado = pedir(Maquina())
    with pytest.raises(dataclasses.FrozenInstanceError):
        resultado.situacao = "outra"


def test_confiavel_e_sera_limpa_sao_perguntas_diferentes():
    """Um bool nao consegue dizer as duas coisas — e e por isso que o resultado
    existe. As duas formas de chegar em "confiavel" tem limpezas opostas."""
    ja_ativa = ResultadoDaPolicy(JA_ATIVA, tem_guardiao=False)
    ativada = ResultadoDaPolicy(ATIVADA, tem_guardiao=True)

    assert ja_ativa.confiavel == ativada.confiavel == True   # noqa: E712
    assert ja_ativa.sera_limpa != ativada.sera_limpa


def test_nenhum_desfecho_carrega_o_cn():
    """As situacoes sao constantes: o CN nunca entra na mensagem."""
    for situacao in (JA_ATIVA, ATIVADA, ELEVACAO_RECUSADA, NAO_APARECEU):
        assert "ALFA" not in situacao
        assert "11111111000191" not in situacao


def test_o_protocolo_nao_imprime(capsys):
    pedir(Maquina())
    pedir(Maquina(elevacao=-1))
    pedir(Maquina(cn_inicial=CN_A))

    assert capsys.readouterr().out == ""


# ── A ponte no legado ─────────────────────────────────────────────────────────

def test_o_bool_legado_continua_derivando_do_resultado(monkeypatch):
    import cert_windows

    monkeypatch.setattr(
        cert_windows, "iniciar_guarda_detalhado",
        lambda cn: ResultadoDaPolicy(JA_ATIVA, tem_guardiao=False),
    )
    assert cert_windows.iniciar_guarda(CN_A) is True

    monkeypatch.setattr(
        cert_windows, "iniciar_guarda_detalhado",
        lambda cn: ResultadoDaPolicy(ELEVACAO_RECUSADA),
    )
    assert cert_windows.iniciar_guarda(CN_A) is False
