"""A fronteira do login, exercitada pela sua propria API.

Nenhum teste abre navegador, chama gov.br, usa certificado real ou chama Gemini:
o fork entra por parametro. Sentinelas ficticias.
"""
import dataclasses

import pytest

from automation.login import (
    AUTENTICADO,
    NAO_AUTENTICADO,
    Certificado,
    ConfigLogin,
    ConfiguracaoInvalida,
    ResultadoDoLogin,
    SessaoReceita,
    autenticar,
)

CN = "ALFA FICTICIA LTDA:11111111000191"
SERIAL = "0A01FICTICIO"
CHAVE = "AIzaSy-SENTINELA-FICTICIA-0000"
PERFIL = "C:/Clientes/ACME/perfil"

CERT = Certificado(subject_cn=CN, serial=SERIAL)
CONFIG = ConfigLogin(diretorio_perfil=PERFIL, gemini_api_key=CHAVE)


class Recurso:
    def __init__(self, nome):
        self.nome = nome
        self.chamadas = []

    def close(self):
        self.chamadas.append("close")

    def stop(self):
        self.chamadas.append("stop")


def trio():
    return Recurso("playwright"), Recurso("contexto"), Recurso("pagina")


def login_que_devolve(valor):
    def fazer_login(**kwargs):
        fazer_login.recebido = kwargs
        return valor
    fazer_login.recebido = None
    return fazer_login


# ── L · M · os dois desfechos ─────────────────────────────────────────────────

def test_l_autenticado_devolve_sessao():
    recursos = trio()

    resultado = autenticar(CERT, CONFIG, True, login_que_devolve(recursos))

    assert resultado.situacao == AUTENTICADO
    assert resultado.autenticado is True
    assert resultado.sessao.pagina is recursos[2]


def test_m_none_vira_nao_autenticado_sem_inventar_motivo():
    """LOGIN_OUTCOME_INFORMATION_LOSS: sao sete causas e um unico None. Criar
    CERTIFICADO_REJEITADO aqui seria fabricar precisao que o contrato nao tem."""
    resultado = autenticar(CERT, CONFIG, True, login_que_devolve(None))

    assert resultado.situacao == NAO_AUTENTICADO
    assert resultado.autenticado is False
    assert resultado.sessao is None


def test_m_os_desfechos_sao_um_conjunto_fechado_de_dois():
    assert {AUTENTICADO, NAO_AUTENTICADO} == {
        autenticar(CERT, CONFIG, True, login_que_devolve(trio())).situacao,
        autenticar(CERT, CONFIG, True, login_que_devolve(None)).situacao,
    }


# ── O que atravessa a fronteira ───────────────────────────────────────────────

def test_o_certificado_e_config_chegam_ao_fork():
    fork = login_que_devolve(trio())

    autenticar(CERT, CONFIG, True, fork)

    assert fork.recebido == {
        "cert_subject_cn": CN,
        "cert_serial": SERIAL,
        # Certificado instalado na maquina: nao ha arquivo a apresentar, e o
        # fork decide usar o Windows Store justamente por `cert_subject_cn`
        # estar preenchido.
        "cert_pfx_path": None,
        "cert_pfx_passphrase": None,
        "policy_ok": True,
        "project_dir": PERFIL,
        "gemini_api_key": CHAVE,
    }


