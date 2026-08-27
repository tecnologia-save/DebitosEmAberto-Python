"""A decisao de startup da policy: CRIAR, EMPRESTAR ou RECUSAR.

`avaliar_estado_inicial` e pura — recebe o estado das colmeias como dado e nao
toca em registro nenhum. `inventario_da_policy` e a primitiva que produz esse
dado, e aqui ela roda sobre `RegistroFalso`.

Nenhum teste escreve no registro real, pede UAC, lanca processo elevado ou abre
Chrome. Todos os CNs sao ficticios.
"""
import inspect
import json

import pytest
from registro_falso import RegistroFalso

import cert_windows
from automation import policy_certificado
from automation.policy_certificado import (
    COLMEIA_ILEGIVEL,
    COLMEIAS_DIVERGENTES,
    CONTEUDO_NAO_RECONHECIDO,
    CRIAR,
    EMPRESTAR,
    OUTRO_CERTIFICADO,
    RECUSAR,
    REGRAS_ADICIONAIS,
    ColmeiaDaPolicy,
    ConfiguracaoDeHostIncompativel,
    RegraDaPolicy,
    avaliar_estado_inicial,
)

CN_NOSSO = "ALFA FICTICIA LTDA:11111111000191"
CN_ALHEIO = "BETA FICTICIA SA:22222222000172"
PADROES = ("https://um.exemplo.invalido", "https://dois.exemplo.invalido")
CAMINHO = cert_windows.REG_PATH


def colmeia(rotulo, cn, padroes=PADROES, **extra):
    """Uma colmeia com exatamente as regras que escreveriamos para `cn`."""
    regras = tuple(
        RegraDaPolicy(str(i), padrao=padrao, cn=cn)
        for i, padrao in enumerate(padroes, 1)
    )
    return ColmeiaDaPolicy(rotulo, existe=True, regras=regras, **extra)


def decidir(*colmeias, cn=CN_NOSSO):
    return avaliar_estado_inicial(tuple(colmeias), cn, PADROES)


# ── As seis categorias de estado inicial ──────────────────────────────────────

def test_vazio_autoriza_criar():
    """EMPTY. Nada instalado: a automacao instala, possui e limpa."""
    resultado = decidir(ColmeiaDaPolicy("HKCU"), ColmeiaDaPolicy("HKLM"))

    assert resultado.decisao == CRIAR
    assert resultado.motivo == ""


def test_chave_existente_sem_regras_tambem_autoriza_criar():
    """Sem regras o Chrome nao seleciona nada, e a nossa propria escrita cria a
    chave antes de preenche-la."""
    assert decidir(ColmeiaDaPolicy("HKCU", existe=True)).decisao == CRIAR


def test_coerente_com_o_certificado_pedido_autoriza_emprestar():
    """CONSISTENT_EXPECTED. Ja e exatamente o que precisamos."""
    resultado = decidir(colmeia("HKCU", CN_NOSSO), colmeia("HKLM", CN_NOSSO))

    assert resultado.decisao == EMPRESTAR


def test_coerente_para_OUTRO_certificado_recusa():
    """CONSISTENT_OTHER. Completo, coerente — e de outra pessoa."""
    resultado = decidir(colmeia("HKCU", CN_ALHEIO))

    assert resultado.decisao == RECUSAR
    assert resultado.motivo == OUTRO_CERTIFICADO


def test_uma_colmeia_so_nao_e_estado_parcial():
    """PARTIAL so tem sentido entre colmeias que EXISTEM. Uma ausente e o caso
    normal de quem nao conseguiu elevacao, e nao um conflito."""
    resultado = decidir(colmeia("HKCU", CN_NOSSO), ColmeiaDaPolicy("HKLM"))

    assert resultado.decisao == EMPRESTAR


def test_colmeias_que_discordam_recusam():
    """DIVERGENT. Uma serve e a outra nao."""
    resultado = decidir(colmeia("HKCU", CN_NOSSO), colmeia("HKLM", CN_ALHEIO))

    assert resultado.decisao == RECUSAR
    assert resultado.motivo == COLMEIAS_DIVERGENTES


def test_as_duas_erradas_do_MESMO_jeito_nao_sao_divergentes():
    """Coerentes entre si e incompativeis com o pedido: o motivo e o
    certificado, e nao a divergencia."""
    resultado = decidir(colmeia("HKCU", CN_ALHEIO), colmeia("HKLM", CN_ALHEIO))

    assert resultado.motivo == OUTRO_CERTIFICADO


