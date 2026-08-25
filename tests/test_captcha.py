"""A fronteira do captcha, exercitada pela sua propria API.

Nenhum teste chama Gemini, abre navegador ou usa chave real. A fronteira externa
inteira e um callable — e esse e o criterio de sucesso da fatia.

Todas as sentinelas sao ficticias e escolhidas para serem reconheciveis se
vazarem para uma mensagem.
"""
import dataclasses

import pytest
from patchright.sync_api import Error as ErroDoNavegador

from automation.captcha import (
    FALHA_EXTERNA,
    NAO_RESOLVIDO,
    RESOLVIDO_OU_AUSENTE,
    ConfigCaptcha,
    ConfiguracaoInvalida,
    resolver,
)

CHAVE = "AIzaSy-SENTINELA-FICTICIA-NAO-E-CHAVE-0000"
CONFIG = ConfigCaptcha(api_key=CHAVE)
PAGINA = object()   # a fronteira nunca toca no alvo; ela so o repassa


def sempre(valor):
    def resolver_bruto(alvo):
        return valor
    return resolver_bruto


def contando(respostas):
    """Devolve uma resposta por tentativa, e registra quantas houve."""
    chamadas = []

    def resolver_bruto(alvo):
        chamadas.append(alvo)
        resposta = respostas[len(chamadas) - 1]
        if isinstance(resposta, BaseException):
            raise resposta
        return resposta

    resolver_bruto.chamadas = chamadas
    return resolver_bruto


# ── Os tres desfechos ─────────────────────────────────────────────────────────

def test_resolvido_na_primeira_tentativa():
    bruto = contando([True, True])

    assert resolver(PAGINA, CONFIG, resolver_bruto=bruto) == RESOLVIDO_OU_AUSENTE
    assert len(bruto.chamadas) == 1, "sucesso encerra o retry"


def test_resolvido_na_segunda():
    bruto = contando([False, True])

    assert resolver(PAGINA, CONFIG, resolver_bruto=bruto) == RESOLVIDO_OU_AUSENTE
    assert len(bruto.chamadas) == 2


def test_nao_resolvido_depois_das_tentativas():
    bruto = contando([False, False])

    assert resolver(PAGINA, CONFIG, resolver_bruto=bruto) == NAO_RESOLVIDO
    assert len(bruto.chamadas) == 2


@pytest.mark.parametrize("erro", [
    RuntimeError("Gemini grade: falhou em todos os modelos. Ultimo erro: SENTINELA"),
    ErroDoNavegador("Timeout 30000ms exceeded"),
])
def test_falha_externa_e_desfecho_e_nao_exception(erro):
    """O chamador de hoje trata isto como "nao resolveu" — transformar em
    exception mudaria o fluxo sem que ninguem tivesse pedido. A distincao fica
    no resultado."""
    bruto = contando([erro, erro])

    assert resolver(PAGINA, CONFIG, resolver_bruto=bruto) == FALHA_EXTERNA
    assert len(bruto.chamadas) == 2, "falha externa NAO encurta o retry"


def test_falha_na_primeira_e_sucesso_na_segunda():
    """Falha transitoria do Gemini e exatamente o caso que o retry existe para
    cobrir — e por isso RuntimeError nao pode significar "desista"."""
    bruto = contando([RuntimeError("falhou em todos os modelos"), True])

    assert resolver(PAGINA, CONFIG, resolver_bruto=bruto) == RESOLVIDO_OU_AUSENTE


def test_o_alvo_e_repassado_intacto():
    bruto = contando([True])
    alvo = object()

    resolver(alvo, CONFIG, resolver_bruto=bruto)

    assert bruto.chamadas == [alvo]


# ── Bug nosso sobe ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("erro", [TypeError("bug nosso"), AttributeError("bug nosso"),
                                  KeyError("bug nosso"), ValueError("bug nosso")])
def test_bug_nosso_nao_e_traduzido(erro):
    bruto = contando([erro, erro])

    with pytest.raises(type(erro)):
        resolver(PAGINA, CONFIG, resolver_bruto=bruto)

    assert len(bruto.chamadas) == 1, "nem sequer gasta a segunda tentativa"


# ── O retry e o custo ─────────────────────────────────────────────────────────

def test_o_numero_de_tentativas_e_explicito():
    bruto = contando([False] * 5)

    resolver(PAGINA, CONFIG, tentativas=5, resolver_bruto=bruto)

    assert len(bruto.chamadas) == 5


def test_a_espera_acontece_entre_tentativas_e_nao_depois_da_ultima():
    """Cada tentativa custa chamadas ao Gemini; esperar depois da ultima seria
    tempo jogado fora."""
    esperas = []
    bruto = contando([False, False, False])

    resolver(PAGINA, CONFIG, tentativas=3, resolver_bruto=bruto,
             aguardar=lambda: esperas.append(1))

    assert len(esperas) == 2, "n-1 esperas para n tentativas"


