"""O adapter do cofre: a plataforma responde o que o Certificate Store respondia.

Nada aqui toca cofre real, credencial real, certificado real, Certificate Store,
PowerShell, registro ou navegador. O "cofre" e uma funcao de tres linhas, e o
"certificado" e um arquivo de bytes sinteticos — o adapter copia arquivo, ele
nao le certificado.
"""
import ast
import pathlib

from automation import app, eventos, planilha
from automation.captcha import ConfigCaptcha
from automation.certificados import ProvedorDeCertificados
from certificados_do_cofre import CertificadosDoCofre

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ConfigCaptcha(api_key="chave-ficticia")

# Nada de certificado de verdade: o adapter copia bytes, e e isso que se testa.
BYTES_SINTETICOS = b"nao-e-um-certificado-de-verdade"
SENHA_FICTICIA = "senha-ficticia-de-teste"


class CredencialFalsa:
    """O que o cofre devolve: um objeto com `path` e `password`."""

    def __init__(self, path, password):
        self.path = str(path)
        self.password = password


def _cofre(tmp_path, aliases, senha=SENHA_FICTICIA, conteudo=BYTES_SINTETICOS):
    """Um cofre de mentira: alias -> arquivo materializado, como o SDK faz."""
    materializados = {}
    for alias in aliases:
        arquivo = tmp_path / "cofre" / f"{alias}.pfx"
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_bytes(conteudo + alias.encode())
        materializados[alias] = arquivo

    def resolver(nome):
        if nome not in materializados:
            raise RuntimeError("credencial nao encontrada")
        return CredencialFalsa(materializados[nome], senha)

    return resolver


# ── o adapter satisfaz o seam ────────────────────────────────────────────────

def test_o_adapter_do_cofre_SATISFAZ_o_seam(tmp_path):
    provedor = CertificadosDoCofre(["ALFA"], _cofre(tmp_path, ["ALFA"]),
                                   tmp_path / "run")

    assert isinstance(provedor, ProvedorDeCertificados)
    assert provedor.carregar() == 1
    assert provedor.resolver("ALFA").resolvida
    assert provedor.certificado("ALFA").pfx_path


def test_o_certificado_do_cofre_aponta_para_UM_ARQUIVO_e_traz_a_senha(tmp_path):
    provedor = CertificadosDoCofre(["ALFA"], _cofre(tmp_path, ["ALFA"]),
                                   tmp_path / "run")
    provedor.carregar()

    certificado = provedor.certificado("ALFA")

    assert pathlib.Path(certificado.pfx_path).is_file()
    assert certificado.pfx_senha == SENHA_FICTICIA


def test_o_subject_cn_vem_VAZIO__e_isso_e_o_ponto(tmp_path):
    """O fork decide usar o Windows Store por `bool(cert_subject_cn)`. Preencher
    este campo faria a maquina escolher um certificado instalado em vez do que
    veio do cofre — que e exatamente o caminho que nao pode existir aqui."""
    provedor = CertificadosDoCofre(["ALFA"], _cofre(tmp_path, ["ALFA"]),
                                   tmp_path / "run")
    provedor.carregar()

    certificado = provedor.certificado("ALFA")

    assert certificado.subject_cn == ""
    assert certificado.serial == ""


# ── o arquivo e NOSSO ────────────────────────────────────────────────────────

def test_o_pfx_e_COPIADO_para_o_chao_da_execucao(tmp_path):
    """O SDK materializa num diretorio compartilhado e sem limpeza. O que sai
    daqui aponta para a copia desta execucao, que morre com ela."""
    destino = tmp_path / "run"
    do_cofre = tmp_path / "cofre" / "ALFA.pfx"
    provedor = CertificadosDoCofre(["ALFA"], _cofre(tmp_path, ["ALFA"]), destino)
    provedor.carregar()

    copia = pathlib.Path(provedor.certificado("ALFA").pfx_path)

    assert copia.parent == destino
    assert copia != do_cofre
    assert copia.read_bytes() == do_cofre.read_bytes()


