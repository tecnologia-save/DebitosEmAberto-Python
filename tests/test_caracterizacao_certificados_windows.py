"""Caracterizacao da descoberta de certificados COMO ELA E HOJE.

Escrito ANTES de extrair. Nenhum teste consulta o Certificate Store, executa
PowerShell, toca o registro ou pede UAC: a UNICA fronteira substituida e o
`subprocess.run`, e no lugar dele entram saidas sinteticas equivalentes as reais.

Corpus 100% ficticio — nenhum CN, empresa, CPF/CNPJ, serial ou thumbprint real.
"""
import base64
import json
import subprocess

import pytest

from automation import certificados_windows
from automation.domain import buscar_certificado

# ── Corpus ficticio ───────────────────────────────────────────────────────────

ALFA = {
    "display": "ALFA FICTICIA LTDA",
    "subject_cn": "ALFA FICTICIA LTDA:11111111000191",
    "thumbprint": "AAAA1111",
    "serial": "0A01",
    "notafter": "2030-01-01",
}
BETA = {
    "display": "BETA COMÉRCIO FICTÍCIO",
    "subject_cn": "BETA COMÉRCIO FICTÍCIO:22222222000172",
    "thumbprint": "BBBB2222",
    "serial": "0B02",
    "notafter": "2030-02-02",
}
PESSOA = {
    "display": "FULANO DE TAL FICTICIO",
    "subject_cn": "FULANO DE TAL FICTICIO:11122233344",
    "thumbprint": "CCCC3333",
    "serial": "0C03",
    "notafter": "2030-03-03",
}
# CN em GUID: e assim que os certificados de sistema do Windows aparecem.
SISTEMA = {
    "display": "Microsoft Ficticio",
    "subject_cn": "00000000-0000-0000-0000-000000000000",
    "thumbprint": "DDDD4444",
    "serial": "0D04",
    "notafter": "2030-04-04",
}