def test_payload_desconhecido_recusa():
    """MALFORMED_OR_UNKNOWN."""
    ilegivel = ColmeiaDaPolicy(
        "HKCU", existe=True,
        regras=tuple(RegraDaPolicy(str(i), reconhecida=False) for i in (1, 2)),
    )

    assert decidir(ilegivel).motivo == CONTEUDO_NAO_RECONHECIDO


def test_padrao_diferente_com_o_cn_certo_tambem_e_desconhecido():
    """Mesmo CN, outras URLs: nao e a nossa configuracao, e nao sabemos o que
    ela cobre."""
    torta = ColmeiaDaPolicy("HKCU", existe=True, regras=(
        RegraDaPolicy("1", padrao="https://outra.coisa.invalido", cn=CN_NOSSO),
        RegraDaPolicy("2", padrao=PADROES[1], cn=CN_NOSSO),
    ))

    assert decidir(torta).motivo == CONTEUDO_NAO_RECONHECIDO


def test_regra_a_mais_recusa():
    base = colmeia("HKCU", CN_NOSSO)
    com_extra = ColmeiaDaPolicy(
        "HKCU", existe=True,
        regras=(*base.regras, RegraDaPolicy("99", padrao="https://x", cn=CN_ALHEIO)),
    )

    assert decidir(com_extra).motivo == REGRAS_ADICIONAIS


def test_regra_a_MENOS_e_conteudo_nao_reconhecido_e_nao_regra_adicional():
    """Faltar regra nossa nao e "ter regras a mais" — sao coisas diferentes, e o
    motivo que vai para o operador precisa dizer a certa."""
    incompleta = ColmeiaDaPolicy("HKCU", existe=True, regras=(
        RegraDaPolicy("1", padrao=PADROES[0], cn=CN_NOSSO),
    ))

    assert decidir(incompleta).motivo == CONTEUDO_NAO_RECONHECIDO


def test_colmeia_ilegivel_recusa_antes_de_qualquer_outra_coisa():
    """Ignorancia nao e ausencia. Uma colmeia que existe e nao pode ser lida
    recusa mesmo que a outra esteja perfeita."""
    resultado = decidir(
        colmeia("HKCU", CN_NOSSO),
        ColmeiaDaPolicy("HKLM", existe=True, legivel=False),
    )

    assert resultado.decisao == RECUSAR
    assert resultado.motivo == COLMEIA_ILEGIVEL


# ── Independencia de ordem: a precedencia do Chrome nao entra na decisao ──────

@pytest.mark.parametrize("primeiro, segundo", [
    (CN_NOSSO, CN_ALHEIO),
    (CN_ALHEIO, CN_NOSSO),
])
def test_a_ordem_das_colmeias_nao_muda_a_decisao(primeiro, segundo):
    resultado = decidir(colmeia("HKCU", primeiro), colmeia("HKLM", segundo))

    assert resultado.decisao == RECUSAR
    assert resultado.motivo == COLMEIAS_DIVERGENTES


def test_nenhuma_colmeia_e_privilegiada_por_nome():
    """A funcao recebe rotulos como dado e nunca decide por eles."""
    fonte = inspect.getsource(avaliar_estado_inicial)
    fonte += inspect.getsource(policy_certificado._verdicto)
    corpo = "\n".join(
        linha for linha in fonte.splitlines() if not linha.lstrip().startswith("#")
    )

    assert '"HKCU"' not in corpo and '"HKLM"' not in corpo


# ── O inventario: a leitura completa das duas colmeias ────────────────────────

@pytest.fixture
def registro(monkeypatch):
    falso = RegistroFalso()
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))
    return falso


def bruto(cn, url):
    return json.dumps({"pattern": url, "filter": {"SUBJECT": {"CN": cn}}})


def test_inventario_devolve_as_duas_colmeias_sempre(registro):
    colmeias = cert_windows.inventario_da_policy()

    assert [c.rotulo for c in colmeias] == ["HKCU", "HKLM"]
    assert all(c.existe is False for c in colmeias)


def test_inventario_enumera_todos_os_valores_inclusive_os_que_nao_sao_nossos(
    registro,
):
    """E a diferenca em relacao a `_ler_cn`, que le so o valor "1"."""
    registro.dados["HKCU"][CAMINHO] = {
        "1": bruto(CN_NOSSO, "https://a.invalido"),
        "7": bruto(CN_ALHEIO, "https://b.invalido"),
        "RegraDaEmpresa": bruto(CN_ALHEIO, "https://c.invalido"),
    }

    hkcu = cert_windows.inventario_da_policy()[0]

    assert {r.nome for r in hkcu.regras} == {"1", "7", "RegraDaEmpresa"}