def test_k_a_chave_vai_explicita_e_nao_pelo_ambiente(monkeypatch):
    """K da entrega: o caminho novo nao usa os.environ para o segredo."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    fork = login_que_devolve(trio())

    autenticar(CERT, CONFIG, True, fork)

    assert fork.recebido["gemini_api_key"] == CHAVE


def test_aa_o_login_so_recebe_se_pode_confiar_no_auto_select():
    """AA/AB: a policy fica fora. O login nao sabe quem limpa nem se ha guardiao."""
    fork = login_que_devolve(trio())

    autenticar(CERT, CONFIG, False, fork)

    assert fork.recebido["policy_ok"] is False
    assert set(fork.recebido) == {
        "cert_subject_cn", "cert_serial", "cert_pfx_path", "cert_pfx_passphrase",
        "policy_ok", "project_dir", "gemini_api_key"
    }


# ── Configuração ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("chave", ["", "   ", None])
def test_sem_chave_nada_e_tentado(chave):
    fork = login_que_devolve(trio())

    with pytest.raises(ConfiguracaoInvalida, match="Gemini"):
        autenticar(CERT, ConfigLogin(PERFIL, chave), True, fork)

    assert fork.recebido is None, "nem chegou a abrir o navegador"


@pytest.mark.parametrize("perfil", ["", "   ", None])
def test_sem_perfil_nada_e_tentado(perfil):
    fork = login_que_devolve(trio())

    with pytest.raises(ConfiguracaoInvalida, match="perfil"):
        autenticar(CERT, ConfigLogin(perfil, CHAVE), True, fork)

    assert fork.recebido is None


def test_a_config_nao_mostra_a_chave_no_repr():
    assert "SENTINELA" not in repr(CONFIG)
    assert PERFIL in repr(CONFIG), "o perfil nao e segredo, e ajuda a diagnosticar"


def test_o_repr_nao_torna_a_config_segura_para_serializacao():
    """`repr=False` e defesa adicional, nao garantia — como na fatia 6."""
    assert CHAVE in str(dataclasses.asdict(CONFIG))
    assert CONFIG.gemini_api_key == CHAVE


def test_a_mensagem_de_config_nunca_mostra_valor():
    for config in (ConfigLogin(PERFIL, ""), ConfigLogin("", CHAVE)):
        with pytest.raises(ConfiguracaoInvalida) as erro:
            config.validar()
        assert "SENTINELA" not in str(erro.value)
        assert "ACME" not in str(erro.value)


def test_o_certificado_e_dado_da_chamada_e_nao_config():
    """Ele muda a cada troca de certificado dentro da MESMA execucao."""
    assert "subject_cn" in {c.name for c in dataclasses.fields(Certificado)}
    assert "subject_cn" not in {c.name for c in dataclasses.fields(ConfigLogin)}


# ── P · ownership e cleanup ───────────────────────────────────────────────────

def test_p_a_fronteira_nao_fecha_o_que_devolveu():
    recursos = trio()

    resultado = autenticar(CERT, CONFIG, True, login_que_devolve(recursos))

    assert all(r.chamadas == [] for r in recursos), "quem chama e o dono"
    assert resultado.sessao.encerrada is False


def test_p_encerrar_fecha_o_contexto_e_depois_para_o_playwright():
    playwright, contexto, pagina = trio()
    sessao = SessaoReceita(playwright, contexto, pagina)

    sessao.encerrar()

    assert contexto.chamadas == ["close"]
    assert playwright.chamadas == ["stop"]
    assert sessao.encerrada is True


def test_p_encerrar_e_idempotente_sem_esconder_double_close():
    playwright, contexto, pagina = trio()
    sessao = SessaoReceita(playwright, contexto, pagina)

    sessao.encerrar()
    sessao.encerrar()
    sessao.encerrar()

    assert contexto.chamadas == ["close"], "uma vez so, e sem except largo"
    assert playwright.chamadas == ["stop"]


def test_p_o_repr_da_sessao_nao_expoe_nada_do_navegador():
    """`page.url` de uma sessao autenticada carrega identificadores."""
    class PaginaFalante:
        url = "https://servicos.receitafederal.gov.br/?token=SENTINELA-TOKEN"

        def __repr__(self):
            return f"Pagina({self.url})"

    sessao = SessaoReceita(Recurso("p"), Recurso("c"), PaginaFalante())

    assert repr(sessao) == "SessaoReceita(encerrada=False)"
    assert "SENTINELA" not in repr(sessao)
    assert "receitafederal" not in repr(sessao)


def test_p_o_resultado_e_imutavel():
    resultado = ResultadoDoLogin(NAO_AUTENTICADO)
    with pytest.raises(dataclasses.FrozenInstanceError):
        resultado.situacao = AUTENTICADO


# ── Bug nosso sobe ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("erro", [TypeError("bug"), AttributeError("bug"), ValueError("bug")])
def test_bug_nosso_nao_e_traduzido_em_nao_autenticado(erro):
    def fork(**kwargs):
        raise erro

    with pytest.raises(type(erro)):
        autenticar(CERT, CONFIG, True, fork)


def test_retorno_com_forma_errada_nao_e_disfarcado():
    """Se o fork devolver algo que nao e o trio, isso e bug — e sobe."""
    with pytest.raises((TypeError, ValueError)):
        autenticar(CERT, CONFIG, True, login_que_devolve(("so", "dois")))


def test_certificado_de_ARQUIVO_chega_ao_fork_sem_subject_cn():
    """A outra procedencia: o certificado veio como arquivo, e nao instalado.

    `cert_subject_cn` vazio e o que faz o fork NAO procurar no Windows Store —
    ele decide por `bool(cert_subject_cn)`. Com os dois preenchidos, a maquina
    escolheria um certificado instalado em vez do arquivo que recebemos.
    """
    fork = login_que_devolve(trio())
    do_arquivo = Certificado(subject_cn="", serial="",
                             pfx_path="C:/chao-da-execucao/0.pfx",
                             pfx_senha="senha-ficticia")

    autenticar(do_arquivo, CONFIG, True, fork)

    assert fork.recebido["cert_subject_cn"] == ""
    assert fork.recebido["cert_pfx_path"] == "C:/chao-da-execucao/0.pfx"
    assert fork.recebido["cert_pfx_passphrase"] == "senha-ficticia"


def test_a_senha_do_certificado_nao_aparece_no_repr():
    """Defesa ADICIONAL, e nao garantia: quem imprimir o campo direto continua
    imprimindo. A garantia e nao haver ponto que o imprima."""
    certificado = Certificado(subject_cn="", pfx_path="x.pfx",
                              pfx_senha="senha-ficticia-de-teste")

    assert "senha-ficticia-de-teste" not in repr(certificado)
