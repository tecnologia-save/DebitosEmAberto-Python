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
from automation.eventos import EventoOperacional
from certificados_do_cofre import CertificadosDoCofre

RAIZ = pathlib.Path(__file__).resolve().parents[1]

BYTES_SINTETICOS = b"nao-e-um-certificado-de-verdade"
SENHA_FICTICIA = "senha-ficticia-de-teste"
CHAVE_FICTICIA = "chave-ficticia-de-teste"


class CredencialFalsa:
    def __init__(self, path, password):
        self.path = str(path)
        self.password = password


class SegredoFalso:
    """Como o SDK: o valor so sai por `.reveal()`, e o `repr` nunca o mostra."""

    def __init__(self, nome, valor):
        self._nome = nome
        self._valor = valor

    def reveal(self):
        return self._valor

    def __repr__(self):
        return f"<Secret {self._nome}>"


class SecretsFalso:
    def __init__(self, cofre, chave_gemini=CHAVE_FICTICIA):
        self._cofre = cofre
        self._chave_gemini = chave_gemini
        self.pedidos = []
        self.segredos_pedidos = []

    def cert(self, nome):
        self.pedidos.append(nome)
        if nome not in self._cofre:
            raise RuntimeError("credencial nao encontrada")
        return CredencialFalsa(self._cofre[nome], SENHA_FICTICIA)

    def get(self, nome):
        self.segredos_pedidos.append(nome)
        if self._chave_gemini is None:
            raise RuntimeError("credencial nao vinculada")
        return SegredoFalso(nome, self._chave_gemini)


class CtxFalso:
    """O pedaco do contexto da plataforma que esta fase usa, e mais nada."""

    def __init__(self, planilha, cofre=None, chave_gemini=CHAVE_FICTICIA):
        self._planilha = str(planilha)
        self.secrets = SecretsFalso(cofre or {}, chave_gemini)
        self.entradas_pedidas = []
        self.checkpoints = []
        self.artefatos = []
        self.saidas = []

    def input_file(self, nome):
        self.entradas_pedidas.append(nome)
        return self._planilha

    def checkpoint(self, nome, conteudo, mime="application/octet-stream",
                   kind="file"):
        self.checkpoints.append((nome, bytes(conteudo), mime, kind))

    def artifact(self, nome, conteudo, mime="application/octet-stream",
                 kind="file"):
        self.artefatos.append((nome, bytes(conteudo), mime, kind))

    def output(self, dados):
        self.saidas.append(dados)


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


# ── a chave do Gemini vem do cofre ───────────────────────────────────────────

def test_a_chave_do_gemini_vem_do_COFRE_e_nao_do_ambiente(tmp_path, monkeypatch,
                                                          _sem_execucao_real):
    """Sem variavel de ambiente nenhuma, o caminho da plataforma continua
    funcionando — e e assim que ele tem de ser: no runtime remoto nao ha `.env`
    nem ambiente preparado por ninguem."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    runner.executar_no_save(ctx)

    assert ctx.secrets.segredos_pedidos == [runner.ALIAS_DO_GEMINI]
    (args, _), = _sem_execucao_real
    assert args[1].api_key == CHAVE_FICTICIA


def test_o_alias_pedido_e_o_CANONICO():
    """Nao o `Gemini_KEY` da automacao irma: aquilo e sonda de uma regressao do
    resolver da plataforma, e copiar propagaria o defeito."""
    assert runner.ALIAS_DO_GEMINI == "gemini_api_key"


def test_a_chave_nao_aparece_no_repr_da_config(tmp_path, _sem_execucao_real):
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    runner.executar_no_save(ctx)

    (args, _), = _sem_execucao_real
    assert CHAVE_FICTICIA not in repr(args[1])


def test_chave_ausente_para_ANTES_de_qualquer_efeito(tmp_path, monkeypatch,
                                                     _sem_execucao_real):
    """Configuracao invalida nao se resolve repetindo, e o certificado nem
    chega a ser baixado."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)),
                   chave_gemini="")

    with pytest.raises(runner.ConfiguracaoInvalida):
        runner.executar_no_save(ctx)

    assert ctx.entradas_pedidas == [], "nem a planilha foi pedida"
    assert ctx.secrets.pedidos == [], "nenhum certificado baixado"
    assert _sem_execucao_real == []


def test_o_desktop_continua_lendo_a_chave_do_AMBIENTE(tmp_path, monkeypatch,
                                                      _sem_execucao_real):
    """`executar` sem `config` e o caminho de quem roda sem plataforma."""
    monkeypatch.setenv("GEMINI_API_KEY", "chave-do-ambiente-ficticia")
    caminho = _planilha(tmp_path)

    runner.executar({"planilha": str(caminho)})

    (args, _), = _sem_execucao_real
    assert args[1].api_key == "chave-do-ambiente-ficticia"