def test_inventario_marca_o_ilegivel_em_vez_de_chutar(registro):
    registro.dados["HKCU"][CAMINHO] = {"1": "isto nao e json"}

    regra = cert_windows.inventario_da_policy()[0].regras[0]

    assert regra.reconhecida is False
    assert regra.cn == "" and regra.padrao == "", "nao inventa conteudo"


def test_inventario_recusa_json_com_campos_a_mais(registro):
    """Um payload que tem o nosso formato E algo mais nao e o nosso formato."""
    registro.dados["HKCU"][CAMINHO] = {
        "1": json.dumps({"pattern": "https://a", "filter": {"SUBJECT": {"CN": CN_NOSSO}},
                         "outra_coisa": True})
    }

    assert cert_windows.inventario_da_policy()[0].regras[0].reconhecida is False


def test_colmeia_ausente_e_diferente_de_colmeia_ilegivel(registro, monkeypatch):
    """FileNotFoundError e ausencia; qualquer outro OSError e ignorancia."""
    def negar(colmeia_, caminho, reservado, acesso):
        if colmeia_ == "HKLM":
            raise PermissionError("acesso negado")
        raise FileNotFoundError(caminho)

    monkeypatch.setattr(registro, "OpenKeyEx", negar)

    hkcu, hklm = cert_windows.inventario_da_policy()

    assert hkcu.existe is False and hkcu.legivel is True
    assert hklm.existe is True and hklm.legivel is False


# ── A fronteira de mutacao ────────────────────────────────────────────────────

def test_a_avaliacao_vem_antes_do_unico_ponto_que_escreve():
    """§19. `lancar_guardiao` e a unica coisa em `garantir_policy` que produz
    escrita no registro, e nao ha caminho ate ela que pule a avaliacao."""
    fonte = inspect.getsource(policy_certificado.garantir_policy)

    assert fonte.count("lancar_guardiao(") == 1
    assert fonte.index("avaliar_inicio()") < fonte.index("lancar_guardiao(")


def test_o_lease_de_host_vem_antes_da_avaliacao_nos_tres_entrypoints():
    """§19, a ordem completa: adapter -> lease -> app -> avaliacao -> escrita.

    A avaliacao acontece dentro de `app.executar`; os entrypoints so precisam
    garantir que o lease foi adquirido antes dela.
    """
    from pathlib import Path

    raiz = Path(cert_windows.__file__).parent
    for arquivo in ("runner.py", "local.py", "main.py"):
        fonte = (raiz / arquivo).read_text(encoding="utf-8-sig")
        assert fonte.index("exclusividade_host.adquirir()") < fonte.index(
            "app.executar("
        ), f"{arquivo} avalia policy sem possuir o host"


def test_recusar_nunca_chega_a_escrever():
    escritas = []

    with pytest.raises(ConfiguracaoDeHostIncompativel):
        policy_certificado.garantir_policy(
            CN_NOSSO,
            avaliar_inicio=lambda: decidir(colmeia("HKCU", CN_ALHEIO)),
            lancar_guardiao=lambda cn: escritas.append(cn),
            aguardar=lambda: None,
        )

    assert escritas == []


def test_emprestar_nunca_chega_a_escrever():
    escritas = []

    resultado = policy_certificado.garantir_policy(
        CN_NOSSO,
        avaliar_inicio=lambda: decidir(colmeia("HKCU", CN_NOSSO)),
        lancar_guardiao=lambda cn: escritas.append(cn),
        aguardar=lambda: None,
    )

    assert escritas == []
    assert resultado.sera_limpa is False, "e nada sera removido no fim"


def test_nao_ha_mais_porta_para_pular_a_avaliacao():
    """ANTES (12D): `policy_ja_e_nossa=True` pulava a avaliacao inteira, porque
    a posse dentro da execucao autorizava sobrescrever a policy anterior.

    AGORA (13A) a escrita nao sobrescreve nada, entao nao ha o que autorizar. A
    avaliacao acontece SEMPRE, e e a unica porta para `lancar_guardiao`.
    """
    avaliacoes = []
    instalado = []

    def avaliar():
        avaliacoes.append(1)
        # Antes do guardiao, host limpo; depois dele, a policy no lugar. E a
        # mesma pergunta respondendo ao mundo, e nao dois predicados.
        if instalado:
            return decidir(colmeia("HKCU", CN_NOSSO))
        return decidir()

    resultado = policy_certificado.garantir_policy(
        CN_NOSSO,
        avaliar_inicio=avaliar,
        lancar_guardiao=lambda cn: instalado.append(cn) or object(),
        aguardar=lambda: None,
    )

    # Uma na decisao e as demais na confirmacao: desde a 13A.1 e a mesma
    # pergunta que abre e fecha o protocolo.
    assert len(avaliacoes) >= 2, "avaliou, e nao ha argumento que evite isso"
    assert resultado.situacao == policy_certificado.ATIVADA