def test_o_nome_do_arquivo_NAO_carrega_o_alias(tmp_path):
    """O alias e o nome do certificado de um cliente, e caminho vaza em stack
    trace, em log de excecao e em listagem de diretorio."""
    alias = "CLIENTE FICTICIO LTDA"
    provedor = CertificadosDoCofre([alias], _cofre(tmp_path, [alias]), tmp_path / "run")
    provedor.carregar()

    caminho = provedor.certificado(alias).pfx_path

    assert alias not in caminho
    assert "CLIENTE" not in caminho


def test_o_chao_da_execucao_apaga_a_copia(tmp_path):
    """A limpeza e do espaco de trabalho: o adapter escreve dentro dele."""
    from planilhas_sinteticas import criar_planilha

    from automation import espaco_de_trabalho

    origem = tmp_path / "entrada.xlsx"
    criar_planilha(str(origem))

    with espaco_de_trabalho.abrir(origem) as espaco:
        destino = espaco.raiz / "certificados"
        provedor = CertificadosDoCofre(["ALFA"], _cofre(tmp_path, ["ALFA"]), destino)
        provedor.carregar()
        copia = pathlib.Path(provedor.certificado("ALFA").pfx_path)
        assert copia.exists()

    assert not copia.exists()


# ── o alias e exato, e e por linha ───────────────────────────────────────────

def test_o_alias_e_EXATO__sem_aproximacao(tmp_path):
    """Aproximar aqui transformaria um erro de digitacao em autenticacao na
    empresa errada. O Store precisa de aproximacao; o cofre nao."""
    provedor = CertificadosDoCofre(["ALFA FICTICIA"], _cofre(tmp_path, ["ALFA FICTICIA"]),
                                   tmp_path / "run")
    provedor.carregar()

    assert provedor.resolver("ALFA FICTICIA").resolvida
    assert not provedor.resolver("ALFA FICTICI").resolvida
    assert not provedor.resolver("alfa ficticia").resolvida
    assert not provedor.resolver("ALFA").resolvida


def test_espaco_em_volta_do_alias_nao_conta(tmp_path):
    provedor = CertificadosDoCofre(["  ALFA  "], _cofre(tmp_path, ["ALFA"]),
                                   tmp_path / "run")

    assert provedor.carregar() == 1
    assert provedor.resolver("ALFA").resolvida
    assert provedor.resolver("  ALFA ").resolvida


def test_linhas_diferentes_recebem_CERTIFICADOS_DIFERENTES(tmp_path):
    provedor = CertificadosDoCofre(["ALFA", "BETA"], _cofre(tmp_path, ["ALFA", "BETA"]),
                                   tmp_path / "run")
    provedor.carregar()

    alfa = provedor.certificado(provedor.resolver("ALFA").chave)
    beta = provedor.certificado(provedor.resolver("BETA").chave)

    assert alfa.pfx_path != beta.pfx_path
    assert (pathlib.Path(alfa.pfx_path).read_bytes()
            != pathlib.Path(beta.pfx_path).read_bytes())


def test_o_alias_repetido_e_resolvido_UMA_vez(tmp_path):
    pedidos = []
    resolver = _cofre(tmp_path, ["ALFA"])

    def contando(nome):
        pedidos.append(nome)
        return resolver(nome)

    provedor = CertificadosDoCofre(["ALFA", "ALFA", " ALFA "], contando, tmp_path / "run")

    assert provedor.carregar() == 1
    assert pedidos == ["ALFA"], "tres linhas, um download"


# ── desfechos que nao sao sucesso ────────────────────────────────────────────

def test_alias_que_o_cofre_nao_tem_NAO_resolve(tmp_path):
    provedor = CertificadosDoCofre(["ALFA", "SUMIU"], _cofre(tmp_path, ["ALFA"]),
                                   tmp_path / "run")

    assert provedor.carregar() == 1
    assert provedor.resolver("ALFA").resolvida
    assert not provedor.resolver("SUMIU").resolvida