def test_sucesso_nao_espera():
    esperas = []

    resolver(PAGINA, CONFIG, resolver_bruto=sempre(True),
             aguardar=lambda: esperas.append(1))

    assert esperas == []


def test_sem_aguardar_o_retry_e_imediato():
    bruto = contando([False, False])

    assert resolver(PAGINA, CONFIG, resolver_bruto=bruto) == NAO_RESOLVIDO


# ── Config: o segredo ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("valor", ["", "   ", None])
def test_config_sem_chave_e_recusada_antes_de_qualquer_tentativa(valor):
    bruto = contando([True])

    with pytest.raises(ConfiguracaoInvalida):
        resolver(PAGINA, ConfigCaptcha(api_key=valor), resolver_bruto=bruto)

    assert bruto.chamadas == [], "nada foi tentado, nada foi gasto"


def test_config_com_o_texto_de_exemplo_e_recusada():
    with pytest.raises(ConfiguracaoInvalida, match="exemplo"):
        ConfigCaptcha(api_key="cole-sua-chave-aqui").validar()


def test_a_chave_com_espacos_em_volta_e_aceita():
    """Diferente do fork, que nao faz strip e deixa passar uma chave só de espaços."""
    ConfigCaptcha(api_key=f"  {CHAVE}  ").validar()


def test_a_mensagem_de_config_nunca_mostra_o_valor():
    for valor in ["cole-" + CHAVE, "", "   "]:
        with pytest.raises(ConfiguracaoInvalida) as erro:
            ConfigCaptcha(api_key=valor).validar()
        assert "SENTINELA" not in str(erro.value)


def test_o_repr_da_config_nao_mostra_a_chave():
    """`repr=False` e DEFESA ADICIONAL, nao garantia — os dois testes seguintes
    mostram exatamente o que ele NAO cobre."""
    assert "SENTINELA" not in repr(CONFIG)
    assert "api_key" not in repr(CONFIG)


def test_o_repr_nao_torna_o_objeto_seguro_para_serializacao():
    """O que `repr=False` nao resolve, dito por teste para ninguem confiar demais."""
    assert CHAVE in str(dataclasses.asdict(CONFIG))
    assert CONFIG.api_key == CHAVE


def test_a_config_e_imutavel():
    with pytest.raises(dataclasses.FrozenInstanceError):
        CONFIG.api_key = "outra"


def test_nao_ha_chave_padrao():
    """Nenhum default: quem constroi a config tem de dizer de onde a chave veio."""
    with pytest.raises(TypeError):
        ConfigCaptcha()


# ── Mensagens seguras nos desfechos ───────────────────────────────────────────

def test_nenhum_desfecho_carrega_conteudo_externo():
    """Sentinelas plantadas na exception do fork e do navegador não podem
    reaparecer em nada que a fronteira devolva."""
    erro = RuntimeError(
        "Gemini grade: falhou. Ultimo erro: 401 chave AIzaSy-SENTINELA "
        "https://portal.exemplo/autenticado?token=SENTINELA-TOKEN CNPJ 11111111000191"
    )
    desfecho = resolver(PAGINA, CONFIG, resolver_bruto=contando([erro, erro]))

    for proibido in ["SENTINELA", "AIzaSy", "token", "11111111000191", "https://"]:
        assert proibido not in desfecho


def test_os_desfechos_sao_um_conjunto_fechado():
    assert {RESOLVIDO_OU_AUSENTE, NAO_RESOLVIDO, FALHA_EXTERNA} == {
        resolver(PAGINA, CONFIG, resolver_bruto=sempre(True)),
        resolver(PAGINA, CONFIG, resolver_bruto=sempre(False)),
        resolver(PAGINA, CONFIG, resolver_bruto=contando([ErroDoNavegador("x")] * 2)),
    }


# ── A ponte TRANSITIONAL ──────────────────────────────────────────────────────

def test_a_ponte_do_main_monta_a_config_a_partir_do_ambiente(monkeypatch):
    import main

    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)
    capturado = {}

    def espiao(alvo, config, tentativas=2, aguardar=None, resolver_bruto=None):
        capturado["chave"] = config.api_key
        capturado["tentativas"] = tentativas
        return RESOLVIDO_OU_AUSENTE

    monkeypatch.setattr(main.captcha, "resolver", espiao)

    assert main._resolver_captcha(PAGINA) == RESOLVIDO_OU_AUSENTE
    assert capturado == {"chave": CHAVE, "tentativas": 2}


def test_a_ponte_esta_marcada_e_tem_condicao_de_remocao():
    import main

    doc = main._resolver_captcha.__doc__
    assert "TRANSITIONAL" in doc
    assert "CAPTCHA_INTEGRATION_COUPLING" in doc
    assert "Condição de remoção" in doc