# ── os seams que ja existiam, e que esta fase reusa ──────────────────────────

def test_o_captcha_ja_tem_seam_e_ele_nao_precisa_de_gemini():
    """`resolver_bruto` e a fronteira externa inteira num callable."""
    from automation import captcha

    chamadas = []
    desfecho = captcha.resolver(
        "pagina-falsa",
        captcha.ConfigCaptcha(api_key=CHAVE_FICTICIA),
        tentativas=1,
        resolver_bruto=lambda alvo: chamadas.append(alvo) or True,
    )

    assert desfecho == captcha.RESOLVIDO_OU_AUSENTE
    assert chamadas == ["pagina-falsa"]


def test_o_login_ja_tem_seam_e_ele_nao_precisa_de_navegador():
    """`fazer_login` entra por parametro: o login inteiro roda sem Chromium."""
    from automation.login import AUTENTICADO, Certificado, ConfigLogin, autenticar

    recebido = {}

    def fork(**kwargs):
        recebido.update(kwargs)
        return ("playwright-falso", "contexto-falso", "pagina-falsa")

    resultado = autenticar(
        Certificado(subject_cn="", pfx_path="0.pfx", pfx_senha="senha-ficticia"),
        ConfigLogin(diretorio_perfil="perfil-falso", gemini_api_key=CHAVE_FICTICIA),
        True, fork)

    assert resultado.situacao == AUTENTICADO
    assert recebido["cert_pfx_path"] == "0.pfx"
    assert recebido["cert_subject_cn"] == "", "sem Store no caminho do arquivo"


def test_o_certificado_do_cofre_NUNCA_cai_no_windows_store(tmp_path,
                                                           _sem_execucao_real):
    """O fork decide usar o Store por `bool(cert_subject_cn)`. Se o provedor do
    cofre preenchesse esse campo, a maquina escolheria um certificado instalado
    — e autenticaria na empresa errada sem erro nenhum."""
    caminho = _planilha(tmp_path)
    aliases = _certificados_da_planilha(caminho)
    ctx = CtxFalso(caminho, _cofre(tmp_path, aliases))
    vistos = []

    def espiar(entrada, config, **kwargs):
        provedor = kwargs["provedor_de_certificados"]
        provedor.carregar()
        vistos.extend(provedor.certificado(a) for a in aliases)

    runner.app.executar = espiar
    runner.executar_no_save(ctx)

    assert vistos
    for certificado in vistos:
        assert certificado.subject_cn == ""
        assert certificado.pfx_path


def test_o_dominio_nao_conhece_navegador_nem_gemini():
    proibidos = {"patchright", "playwright", "google", "resolvedor_captcha",
                 "autohub_sdk", "autohub"}
    assert not (_importados(RAIZ / "automation" / "domain.py") & proibidos)


# ── perfil do navegador por execucao ─────────────────────────────────────────

def test_o_chao_da_execucao_chega_a_aplicacao(tmp_path, _sem_execucao_real):
    """Dele nasce o perfil do navegador — e a aplicação não sabe disso."""
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    runner.executar_no_save(ctx)

    (args, kwargs), = _sem_execucao_real
    chao = pathlib.Path(kwargs["diretorio_da_execucao"])
    assert chao == pathlib.Path(args[0].planilha).parent
    assert chao != RAIZ, "nunca a raiz do repositorio"


def test_duas_execucoes_recebem_CHAOS_DIFERENTES(tmp_path, _sem_execucao_real):
    caminho = _planilha(tmp_path)
    aliases = _certificados_da_planilha(caminho)

    runner.executar_no_save(CtxFalso(caminho, _cofre(tmp_path, aliases)))
    runner.executar_no_save(CtxFalso(caminho, _cofre(tmp_path, aliases)))

    (_, a), (_, b) = _sem_execucao_real
    assert a["diretorio_da_execucao"] != b["diretorio_da_execucao"]


def test_o_chao_e_o_perfil_somem_com_a_execucao(tmp_path, _sem_execucao_real):
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    runner.executar_no_save(ctx)

    (_, kwargs), = _sem_execucao_real
    assert not pathlib.Path(kwargs["diretorio_da_execucao"]).exists()


def test_o_desktop_continua_com_o_diretorio_de_sempre(tmp_path, _sem_execucao_real,
                                                      monkeypatch):
    """`executar` sem chão é o caminho de quem roda sem plataforma."""
    monkeypatch.setenv("GEMINI_API_KEY", "chave-do-ambiente-ficticia")
    caminho = _planilha(tmp_path)

    runner.executar({"planilha": str(caminho)})

    (_, kwargs), = _sem_execucao_real
    assert kwargs["diretorio_da_execucao"] is None, "a fiação decide, como sempre"