# ── O erro que o operador ve ──────────────────────────────────────────────────

SENTINELAS = {
    "cn": CN_NOSSO,
    "cn alheio": CN_ALHEIO,
    "caminho de registro": CAMINHO,
    "colmeia": "HKLM",
    "chave do registro": "AutoSelectCertificateForUrls",
}


@pytest.mark.parametrize("motivo", [
    OUTRO_CERTIFICADO, COLMEIAS_DIVERGENTES, CONTEUDO_NAO_RECONHECIDO,
    REGRAS_ADICIONAIS, COLMEIA_ILEGIVEL,
])
def test_a_mensagem_nao_carrega_nada_que_identifique(motivo):
    """§21. Nem CN, nem caminho de registro, nem usuario, nem PID."""
    texto = str(ConfiguracaoDeHostIncompativel(motivo))

    for rotulo, sentinela in SENTINELAS.items():
        assert sentinela not in texto, f"vazou {rotulo}"
    assert "11111111000191" not in texto and "22222222000172" not in texto


def test_a_mensagem_diz_o_que_esta_errado_e_o_que_fazer():
    texto = str(ConfiguracaoDeHostIncompativel(OUTRO_CERTIFICADO))

    assert "seleção automática de certificado" in texto
    assert "host" in texto
    assert OUTRO_CERTIFICADO in texto, "o motivo entra, e ele nao identifica ninguem"


def test_os_motivos_sao_um_vocabulario_fechado_sem_interpolacao():
    """Nenhum motivo e montado com dado de execucao: sao literais do modulo."""
    fonte = inspect.getsource(policy_certificado)
    trecho = fonte[
        fonte.index("OUTRO_CERTIFICADO ="):fonte.index("class ConfiguracaoDeHost")
    ]

    assert "{" not in trecho and "%" not in trecho and "+" not in trecho


def test_o_motivo_fica_acessivel_sem_precisar_ler_a_frase():
    """Classificar por texto de excecao e proibido desde a fatia 6A. Quem
    precisar do motivo le o atributo."""
    erro = ConfiguracaoDeHostIncompativel(COLMEIAS_DIVERGENTES)

    assert erro.motivo == COLMEIAS_DIVERGENTES


def test_o_cn_nao_aparece_no_repr_da_regra():
    """DEFESA ADICIONAL, nao garantia: `asdict`, acesso direto ao atributo e log
    manual continuam expondo. O que isto impede e o vazamento por acidente."""
    regra = RegraDaPolicy("1", padrao="https://a.invalido", cn=CN_NOSSO)

    assert CN_NOSSO not in repr(regra)
    assert CN_NOSSO not in repr(ColmeiaDaPolicy("HKCU", existe=True, regras=(regra,)))
    assert regra.cn == CN_NOSSO, "e continua la para quem precisa dele"


# ── Os entrypoints terminam em falha ──────────────────────────────────────────

def test_local_e_main_tratam_a_recusa_como_falha_e_nao_como_aviso():
    """§22. Codigo de saida proprio, e distinto do da exclusividade de host."""
    from pathlib import Path

    raiz = Path(cert_windows.__file__).parent
    for arquivo, saida in (("local.py", "return 4"), ("main.py", "sys.exit(4)")):
        fonte = (raiz / arquivo).read_text(encoding="utf-8-sig")
        trecho = fonte[fonte.index("ConfiguracaoDeHostIncompativel as erro"):]

        assert saida in trecho[:400], f"{arquivo} nao termina em falha"


def test_o_runner_deixa_a_recusa_subir_para_a_plataforma():
    """Ele nao traduz: quem chama e a plataforma, e ela precisa ver o erro —
    exatamente como ja acontece com a exclusividade de host."""
    from pathlib import Path

    fonte = (Path(cert_windows.__file__).parent / "runner.py").read_text(
        encoding="utf-8"
    )

    assert "ConfiguracaoDeHostIncompativel" not in fonte
    assert "except" not in fonte[fonte.index("app.executar("):]