def test_alias_ausente_vira_o_MESMO_evento_de_sempre(tmp_path):
    """A aplicacao nao aprende uma palavra nova: continua sendo 'certificado nao
    instalado', e a linha e pulada."""
    class PlanilhaInerte:
        def salvar(self):
            pass

        def descartar(self):
            pass

        def mapa_status(self, caminho):
            return {}

    provedor = CertificadosDoCofre(["ALFA"], _cofre(tmp_path, ["ALFA"]), tmp_path / "run")
    provedor.carregar()
    recebidos = []
    execucao = app._Execucao(PlanilhaInerte(), "p.xlsx", CONFIG,
                             lambda e: recebidos.append(e.codigo), provedor)
    item = planilha.ItemPendente(posicao=0, cnpj="11111111000191",
                                 certificado="SUMIU", linha=2)

    assert execucao.trocar_certificado(item) is False
    assert eventos.CERTIFICADO_NAO_INSTALADO in recebidos


def test_credencial_sem_arquivo_nao_entra_no_catalogo(tmp_path):
    """Nem toda credencial e certificado: uma chave de API tem senha e nao tem
    `.pfx`."""
    def so_senha(nome):
        return CredencialFalsa("", "valor-ficticio")

    provedor = CertificadosDoCofre(["ALFA"], so_senha, tmp_path / "run")

    assert provedor.carregar() == 0
    assert not provedor.resolver("ALFA").resolvida


# ── nada de segredo vazando ──────────────────────────────────────────────────

def test_a_senha_nao_aparece_no_repr_do_certificado(tmp_path):
    provedor = CertificadosDoCofre(["ALFA"], _cofre(tmp_path, ["ALFA"]),
                                   tmp_path / "run")
    provedor.carregar()

    texto = repr(provedor.certificado("ALFA"))

    assert SENHA_FICTICIA not in texto


def test_o_adapter_nao_registra_nada_do_cofre(capsys, tmp_path):
    """Sem `print`: uma falha do cofre traz o nome da credencial na mensagem."""
    provedor = CertificadosDoCofre(["ALFA", "SUMIU"], _cofre(tmp_path, ["ALFA"]),
                                   tmp_path / "run")
    provedor.carregar()

    saida = capsys.readouterr()
    assert saida.out == ""
    assert saida.err == ""


# ── fronteiras estruturais ───────────────────────────────────────────────────

def test_o_adapter_NAO_importa_windows_nem_o_sdk():
    arvore = ast.parse((RAIZ / "certificados_do_cofre.py").read_text(encoding="utf-8"))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])

    proibidos = {"autohub_sdk", "autohub", "cert_windows", "winreg", "ctypes",
                 "subprocess", "patchright", "playwright", "servicos_rf_login",
                 "tkinter"}
    assert not (importados & proibidos), importados & proibidos


def test_o_pacote_automation_continua_sem_plataforma_e_sem_navegador():
    """A regra que esta fase nao pode afrouxar.

    Confere IMPORTS, e nao texto: o seam explica `client_certificates` na
    docstring de proposito — dizer por que a plataforma precisa de outro
    provedor e o motivo de ele existir. Prosa nao cria dependencia.
    """
    # `patchright` NAO entra nesta lista, e a razao importa: quatro integracoes
    # ja o importam, e so para capturar `Error` — e a traducao de erro externo na
    # fronteira, que e o desenho do repositorio desde antes desta fase. Proibi-lo
    # aqui seria inventar uma regra que o projeto nunca teve.
    #
    # O que esta fase nao pode deixar entrar e a PLATAFORMA: SDK, o adapter do
    # cofre, e a montagem de `client_certificates` — essa fica no fork, atras da
    # fronteira do login.
    proibidos = {"autohub_sdk", "autohub", "certificados_do_cofre"}
    for modulo in sorted((RAIZ / "automation").glob("*.py")):
        arvore = ast.parse(modulo.read_text(encoding="utf-8"))
        importados = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                importados.update(a.name.split(".")[0] for a in no.names)
            elif isinstance(no, ast.ImportFrom) and no.module:
                importados.add(no.module.split(".")[0])
        assert not (importados & proibidos), f"{modulo.name}: {importados & proibidos}"
