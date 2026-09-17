"""O entrypoint da plataforma: a task registrada, e nada alem de entregar o `ctx`.

O CONTRATO VEM DE EVIDENCIA, e nao de template. Tres fontes dizem a mesma coisa:
o runner historico desta automacao, a docstring do proprio SDK instalado no
agente e o describe estatico do SDK, que le o decorator como arvore. Estes
testes conferem as tres formas pelas quais a plataforma enxerga o arquivo: a
arvore, o modulo importado e o processo executado.

O SDK usado aqui e o REAL — o `conftest` o encontra onde o agente o instala.
Os processos filhos rodam com `AUTOHUB_DESCRIBE=1`: nesse modo o SDK emite o
schema declarado e retorna sem chamar `main`. Nada aqui fala com broker, cofre,
portal, Gemini ou navegador.
"""
import ast
import json
import os
import pathlib
import runpy
import subprocess
import sys

import pytest

# O SDK e injetado pelo agente em runtime (ver `tests/conftest.py`). Sem ele, os
# testes de ARVORE continuam valendo — o contrato da task esta ESCRITO no
# arquivo, e e assim que o publish o le — e so os que importam o runner ou falam
# com o SDK de verdade sao pulados.
try:
    import autohub_sdk

    import runner
except ModuleNotFoundError:  # pragma: no cover — so acontece sem o agente
    autohub_sdk = runner = None

precisa_do_sdk = pytest.mark.skipif(
    autohub_sdk is None,
    reason="autohub_sdk ausente: o SDK e injetado pelo agente em runtime",
)

RAIZ = pathlib.Path(__file__).resolve().parents[1]
RUNNER = RAIZ / "runner.py"
SDK = pathlib.Path(autohub_sdk.__file__).resolve().parent if autohub_sdk else None

ID_DA_TASK = "AUT-DEBITOS-ABERTO"
DESCRICAO = {"t": "describe", "id": ID_DA_TASK, "retries": 0, "params": [], "inputs": []}

NOMES_DO_SDK = {"autohub_sdk", "autohub"}
INTERACAO_MANUAL = {"tkinter", "ui_upload", "msvcrt", "getpass"}
CERTIFICATE_STORE = {"automation.certificados_windows", "certificados_windows",
                     "cert_windows", "winreg", "ctypes", "subprocess"}

# O que nao e codigo do projeto: perfil de navegador, build, logs e a suite.
FORA_DO_CODIGO = {"tests", "__pycache__", "build", "dist", "logs", "chrome_debug_profile"}


def _arvore(caminho=RUNNER):
    # `utf-8-sig`: `main.py` e o entrypoint legado e comeca com BOM.
    return ast.parse(caminho.read_text(encoding="utf-8-sig"))


def _modulos(caminho):
    """Os modulos importados, pelo nome completo e pelo pacote.

    `from automation import certificados_windows` vira tambem
    `automation.certificados_windows`: so o pacote esconderia o que importa.
    """
    nomes = set()
    for no in ast.walk(_arvore(caminho)):
        if isinstance(no, ast.Import):
            for alias in no.names:
                nomes.update({alias.name, alias.name.split(".")[0]})
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes.update({no.module, no.module.split(".")[0]})
            nomes.update(f"{no.module}.{alias.name}" for alias in no.names)
    return nomes


def _modulos_do_projeto():
    for pasta, subpastas, arquivos in os.walk(RAIZ):
        subpastas[:] = [p for p in subpastas
                        if not p.startswith(".") and p not in FORA_DO_CODIGO]
        for nome in arquivos:
            if nome.endswith(".py"):
                yield pathlib.Path(pasta) / nome


def _sem_docstring(corpo):
    primeiro = corpo[0] if corpo else None
    if (isinstance(primeiro, ast.Expr) and isinstance(primeiro.value, ast.Constant)
            and isinstance(primeiro.value.value, str)):
        return corpo[1:]
    return corpo


def _funcao(nome):
    return next(n for n in _arvore().body
                if isinstance(n, ast.FunctionDef) and n.name == nome)


def _e_atributo(no, objeto, atributo):
    return (isinstance(no, ast.Attribute) and no.attr == atributo
            and isinstance(no.value, ast.Name) and no.value.id == objeto)


def _e_o_bloco_de_script(no):
    teste = getattr(no, "test", None)
    return (isinstance(no, ast.If) and isinstance(teste, ast.Compare)
            and isinstance(teste.left, ast.Name) and teste.left.id == "__name__"
            and len(teste.ops) == 1 and isinstance(teste.ops[0], ast.Eq)
            and len(teste.comparators) == 1
            and isinstance(teste.comparators[0], ast.Constant)
            and teste.comparators[0].value == "__main__")


def _chamadas(arvore, nome):
    """Chamadas a `nome(...)` ou a `algo.nome(...)`."""
    return [n for n in ast.walk(arvore) if isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Name) and n.func.id == nome)
        or (isinstance(n.func, ast.Attribute) and n.func.attr == nome))]