def test_a_fiacao_deriva_o_perfil_do_chao_recebido():
    """Quem transforma um diretório em perfil continua sendo `maquina`."""
    import inspect

    from automation import maquina

    fonte = inspect.getsource(maquina.abrir_sessao)
    assert "diretorio_da_execucao or diretorio_de_perfil()" in fonte
    assert "diretorio_perfil=perfil" in fonte


# ── checkpoint, artefato e output ────────────────────────────────────────────

def test_cada_gravacao_da_planilha_vira_um_CHECKPOINT(tmp_path, _sem_execucao_real):
    """Sem thread e sem relógio: o checkpoint sai no instante em que o arquivo
    ficou consistente, que é o único em que lê-lo é seguro."""
    from automation import eventos

    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    def duas_gravacoes(entrada, config, **kwargs):
        emitir = kwargs["emitir_evento"]
        emitir(EventoOperacional(eventos.PLANILHA_GRAVADA))
        emitir(EventoOperacional(eventos.PLANILHA_GRAVADA))

    runner.app.executar = duas_gravacoes
    runner.executar_no_save(ctx)

    assert len(ctx.checkpoints) == 2
    nome, conteudo, mime, kind = ctx.checkpoints[0]
    assert nome == runner.NOME_DA_SAIDA
    assert mime == runner.TIPO_DA_SAIDA
    assert conteudo.startswith(b"PK"), "é a planilha, e ela abre"
    assert kind == "spreadsheet"


def test_evento_QUALQUER_nao_vira_checkpoint(tmp_path, _sem_execucao_real):
    from automation import eventos

    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    def so_ruido(entrada, config, **kwargs):
        kwargs["emitir_evento"](EventoOperacional(eventos.ITEM_INICIADO, posicao=0))

    runner.app.executar = so_ruido
    runner.executar_no_save(ctx)

    assert ctx.checkpoints == []


def test_o_artefato_final_e_a_planilha(tmp_path, _sem_execucao_real):
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    runner.executar_no_save(ctx)

    (nome, conteudo, mime, kind), = ctx.artefatos
    assert nome == runner.NOME_DA_SAIDA
    assert mime == runner.TIPO_DA_SAIDA
    assert kind == "spreadsheet"
    assert conteudo.startswith(b"PK")


def test_o_output_e_pequeno_e_sem_segredo(tmp_path, _sem_execucao_real):
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    runner.executar_no_save(ctx)

    saida, = ctx.saidas
    assert set(saida) == {"ok"}
    texto = repr(saida)
    for segredo in (CHAVE_FICTICIA, SENHA_FICTICIA, ".pfx", str(tmp_path)):
        assert segredo not in texto


def test_execucao_que_morre_NAO_publica_artefato_mas_mantem_o_checkpoint(tmp_path,
                                                                         monkeypatch):
    """O checkpoint existe justamente para sobreviver ao que o artefato não
    sobrevive."""
    from automation import eventos

    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    def gravar_e_morrer(entrada, config, **kwargs):
        kwargs["emitir_evento"](EventoOperacional(eventos.PLANILHA_GRAVADA))
        raise ZeroDivisionError("a execução morreu no meio")

    monkeypatch.setattr(runner.app, "executar", gravar_e_morrer)

    with pytest.raises(ZeroDivisionError):
        runner.executar_no_save(ctx)

    assert len(ctx.checkpoints) == 1
    assert ctx.artefatos == []
    assert ctx.saidas == []


def test_o_anexo_original_nao_entra_no_que_e_publicado(tmp_path, _sem_execucao_real):
    caminho = _planilha(tmp_path)
    antes = caminho.read_bytes()
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    runner.executar_no_save(ctx)

    assert caminho.read_bytes() == antes, "o anexo continua intocado"


# ── a task da plataforma ─────────────────────────────────────────────────────

def test_a_task_chega_a_boundary_sem_pedir_nada_a_ninguem(tmp_path, monkeypatch,
                                                          _sem_execucao_real):
    """`main` e o que o agente chama. Pelo caminho inteiro ate a aplicacao nada
    pergunta nada: a execucao roda sem ninguem diante dela, e um `input()`
    ficaria esperando quem nao existe.

    A mesma chamada prova que a aplicacao roda UMA vez e recebe o provedor do
    cofre, e nao o do Certificate Store."""
    def ninguem_responde(*_a, **_k):
        raise AssertionError("o caminho da plataforma pediu interacao manual")

    monkeypatch.setattr("builtins.input", ninguem_responde)
    caminho = _planilha(tmp_path)
    ctx = CtxFalso(caminho, _cofre(tmp_path, _certificados_da_planilha(caminho)))

    resultado = runner.main(ctx)

    (_, kwargs), = _sem_execucao_real
    assert isinstance(kwargs["provedor_de_certificados"], CertificadosDoCofre)
    assert ctx.entradas_pedidas == ["planilha"]
    assert ctx.saidas == [resultado]
    assert len(ctx.artefatos) == 1
