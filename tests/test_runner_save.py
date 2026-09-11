"""A boundary da plataforma: `ctx` entra, e nao sai daqui.

Nada nestes testes toca plataforma, cofre, certificado, navegador, portal,
Certificate Store, PowerShell ou registro. O `ctx` e um objeto de vinte linhas e
o "certificado" e um arquivo de bytes sinteticos — a fase prova a MONTAGEM dos
objetos, e nao a execucao do dominio.
"""
import ast
import pathlib

import pytest
from planilhas_sinteticas import criar_planilha

import runner
from automation import espaco_de_trabalho
from certificados_do_cofre import CertificadosDoCofre

RAIZ = pathlib.Path(__file__).resolve().parents[1]

BYTES_SINTETICOS = b"nao-e-um-certificado-de-verdade"
SENHA_FICTICIA = "senha-ficticia-de-teste"
CHAVE_FICTICIA = "chave-ficticia-de-teste"


class CredencialFalsa:
    def __init__(self, path, password):
        self.path = str(path)
        self.password = password


class SecretsFalso:
    def __init__(self, cofre):
        self._cofre = cofre
        self.pedidos = []

    def cert(self, nome):
        self.pedidos.append(nome)
        if nome not in self._cofre:
            raise RuntimeError("credencial nao encontrada")
        return CredencialFalsa(self._cofre[nome], SENHA_FICTICIA)


class CtxFalso:
    """O pedaco do contexto da plataforma que esta fase usa, e mais nada."""

    def __init__(self, planilha, cofre=None):
        self._planilha = str(planilha)
        self.secrets = SecretsFalso(cofre or {})
        self.entradas_pedidas = []

    def input_file(self, nome):
        self.entradas_pedidas.append(nome)
        return self._planilha


@pytest.fixture(autouse=True)
def _sem_execucao_real(monkeypatch):
    """A aplicacao e o lock do host sao substituidos: esta fase monta objetos.

    Substituir `app.executar` e o que mantem o teste offline — o dominio real
    abriria navegador e falaria com o portal.
    """
    chamadas = []
    monkeypatch.setattr(runner.app, "executar",
                        lambda *a, **k: chamadas.append((a, k)))
    monkeypatch.setattr(runner.exclusividade_host, "adquirir", lambda: "lease-falso")
    monkeypatch.setattr(runner.exclusividade_host, "liberar", lambda c: None)
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE_FICTICIA)
    return chamadas


def _cofre(tmp_path, aliases):
    materializados = {}
    for alias in aliases:
        arquivo = tmp_path / "cofre" / f"{alias}.pfx"
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_bytes(BYTES_SINTETICOS + alias.encode())
        materializados[alias] = arquivo
    return materializados


def _planilha(tmp_path, linhas=None):
    caminho = tmp_path / "recebida.xlsx"
    if linhas is None:
        criar_planilha(str(caminho))
    else:
        criar_planilha(str(caminho), linhas=linhas)
    return caminho


def _certificados_da_planilha(caminho):
    from automation.planilha import aliases_de_certificado

    return aliases_de_certificado(str(caminho))


# ── a entrada vem da plataforma ──────────────────────────────────────────────

def test_a_planilha_e_pedida_por_input_file(tmp_path, _sem_execucao_real):
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    runner.executar_no_save(ctx)

    assert ctx.entradas_pedidas == ["planilha"]


def test_o_anexo_recebido_NAO_e_modificado(tmp_path, _sem_execucao_real):
    caminho = _planilha(tmp_path)
    antes = caminho.read_bytes()
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    runner.executar_no_save(ctx)

    assert caminho.read_bytes() == antes


def test_a_aplicacao_recebe_a_COPIA_de_trabalho(tmp_path, _sem_execucao_real):
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    runner.executar_no_save(ctx)

    (args, _), = _sem_execucao_real
    entrada = args[0]
    assert pathlib.Path(entrada.planilha).name == espaco_de_trabalho.NOME_DA_PLANILHA
    assert pathlib.Path(entrada.planilha) != caminho


def test_o_chao_da_execucao_some_no_fim(tmp_path, _sem_execucao_real):
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    runner.executar_no_save(ctx)

    (args, _), = _sem_execucao_real
    assert not pathlib.Path(args[0].planilha).exists()


def test_o_chao_some_TAMBEM_quando_a_execucao_morre(tmp_path, monkeypatch):
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))
    visto = {}

    def explodir(entrada, config, **kwargs):
        visto["planilha"] = entrada.planilha
        raise ZeroDivisionError("a execução morreu no meio")

    monkeypatch.setattr(runner.app, "executar", explodir)

    with pytest.raises(ZeroDivisionError):
        runner.executar_no_save(ctx)

    assert not pathlib.Path(visto["planilha"]).exists()


# ── o provedor do cofre e composto aqui ──────────────────────────────────────

def test_a_aplicacao_recebe_o_PROVEDOR_do_cofre(tmp_path, _sem_execucao_real):
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    runner.executar_no_save(ctx)

    (_, kwargs), = _sem_execucao_real
    assert isinstance(kwargs["provedor_de_certificados"], CertificadosDoCofre)


def test_o_resolvedor_do_provedor_e_o_ctx_secrets_cert(tmp_path, _sem_execucao_real):
    caminho = _planilha(tmp_path)
    aliases = _certificados_da_planilha(caminho)
    ctx = CtxFalso(caminho, _cofre(tmp_path, aliases))

    runner.executar_no_save(ctx)

    (_, kwargs), = _sem_execucao_real
    assert kwargs["provedor_de_certificados"].carregar() == len(aliases)
    assert sorted(set(ctx.secrets.pedidos)) == sorted(set(aliases))