def _como_o_agente(*argumentos, **ambiente):
    """Um processo filho com o SDK por `PYTHONPATH`, como `agent.py` dispara.

    Nenhuma variavel `AUTOHUB_*` herdada entra — broker e token menos ainda.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("AUTOHUB_")}
    env["PYTHONPATH"] = str(SDK.parent)
    env.update(ambiente)
    return subprocess.run(  # noqa: S603 — comando fixo, montado aqui mesmo
        [sys.executable, *argumentos], cwd=RAIZ, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, encoding="utf-8", errors="replace", timeout=120,
        check=False,
    )


def _ndjson(saida):
    return [json.loads(linha) for linha in saida.splitlines() if linha.strip()]


# ── 1 · a task esta registrada ───────────────────────────────────────────────

def test_o_sdk_entra_com_o_nome_do_contrato():
    importacoes = {(alias.name, alias.asname)
                   for no in _arvore().body if isinstance(no, ast.Import)
                   for alias in no.names}

    assert ("autohub_sdk", "autohub") in importacoes


def test_a_task_e_main_com_o_id_historico_e_sem_retransmissao():
    """`AUT-DEBITOS-ABERTO` e o id da TASK, o mesmo do runner historico;
    `AUT-0071` e o da AUTOMACAO na plataforma. Nao sao intercambiaveis.

    LITERAIS de proposito: o describe estatico do SDK le a arvore, e um valor
    computado seria trocado pelo default em silencio. E nenhum `params` ou
    `inputs`: o contrato comprovado da entrada e so `ctx.input_file("planilha")`.
    """
    decoradas = [n for n in ast.walk(_arvore())
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.decorator_list]
    assert [n.name for n in decoradas] == ["main"], "uma task, e so ela"

    decorator, = decoradas[0].decorator_list
    assert isinstance(decorator, ast.Call)
    assert _e_atributo(decorator.func, "autohub", "task")
    assert decorator.args == []

    argumentos = {k.arg: ast.literal_eval(k.value) for k in decorator.keywords}
    assert argumentos == {"id": ID_DA_TASK, "retries": 0}
    assert type(argumentos["retries"]) is int, "`False` tambem e igual a 0"


@precisa_do_sdk
def test_o_sdk_real_guarda_os_mesmos_metadados():
    assert runner.main.__autohub__ == {
        "id": ID_DA_TASK, "retries": 0, "params": [], "inputs": [],
    }


@precisa_do_sdk
def test_o_describe_estatico_do_sdk_le_o_contrato():
    """O describe que o proprio SDK documenta para o edge: le `runner.py` como
    arvore, sem importar a automacao nem as dependencias dela."""
    processo = _como_o_agente(str(SDK / "_describe.py"), str(RUNNER))

    assert processo.returncode == 0
    assert _ndjson(processo.stdout) == [DESCRICAO]


# ── 2 · main delega, e so ────────────────────────────────────────────────────

def test_o_corpo_de_main_e_UMA_delegacao():
    """Planilha, cofre, Gemini, checkpoint e artefato ja estao compostos em
    `executar_no_save`. Repeti-los aqui criaria dois lugares para a mesma
    composicao — e o segundo envelheceria calado."""
    corpo = _sem_docstring(_funcao("main").body)

    assert len(corpo) == 1
    retorno = corpo[0]
    assert isinstance(retorno, ast.Return)
    chamada = retorno.value
    assert isinstance(chamada, ast.Call)
    assert isinstance(chamada.func, ast.Name)
    assert chamada.func.id == "executar_no_save"
    assert len(chamada.args) == 1
    assert isinstance(chamada.args[0], ast.Name)
    assert chamada.args[0].id == "ctx"
    assert chamada.keywords == []


@precisa_do_sdk
def test_main_chama_a_boundary_UMA_vez_e_devolve_o_que_ela_devolve(monkeypatch):
    chamadas = []
    resultado = {"ok": True}

    def boundary(*args, **kwargs):
        chamadas.append((args, kwargs))
        return resultado

    def proibido(nome):
        def recusar(*_a, **_k):
            raise AssertionError(f"main chamou {nome} por fora da boundary")
        return recusar

    monkeypatch.setattr(runner, "executar_no_save", boundary)
    monkeypatch.setattr(runner, "executar", proibido("executar"))
    monkeypatch.setattr(runner, "validar_recurso", proibido("validar_recurso"))
    monkeypatch.setattr(runner, "CertificadosDoCofre", proibido("CertificadosDoCofre"))
    monkeypatch.setattr(runner.app, "executar", proibido("app.executar"))
    monkeypatch.setattr(runner.espaco_de_trabalho, "abrir", proibido("abrir"))
    monkeypatch.setattr(runner.app, "aliases_necessarios",
                        proibido("app.aliases_necessarios"))

    # Um `ctx` que nao responde a nada: se `main` pedisse planilha, segredo ou
    # certificado por conta propria, isto levantaria.
    ctx = object()

    assert runner.main(ctx) is resultado
    assert chamadas == [((ctx,), {})]


# ── 3 · como script, `autohub.run(main)`, e uma vez so ───────────────────────

def test_o_bloco_de_script_e_so_autohub_run_main():
    blocos = [n for n in _arvore().body if _e_o_bloco_de_script(n)]
    assert len(blocos) == 1

    bloco = blocos[0]
    assert bloco.orelse == []
    assert len(bloco.body) == 1
    instrucao = bloco.body[0]
    assert isinstance(instrucao, ast.Expr)
    chamada = instrucao.value
    assert isinstance(chamada, ast.Call)
    assert _e_atributo(chamada.func, "autohub", "run")
    assert len(chamada.args) == 1
    assert isinstance(chamada.args[0], ast.Name)
    assert chamada.args[0].id == "main"
    assert chamada.keywords == []


def test_nada_executa_ao_IMPORTAR_o_runner():
    """Uma chamada solta no nivel do modulo rodaria a automacao ao importar o
    arquivo: no describe do publish e em todo teste que importa o runner.

    `autohub.run` aparece uma vez no arquivo inteiro, `main` nunca e chamada
    diretamente, e `executar_no_save` so e chamada de dentro de `main`.
    """
    arvore = _arvore()

    assert len(_chamadas(arvore, "run")) == 1
    assert _chamadas(arvore, "main") == []
    assert len(_chamadas(arvore, "executar_no_save")) == 1
    assert len(_chamadas(_funcao("main"), "executar_no_save")) == 1

    soltas = [no for no in arvore.body
              if not isinstance(no, ast.FunctionDef) and not _e_o_bloco_de_script(no)
              for nome in ("run", "main", "executar_no_save", "executar")
              if _chamadas(no, nome)]
    assert soltas == []


@precisa_do_sdk
def test_como_script_o_sdk_recebe_main_UMA_vez(monkeypatch):
    recebidas = []
    monkeypatch.setattr(autohub_sdk, "run", recebidas.append)

    runpy.run_path(str(RUNNER), run_name="__main__")

    assert len(recebidas) == 1
    tarefa = recebidas[0]
    assert tarefa.__name__ == "main"
    assert tarefa.__autohub__["id"] == ID_DA_TASK


@precisa_do_sdk
def test_importado_como_modulo_o_sdk_nao_e_acionado(monkeypatch):
    recebidas = []
    monkeypatch.setattr(autohub_sdk, "run", recebidas.append)

    runpy.run_path(str(RUNNER), run_name="runner_importado")

    assert recebidas == []


@precisa_do_sdk
def test_disparado_como_o_agente_dispara_o_sdk_real_descreve_e_nao_roda_nada():
    """`python -u runner.py` na raiz, com o SDK por `PYTHONPATH`.

    `AUTOHUB_DESCRIBE=1` e o modo do publish: o `autohub.run` real emite o schema
    e retorna sem chamar `main`. UMA linha de describe prova UMA chamada; a
    ausencia da linha de resultado prova que a automacao nao rodou. E o stdout e
    o canal NDJSON do agente — nada mais pode aparecer nele.
    """
    processo = _como_o_agente("-u", "runner.py", AUTOHUB_DESCRIBE="1")

    assert processo.returncode == 0, processo.stderr[-2000:]
    assert _ndjson(processo.stdout) == [DESCRICAO]


# ── 4 · ninguem precisa responder nada ───────────────────────────────────────

def _caminho_da_plataforma():
    return [RUNNER, RAIZ / "certificados_do_cofre.py",
            *sorted((RAIZ / "automation").rglob("*.py"))]


def test_o_caminho_da_plataforma_nao_pede_nada_ao_operador():
    """A execucao roda sem ninguem diante dela: um `input()` ficaria esperando
    quem nao existe. A prova pelo comportamento — `main` ate a aplicacao, com
    `input` recusando — esta em `test_runner_save.py`."""
    for caminho in _caminho_da_plataforma():
        pedidos = [n for n in ast.walk(_arvore(caminho))
                   if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                       and n.func.id == "input")
                   or (isinstance(n, ast.Attribute) and n.attr == "stdin")]
        assert pedidos == [], caminho.name
        assert not (_modulos(caminho) & INTERACAO_MANUAL), caminho.name


# ── 5 · o SDK mora no runner, e so nele ──────────────────────────────────────

def test_automation_nao_importa_o_sdk():
    for caminho in sorted((RAIZ / "automation").rglob("*.py")):
        assert not (_modulos(caminho) & NOMES_DO_SDK), caminho.name


def test_o_runner_e_o_UNICO_modulo_do_projeto_que_importa_o_sdk():
    importadores = sorted(caminho.relative_to(RAIZ).as_posix()
                          for caminho in _modulos_do_projeto()
                          if _modulos(caminho) & NOMES_DO_SDK)

    assert importadores == ["runner.py"]


# ── 6 · o Certificate Store nao entra pelo entrypoint ────────────────────────

def test_o_runner_nao_importa_o_certificate_store():
    """No caminho da plataforma o certificado vem do cofre, como arquivo. O
    provedor que de fato chega a aplicacao e conferido em
    `test_runner_save.py`."""
    assert not (_modulos(RUNNER) & CERTIFICATE_STORE)