class Saida:
    """O que `subprocess.run` devolve, no minimo que o codigo consome."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def powershell(monkeypatch):
    """Substitui SO a fronteira externa e guarda como ela foi chamada."""
    chamadas = []

    def fingir(*args, **kwargs):
        chamadas.append((args, kwargs))
        resultado = fingir.resposta
        if isinstance(resultado, BaseException):
            raise resultado
        return resultado

    fingir.resposta = Saida()
    fingir.chamadas = chamadas
    monkeypatch.setattr(subprocess, "run", fingir)
    return fingir


def responder(powershell, certificados):
    """JSON como o `ConvertTo-Json -Compress` produz: objeto se for um so."""
    dados = certificados[0] if len(certificados) == 1 else certificados
    powershell.resposta = Saida(stdout=json.dumps(dados))


# ── CHARACTERIZATION_TARGET_CHANGE (fatia 9B) ─────────────────────────────────
# `main._listar_certs_windows` e `main.carregar_certificados` eram adapters do
# laco legado: chamavam a integracao, transformavam falha conhecida em lista
# vazia e imprimiam. O laco foi para automation/app.py, e a decisao de tratar a
# falha subiu para la — onde ela vira um EVENTO, e nao um print.
#
# Os helpers abaixo exercitam o mesmo caminho pela integracao. Onde o adapter
# devolvia `[]` e imprimia, os testes passam a afirmar sobre a EXCEPTION que a
# fatia 5A ja levantava e sobre a frase que o adapter de apresentacao produz —
# as mesmas garantias, sem intermediario.

def listar_certs():
    return certificados_windows.interpretar_saida(
        certificados_windows.executar_powershell(certificados_windows.COMANDO_POWERSHELL)
    )


def carregar_certificados():
    return certificados_windows.descobrir()[0]


# ── E · o comando PowerShell ──────────────────────────────────────────────────

def test_e_o_comando_e_constante_e_nao_interpola_nada(powershell):
    """SECURITY: nenhum dado da planilha, do usuario ou do ambiente entra no
    comando. Ele e literal no codigo e nao muda entre execucoes."""
    responder(powershell, [ALFA])
    listar_certs()
    listar_certs()

    (args1, _), (args2, _) = powershell.chamadas
    assert args1 == args2, "o comando nao varia"

    argv = args1[0]
    assert argv[0] == "powershell"
    assert "-NoProfile" in argv and "-NonInteractive" in argv


def test_e_o_comando_vai_como_lista_e_nao_por_shell(powershell):
    responder(powershell, [ALFA])
    listar_certs()

    (args, kwargs) = powershell.chamadas[0]
    assert isinstance(args[0], list), "argv em lista, nao string"
    assert kwargs.get("shell") in (None, False), "sem shell=True"


def test_f_o_store_consultado_e_o_do_usuario_atual(powershell):
    """Decodifica o -EncodedCommand para ver o que realmente e executado."""
    responder(powershell, [ALFA])
    listar_certs()

    argv = powershell.chamadas[0][0][0]
    script = base64.b64decode(argv[argv.index("-EncodedCommand") + 1]).decode("utf-16-le")

    assert "Cert:\\CurrentUser\\My" in script
    assert "HasPrivateKey" in script
    assert "-not $_.Archived" in script
    assert "NotBefore" in script and "NotAfter" in script


def test_g_os_campos_pedidos_ao_powershell(powershell):
    responder(powershell, [ALFA])
    listar_certs()

    argv = powershell.chamadas[0][0][0]
    script = base64.b64decode(argv[argv.index("-EncodedCommand") + 1]).decode("utf-16-le")

    for campo in ("display", "subject_cn", "thumbprint", "serial", "notafter"):
        assert campo in script


def test_e_o_timeout_e_a_janela_escondida(powershell):
    responder(powershell, [ALFA])
    listar_certs()

    kwargs = powershell.chamadas[0][1]
    assert kwargs["timeout"] == 30
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"


# ── H · parsing da saida ──────────────────────────────────────────────────────

def test_h_zero_certificados(powershell):
    powershell.resposta = Saida(stdout="")
    assert listar_certs() == []


@pytest.mark.parametrize("stdout", ["", "   ", "\n\n"])
def test_h_saida_em_branco(powershell, stdout):
    powershell.resposta = Saida(stdout=stdout)
    assert listar_certs() == []


def test_h_um_certificado_vem_como_objeto_e_vira_lista(powershell):
    """`ConvertTo-Json` de um item so devolve OBJETO, nao array. O codigo embrulha."""
    powershell.resposta = Saida(stdout=json.dumps(ALFA))

    assert listar_certs() == [ALFA]


def test_h_varios_certificados(powershell):
    responder(powershell, [ALFA, BETA, PESSOA])
    assert listar_certs() == [ALFA, BETA, PESSOA]


def test_h_a_ordem_do_powershell_e_preservada(powershell):
    responder(powershell, [BETA, ALFA])
    assert [c["serial"] for c in listar_certs()] == ["0B02", "0A01"]


def test_h_saida_que_nao_e_json(powershell):
    """CERT_WINDOWS_POSSIBLE_DEFECT: erro de parsing NAO vira lista vazia.

    Era `[]` mais um print — indistinguivel de "nao ha certificado instalado".
    Desde a 5A e uma exception, e desde a 9B o app a traduz num evento proprio.
    """
    powershell.resposta = Saida(stdout="isto nao e json")

    with pytest.raises(certificados_windows.FalhaAoLerCertificados, match="ilegível"):
        listar_certs()


def test_h_codigo_de_saida_nao_zero_e_ignorado(powershell):
    """CERT_WINDOWS_POSSIBLE_DEFECT: o returncode nunca e consultado. Se houver
    JSON no stdout, ele e aceito mesmo com o PowerShell reportando erro."""
    powershell.resposta = Saida(stdout=json.dumps(ALFA), stderr="erro qualquer", returncode=1)

    assert listar_certs() == [ALFA]


# Os quatro testes abaixo afirmavam o `except Exception` ate o commit d8c4881.
# A fatia 5A o estreitou de proposito — MIGRATION_SEMANTIC_CHANGE exigido pela
# regra "falha externa conhecida vira erro nosso e seguro; bug inesperado sobe".
# O comportamento antigo continua legivel no historico.

def test_h_powershell_ausente(powershell):
    """ANTES: imprimia "FileNotFoundError". DEPOIS: mensagem nossa e acionavel."""
    powershell.resposta = FileNotFoundError("powershell nao encontrado")

    with pytest.raises(certificados_windows.FalhaAoLerCertificados,
                       match="PowerShell não foi encontrado"):
        listar_certs()


def test_h_timeout(powershell):
    """ANTES: imprimia "TimeoutExpired"."""
    powershell.resposta = subprocess.TimeoutExpired(cmd="powershell", timeout=30)

    with pytest.raises(certificados_windows.FalhaAoLerCertificados, match="demorou demais"):
        listar_certs()


def test_h_bug_nosso_sobe_em_vez_de_virar_lista_vazia(powershell):
    """A correcao do defeito mais grave da fatia.

    ANTES: o `except Exception` engolia TUDO, inclusive um bug nosso, e devolvia
    `[]` — indistinguivel de "nao ha certificado instalado". O `main` entao
    mandava o operador instalar um certificado e encerrava com sys.exit(1).
    Uma falha de leitura e um repositorio vazio produziam a MESMA saida.

    DEPOIS: falha externa conhecida vira lista vazia com diagnostico; bug nosso
    sobe e aparece como o que e.
    """
    powershell.resposta = TypeError("bug nosso, nao falha do Windows")

    with pytest.raises(TypeError):
        listar_certs()


def test_h_o_diagnostico_nao_ecoa_mais_a_excecao_crua(powershell, capsys):
    """ANTES: `{e}` ia inteiro para o console e podia trazer saida do PowerShell,
    CN ou serial. DEPOIS: so mensagens constantes nossas."""
    powershell.resposta = Saida(stdout="SENTINELA-CN-ALFA:11111111000191 nao e json")

    with pytest.raises(certificados_windows.FalhaAoLerCertificados) as erro:
        listar_certs()

    assert "SENTINELA-CN-ALFA" not in str(erro.value)
    assert "11111111000191" not in str(erro.value)
    assert capsys.readouterr().out == "", "a integração não imprime"


# ── I · indexacao e deduplicacao ──────────────────────────────────────────────

def test_i_chaves_por_certificado(powershell):
    """display, CN completo e CN sem o documento — a planilha pode trazer qualquer
    um. Aqui display e o CN sem documento coincidem, entao sobram duas chaves."""
    responder(powershell, [ALFA])
    mapa = carregar_certificados()

    assert set(mapa) == {
        "alfa ficticia ltda",
        "alfa ficticia ltda:11111111000191",
    }
    unicos = {id(v) for v in mapa.values()}
    assert len(unicos) == 1, "todas as chaves apontam para o MESMO certificado"


def test_i_display_diferente_do_cn_gera_chave_a_mais(powershell):
    cert = dict(ALFA, display="APELIDO DA ALFA")
    responder(powershell, [cert])
    mapa = carregar_certificados()

    assert "apelido da alfa" in mapa
    assert "alfa ficticia ltda" in mapa
    assert "alfa ficticia ltda:11111111000191" in mapa


def test_i_acentos_sao_removidos_da_chave(powershell):
    responder(powershell, [BETA])
    mapa = carregar_certificados()

    assert "beta comercio ficticio" in mapa
    assert "beta comércio fictício" not in mapa


def test_i_cpf_de_onze_digitos_tambem_e_icp(powershell):
    responder(powershell, [PESSOA])
    assert "fulano de tal ficticio" in carregar_certificados()


def test_i_certificado_de_sistema_e_descartado(powershell, capsys):
    """CN em GUID nao serve ao gov.br. Apontar a policy para um deles faz o
    Chrome desistir e abrir a janela de escolha manual."""
    responder(powershell, [ALFA, SISTEMA])
    mapa, ignorados = certificados_windows.descobrir()

    assert not any("microsoft" in k for k in mapa)
    assert ignorados == 1, "a contagem volta no resultado; antes so existia como print"
    assert capsys.readouterr().out == ""


def test_i_o_primeiro_vence_em_colisao_de_chave(powershell):
    """`setdefault`: dois certificados com o mesmo nome — o primeiro fica."""
    gemeo = dict(ALFA, serial="0Z99", thumbprint="ZZZZ9999")
    responder(powershell, [ALFA, gemeo])
    mapa = carregar_certificados()

    assert mapa["alfa ficticia ltda"]["serial"] == "0A01"


def test_i_display_ausente_nao_gera_chave_vazia(powershell):
    cert = dict(ALFA, display="")
    responder(powershell, [cert])
    mapa = carregar_certificados()

    assert "" not in mapa
    assert len(mapa) == 2


def test_i_cn_ausente_descarta_o_certificado(powershell):
    responder(powershell, [dict(ALFA, subject_cn="")])
    assert carregar_certificados() == {}


def test_i_saida_incompleta_sem_serial_nao_quebra_a_indexacao(powershell):
    """Campos ausentes so aparecem quando alguem os le — a indexacao nao os toca."""
    incompleto = {"display": "GAMA FICTICIA", "subject_cn": "GAMA FICTICIA:33333333000153"}
    responder(powershell, [incompleto])
    mapa = carregar_certificados()

    assert mapa["gama ficticia"] == incompleto


def test_i_repositorio_vazio_devolve_mapa_vazio(powershell):
    powershell.resposta = Saida(stdout="")
    assert carregar_certificados() == {}


# ── R · o circuito completo: descoberta → match ───────────────────────────────

def test_r_do_powershell_ate_o_resultado_da_busca(powershell):
    """A prova que mais importa: a saida sintetica do Windows alimenta o match
    puro da fatia 1 e produz o certificado certo. Sem Windows nenhum."""
    responder(powershell, [ALFA, BETA, PESSOA])
    certs = carregar_certificados()

    def identidade(nome):
        resultado = buscar_certificado(nome, certificados_windows.identidades(certs))
        return certs[resultado.chave]["subject_cn"] if resultado.resolvida else None

    # Afirma IDENTIDADE, e nao chave: qual das chaves do mesmo certificado volta
    # depende da ordem de iteracao de um `set`. E o CERTIFICATE_MATCH_POSSIBLE_
    # DEFECT #3 da fatia 1, e ele atravessa o circuito inteiro sem mudar de forma.
    assert identidade("ALFA FICTICIA LTDA") == ALFA["subject_cn"]
    assert identidade("Fulano") == PESSOA["subject_cn"]
    assert identidade("BETA") == BETA["subject_cn"]
    assert identidade("nao existe") is None


def test_r_ambiguidade_atravessa_o_circuito_inteiro(powershell):
    """A propriedade de seguranca da fatia 1.1 continua valendo com dados vindos
    da descoberta: dois certificados plausiveis, nenhum escolhido."""
    outra = {
        "display": "ALFA COMERCIO FICTICIO",
        "subject_cn": "ALFA COMERCIO FICTICIO:44444444000114",
        "serial": "0E05",
    }
    responder(powershell, [ALFA, outra])
    certs = carregar_certificados()

    resultado = buscar_certificado("ALFA", certificados_windows.identidades(certs))

    assert resultado.chave is None
    assert resultado.ambigua is True