def test_o_ctx_NAO_chega_a_aplicacao(tmp_path, _sem_execucao_real):
    """Se `ctx` atravessasse, a aplicacao passaria a depender da plataforma —
    e o `local.py` deixaria de conseguir roda-la."""
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    runner.executar_no_save(ctx)

    (args, kwargs), = _sem_execucao_real
    assert ctx not in args
    assert ctx not in kwargs.values()
    assert not any(isinstance(v, CtxFalso) for v in (*args, *kwargs.values()))


def test_o_pfx_vai_para_o_chao_da_execucao(tmp_path, _sem_execucao_real):
    caminho = _planilha(tmp_path)
    aliases = _certificados_da_planilha(caminho)
    ctx = CtxFalso(caminho, _cofre(tmp_path, aliases))
    materializados = []

    def espiar(entrada, config, **kwargs):
        provedor = kwargs["provedor_de_certificados"]
        provedor.carregar()
        materializados.append(
            pathlib.Path(provedor.certificado(aliases[0]).pfx_path))

    runner.app.executar = espiar
    runner.executar_no_save(ctx)

    copia, = materializados
    assert not copia.exists(), "some com o chao da execucao"
    assert str(tmp_path / "cofre") not in str(copia), "nao e o arquivo do cofre"


# ── o certificado continua sendo por linha ───────────────────────────────────

def test_aliases_diferentes_produzem_certificados_diferentes(tmp_path, _sem_execucao_real):
    from planilhas_sinteticas import ALFA, BETA

    caminho = tmp_path / "recebida.xlsx"
    criar_planilha(str(caminho), linhas=(ALFA, BETA))
    aliases = _certificados_da_planilha(caminho)
    assert len(aliases) >= 2, "a planilha sintetica precisa de dois certificados"

    ctx = CtxFalso(caminho, _cofre(tmp_path, aliases))
    vistos = {}

    def espiar(entrada, config, **kwargs):
        provedor = kwargs["provedor_de_certificados"]
        provedor.carregar()
        for alias in aliases:
            vistos[alias] = provedor.certificado(alias).pfx_path

    runner.app.executar = espiar
    runner.executar_no_save(ctx)

    assert len(set(vistos.values())) == len(aliases)


def test_o_alias_repetido_e_baixado_uma_vez(tmp_path, _sem_execucao_real):
    from planilhas_sinteticas import ALFA

    caminho = tmp_path / "recebida.xlsx"
    criar_planilha(str(caminho), linhas=(ALFA, ALFA))
    aliases = _certificados_da_planilha(caminho)
    ctx = CtxFalso(caminho, _cofre(tmp_path, aliases))

    def espiar(entrada, config, **kwargs):
        kwargs["provedor_de_certificados"].carregar()

    runner.app.executar = espiar
    runner.executar_no_save(ctx)

    assert len(ctx.secrets.pedidos) == len(set(ctx.secrets.pedidos))


# ── fronteiras estruturais ───────────────────────────────────────────────────

def _importados(caminho):
    # `utf-8-sig`: `main.py` e o entrypoint legado e comeca com BOM.
    arvore = ast.parse(caminho.read_text(encoding="utf-8-sig"))
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes.add(no.module.split(".")[0])
    return nomes


def test_o_caminho_save_nao_conhece_o_certificate_store():
    """O provedor do cofre e o do Windows sao alternativas, nao camadas."""
    assert "certificados_windows" not in _importados(RAIZ / "certificados_do_cofre.py")
    assert "cert_windows" not in _importados(RAIZ / "certificados_do_cofre.py")


def test_a_aplicacao_nao_conhece_a_plataforma_nem_o_adapter_do_cofre():
    proibidos = {"autohub_sdk", "autohub", "certificados_do_cofre", "runner"}
    for modulo in sorted((RAIZ / "automation").glob("*.py")):
        assert not (_importados(modulo) & proibidos), modulo.name


def test_o_local_continua_independente_do_runner():
    """Dois adapters IRMAOS: se o local dependesse do runner, rodar na sua
    maquina passaria a depender da plataforma."""
    assert "runner" not in _importados(RAIZ / "local.py")
    assert "runner" not in _importados(RAIZ / "main.py")


def test_o_runner_nao_ganhou_regra_de_negocio():
    """A borda monta objetos. Quem decide e o app."""
    arvore = ast.parse((RAIZ / "runner.py").read_text(encoding="utf-8"))
    save = next(n for n in ast.walk(arvore)
                if isinstance(n, ast.FunctionDef) and n.name == "executar_no_save")

    assert not [n for n in ast.walk(save) if isinstance(n, (ast.For, ast.While))]

    # Os NOMES que a borda pede ao mundo. `cnpj`, `deal_id` e `origem` sao do
    # contrato historico/webhook, e nao entram como entrada desta automacao.
    literais = {n.value for n in ast.walk(save)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "planilha" in literais
    assert not (literais & {"cnpj", "deal_id", "origem"})


def test_o_contrato_de_entrada_e_so_a_planilha():
    """`cnpj`, `deal_id` e `origem` sao do contrato historico/webhook, e nao
    entram como entrada desta automacao."""
    from automation.boundary import CAMPOS_CONHECIDOS

    assert CAMPOS_CONHECIDOS == frozenset({"planilha"})
