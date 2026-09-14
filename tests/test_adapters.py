"""Os dois entrypoints: `runner.py` (plataforma) e `local.py` (maquina).

Sao adapters IRMAOS. Cada um resolve os parametros e a config a seu modo e
chama a MESMA `automation.app.executar`. Nenhum importa o outro, e nenhum
contem regra.

Nenhum teste abre navegador, portal, Gemini, registro ou plataforma. Chaves,
CNPJs e caminhos ficticios.
"""
import ast
import pathlib

import pytest
from planilhas_sinteticas import criar_planilha

import local
import runner
from automation import app, apresentacao_eventos, eventos
from automation.boundary import EntradaInvalida
from automation.captcha import ConfiguracaoInvalida
from automation.eventos import EventoOperacional
from automation.planilha import PlanilhaIndisponivel

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CHAVE = "AIzaSy-SENTINELA-FICTICIA-0000"
CHAVE_DO_ENV = "AIzaSy-SENTINELA-DO-ARQUIVO-1111"


@pytest.fixture
def planilha(tmp_path):
    return str(criar_planilha(tmp_path / "base.xlsx"))


@pytest.fixture
def app_espiao(monkeypatch):
    """Captura o que chegou a `app.executar`, sem executar nada."""
    chamadas = []

    def executar(entrada, config_captcha, emitir_evento=None,
                 provedor_de_certificados=None, diretorio_da_execucao=None):
        chamadas.append({"entrada": entrada, "config": config_captcha,
                         "emissor": emitir_evento})

    monkeypatch.setattr(app, "executar", executar)
    monkeypatch.setattr(runner.app, "executar", executar)
    monkeypatch.setattr(local.app, "executar", executar)
    return chamadas


# ── §30 · runner ──────────────────────────────────────────────────────────────

def test_runner_traduz_o_disparo_em_entrada_e_config(app_espiao, planilha, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)

    resultado = runner.executar({"planilha": planilha})

    (chamada,) = app_espiao
    assert chamada["entrada"].planilha == planilha
    assert chamada["config"].api_key == CHAVE
    assert resultado == {"ok": True}


def test_runner_rejeita_parametro_desconhecido(app_espiao, planilha, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)

    with pytest.raises(EntradaInvalida):
        runner.executar({"planilha": planilha, "gemini_key": CHAVE})

    assert app_espiao == [], "nada foi executado"


def test_runner_rejeita_planilha_inexistente(app_espiao, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)

    with pytest.raises(PlanilhaIndisponivel):
        runner.executar({"planilha": "C:/nao/existe/base.xlsx"})

    assert app_espiao == []


def test_runner_sem_segredo_recusa_antes_de_executar(app_espiao, planilha, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ConfiguracaoInvalida) as erro:
        runner.executar({"planilha": planilha})

    assert app_espiao == []
    assert "SENTINELA" not in str(erro.value)


def test_runner_nao_transforma_falha_fatal_em_sucesso(planilha, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)
    monkeypatch.setattr(
        runner.app, "executar",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("o portal caiu")),
    )

    with pytest.raises(RuntimeError, match="o portal caiu"):
        runner.executar({"planilha": planilha})


def test_runner_reporta_aborto_por_certificado(planilha, monkeypatch, capsys):
    """`executar` devolve None de proposito. O `ok` vem do que o apresentador
    OBSERVOU passar pelo seam, e nao de um resumo inventado."""
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)

    def executar(entrada, config, emitir_evento=None,
                 provedor_de_certificados=None, diretorio_da_execucao=None):
        emitir_evento(EventoOperacional(eventos.CERTIFICADOS_INDISPONIVEIS))

    monkeypatch.setattr(runner.app, "executar", executar)

    assert runner.executar({"planilha": planilha}) == {"ok": False}
    assert "certificado" in capsys.readouterr().out.lower()


def test_runner_apresenta_eventos_em_tempo_real(planilha, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)

    def executar(entrada, config, emitir_evento=None,
                 provedor_de_certificados=None, diretorio_da_execucao=None):
        emitir_evento(EventoOperacional(eventos.ITEM_INICIADO, posicao=0, total=3))
        assert "1/3" in capsys.readouterr().out, "saiu ANTES do fim da execução"

    monkeypatch.setattr(runner.app, "executar", executar)

    runner.executar({"planilha": planilha})


# ── §31 · local ───────────────────────────────────────────────────────────────

def test_local_com_planilha_valida(app_espiao, planilha, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)

    assert local.main(["--planilha", planilha]) == 0

    (chamada,) = app_espiao
    assert chamada["entrada"].planilha == planilha
    assert chamada["config"].api_key == CHAVE
    assert "concluído" in capsys.readouterr().out


def test_local_le_o_segredo_do_env_do_operador(app_espiao, planilha, monkeypatch, tmp_path):
    """§9: o `.env` que o OPERADOR preparou continua sendo fonte local."""
    (tmp_path / ".env").write_text(f"GEMINI_API_KEY={CHAVE_DO_ENV}\n", encoding="utf-8")
    monkeypatch.setattr(local, "RAIZ", tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    local.main(["--planilha", planilha])

    assert app_espiao[0]["config"].api_key == CHAVE_DO_ENV


def test_local_prefere_o_ambiente_ao_arquivo(app_espiao, planilha, monkeypatch, tmp_path):
    """Trocar a chave numa maquina sem mexer em arquivo."""
    (tmp_path / ".env").write_text(f"GEMINI_API_KEY={CHAVE_DO_ENV}\n", encoding="utf-8")
    monkeypatch.setattr(local, "RAIZ", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)

    local.main(["--planilha", planilha])

    assert app_espiao[0]["config"].api_key == CHAVE


def test_local_sem_segredo_sai_com_codigo_2(app_espiao, planilha, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(local, "RAIZ", tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert local.main(["--planilha", planilha]) == 2
    assert app_espiao == []

    saida = capsys.readouterr()
    assert "configuração inválida" in saida.err
    assert "SENTINELA" not in saida.err + saida.out


def test_local_com_planilha_invalida_sai_com_codigo_2(app_espiao, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)

    assert local.main(["--planilha", "C:/nao/existe/base.xlsx"]) == 2
    assert app_espiao == []
    assert "entrada inválida" in capsys.readouterr().err


def test_local_com_extensao_recusada_sai_com_codigo_2(app_espiao, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)
    csv = tmp_path / "base.csv"
    csv.write_text("a,b\n", encoding="utf-8")

    assert local.main(["--planilha", str(csv)]) == 2
    assert app_espiao == []
    assert "entrada inválida" in capsys.readouterr().err


def test_local_reporta_aborto_com_codigo_1(planilha, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)
    monkeypatch.setattr(
        local.app, "executar",
        lambda e, c, emitir_evento=None: emitir_evento(
            EventoOperacional(eventos.CERTIFICADOS_INDISPONIVEIS)
        ),
    )

    assert local.main(["--planilha", planilha]) == 1


def test_local_nao_transforma_falha_fatal_em_sucesso(planilha, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)
    monkeypatch.setattr(local.app, "executar",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("o portal caiu")))

    with pytest.raises(RuntimeError, match="o portal caiu"):
        local.main(["--planilha", planilha])


def test_local_grava_o_log_com_os_eventos(planilha, monkeypatch, tmp_path):
    """§25: `--log` e LOCAL_OBSERVABILITY_ADAPTER. O app nao sabe que existe."""
    destino = tmp_path / "execucao.log"
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)
    monkeypatch.setattr(
        local.app, "executar",
        lambda e, c, emitir_evento=None: emitir_evento(
            EventoOperacional(eventos.LOGIN_CONCLUIDO)
        ),
    )
    original = local.sys.stdout
    try:
        local.main(["--planilha", planilha, "--log", str(destino)])
    finally:
        local.sys.stdout, local.sys.stderr = original, local.sys.stderr

    gravado = destino.read_text(encoding="utf-8")
    assert "Login no portal concluído" in gravado
    assert CHAVE not in gravado, "a chave nunca vai para o log"


def test_a_chave_nao_e_argumento_de_linha_de_comando():
    """§8: o que passa por argv aparece no historico do shell e na lista de
    processos. A chave nao entra por ai."""
    arvore = ast.parse((RAIZ / "local.py").read_text(encoding="utf-8"))
    flags = [
        no.args[0].value for no in ast.walk(arvore)
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute)
        and no.func.attr == "add_argument" and no.args
        and isinstance(no.args[0], ast.Constant)
    ]

    assert flags == ["--planilha", "--log"]


# ── §12 · a prova do segredo ──────────────────────────────────────────────────

def test_o_runner_nao_le_env_nem_arquivo_embutido():
    """§6: no runtime da plataforma nao ha `.env` nem bundle, e depender de um
    deles tornaria a execucao remota diferente da local por um motivo
    invisivel."""
    codigo = _codigo_sem_docstrings(RAIZ / "runner.py")

    for proibido in ("'.env'", '".env"', "load_dotenv", "_MEIPASS",
                     "chave_gemini", "read_text", "open("):
        assert proibido not in codigo, f"o runner toca em {proibido}"

    # `os.environ` e a fonte legitima aqui — e ela contem ".env" como substring,
    # o que ja reprovou este teste uma vez.
    assert "os.environ.get('GEMINI_API_KEY'" in codigo


@pytest.mark.parametrize("arquivo", ["runner.py", "local.py"])
def test_os_adapters_nao_escrevem_o_segredo(arquivo):
    """§0B: o operador fornecer um `.env` e uma coisa; a automacao criar um e
    outra. Nenhum dos dois entrypoints novos escreve."""
    fonte = (RAIZ / arquivo).read_text(encoding="utf-8")

    for proibido in ("write_text", "os.environ[", "setenv"):
        assert proibido not in fonte


def test_nenhum_arquivo_novo_guarda_a_chave_depois_de_executar(
    app_espiao, planilha, monkeypatch, tmp_path
):
    """§12 I: uma execucao sintetica inteira nao deixa a chave em disco."""
    monkeypatch.setattr(local, "RAIZ", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)
    antes = set(tmp_path.rglob("*"))

    local.main(["--planilha", planilha])

    for caminho in set(tmp_path.rglob("*")) - antes:
        if caminho.is_file():
            assert CHAVE not in caminho.read_text(encoding="utf-8", errors="ignore")


def test_o_app_roda_com_o_ambiente_limpo(monkeypatch, planilha):
    """§12 F: sem GEMINI_API_KEY no ambiente, o caminho novo funciona igual —
    porque a chave viaja por parametro desde a fronteira ate o solver."""
    import resolvedor_captcha
    from automation import captcha
    from automation.captcha import ConfigCaptcha

    recebidas = []
    monkeypatch.setattr(resolvedor_captcha, "solve_hcaptcha",
                        lambda page, api_key=None: recebidas.append(api_key) or True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    captcha.resolver(object(), ConfigCaptcha(api_key=CHAVE))

    assert recebidas == [CHAVE]


@pytest.mark.parametrize("codigo", sorted(eventos.CODIGOS))
def test_nenhuma_frase_carrega_a_chave(codigo):
    """§12 H: nem o renderer inventa o que o evento nao carrega."""
    evento = EventoOperacional(codigo, posicao=0, total=9, tentativa=1, maximo=2,
                               quantidade=3, paginas=1, tipo_da_falha="PermissionError")

    assert "AIzaSy" not in (apresentacao_eventos.frase(evento) or "")


# ── §22 · §23 · §32 · adapters irmaos ─────────────────────────────────────────

def _codigo_sem_docstrings(caminho) -> str:
    """A prosa explica o que NAO esta no arquivo; ela nao pode reprovar o arquivo.

    Sem isto, um docstring que diz "aqui nao entra laco de CNPJ" faz o proprio
    teste falhar por conter "CNPJ". Ja aconteceu tres vezes neste projeto.
    """
    arvore = ast.parse(caminho.read_text(encoding="utf-8-sig"))
    for no in ast.walk(arvore):
        corpo = getattr(no, "body", None)
        # `body` de um lambda e uma EXPRESSAO, e nao uma lista — indexa-lo
        # levanta TypeError. So blocos entram aqui.
        if (isinstance(corpo, list) and corpo and isinstance(corpo[0], ast.Expr)
                and isinstance(corpo[0].value, ast.Constant)
                and isinstance(corpo[0].value.value, str)):
            corpo.pop(0)
            if not corpo:
                corpo.append(ast.Pass())
    return ast.unparse(arvore)


def _importados(caminho):
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes.add(no.module.split(".")[0])
    return nomes


@pytest.mark.parametrize("proibido", [
    "main", "tkinter", "pandas", "openpyxl", "patchright", "winreg", "ctypes",
    "subprocess", "servicos_rf_login", "resolvedor_captcha", "cert_windows",
    "ui_upload", "dotenv",
])
def test_o_runner_nao_importa_tecnologia_nem_o_legado(proibido):
    assert proibido not in _importados(RAIZ / "runner.py")


def test_o_runner_nao_importa_o_local():
    assert "local" not in _importados(RAIZ / "runner.py")


def test_o_local_nao_importa_o_runner_nem_o_main():
    """§23: se o local dependesse do adapter da plataforma, rodar na sua maquina
    passaria a depender da plataforma."""
    usados = _importados(RAIZ / "local.py")

    assert "runner" not in usados
    assert "main" not in usados


@pytest.mark.parametrize("proibido", [
    "tkinter", "pandas", "openpyxl", "patchright", "winreg", "ctypes",
    "subprocess", "servicos_rf_login", "resolvedor_captcha", "cert_windows",
])
def test_o_local_nao_importa_tecnologia(proibido):
    assert proibido not in _importados(RAIZ / "local.py")


@pytest.mark.parametrize("arquivo", ["runner.py", "local.py"])
def test_os_adapters_nao_contem_regra(arquivo):
    """Nem laco de CNPJ, nem seletor, nem retry, nem coluna de planilha.

    `certificado` saiu da lista, e a razao e precisa: COMPOR o provedor de
    certificados e trabalho de adapter — e a unica coisa que muda entre rodar na
    plataforma e rodar no desktop. O que continua proibido e DECIDIR sobre
    certificado, e isso e o que as outras palavras guardam: `policy` barra a
    configuracao do Windows, e `cnpj` barra a selecao por linha. Nenhuma regra
    de certificado cabe num adapter sem tropecar nelas.
    """
    codigo = _codigo_sem_docstrings(RAIZ / arquivo).lower()

    for regra in ("xpath", "locator", "cnpj", "policy", "retry",
                  "retentativa", "col_", "aba_", "iterrows", "while "):
        assert regra not in codigo, f"{arquivo} contém '{regra}'"


@pytest.mark.parametrize("arquivo", ["runner.py", "local.py"])
def test_ambos_chamam_o_app_diretamente(arquivo):
    fonte = (RAIZ / arquivo).read_text(encoding="utf-8")

    assert "app.executar(" in fonte


def test_os_dois_adapters_chegam_ao_mesmo_app(app_espiao, planilha, monkeypatch):
    """§32: mesma entrada e mesma config produzem a MESMA chamada. A diferenca
    esta so na origem dos parametros e na apresentacao."""
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)

    runner.executar({"planilha": planilha})
    local.main(["--planilha", planilha])

    do_runner, do_local = app_espiao
    assert do_runner["entrada"] == do_local["entrada"]
    assert do_runner["config"] == do_local["config"]


# ── §4 · a boundary nao cresceu ───────────────────────────────────────────────

def test_o_input_de_execucao_continua_sendo_so_a_planilha():
    from automation.boundary import CAMPOS_CONHECIDOS

    assert CAMPOS_CONHECIDOS == {"planilha"}


@pytest.mark.parametrize("campo", ["log", "gemini_api_key", "certificado", "cnpj",
                                   "profile", "timeout", "url"])
def test_a_boundary_recusa_o_que_nao_e_input_de_execucao(campo, planilha):
    from automation.boundary import montar_entrada

    with pytest.raises(EntradaInvalida):
        montar_entrada({"planilha": planilha, campo: "x"})


# ── §14 · §17 · o renderer e um so ────────────────────────────────────────────

def test_as_frases_moram_num_lugar_so():
    """Tres implementacoes das mesmas frases seriam tres oportunidades de uma
    delas vazar algo que as outras nao vazam."""
    for arquivo in ("runner.py", "local.py", "main.py"):
        fonte = (RAIZ / arquivo).read_text(encoding="utf-8-sig")
        assert "def frase(" not in fonte
        assert "def _frase(" not in fonte
        assert "apresentacao_eventos" in fonte


def test_o_apresentador_nao_imprime_por_conta_propria():
    """O modulo traduz e devolve texto; quem imprime e o adapter."""
    fonte = (RAIZ / "automation" / "apresentacao_eventos.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)

    prints = [n for n in ast.walk(arvore)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "print"]
    assert prints == [], "print só como DEFAULT injetável, nunca chamado direto"
    assert "escrever=print" in fonte


# ── §33 · o contrato de plataforma ────────────────────────────────────────────

def test_runner_na_raiz_do_codigo():
    assert (RAIZ / "runner.py").is_file()


def test_a_automacao_roda_fora_da_plataforma():
    assert (RAIZ / "local.py").is_file()
    assert "if __name__" in (RAIZ / "local.py").read_text(encoding="utf-8")


def test_so_o_runner_conhece_o_sdk_da_plataforma():
    """O decorator saiu do comentario quando o contrato da task foi comprovado
    (D8.1-A) — e saiu so no runner. O `local.py` roda numa maquina sem
    plataforma nenhuma: se importasse o SDK, deixaria de rodar ali. O contrato
    da task em si e conferido em `test_entrypoint_save.py`."""
    assert "autohub_sdk" in _importados(RAIZ / "runner.py")
    assert not ({"autohub_sdk", "autohub"} & _importados(RAIZ / "local.py"))


def test_o_manifest_de_runtime_existe():
    assert (RAIZ / "requirements.txt").is_file()


# ── §19 · §20 · o guardião e o main legado ────────────────────────────────────

def test_o_local_nao_precisa_conhecer_o_guardiao():
    """§19, respondido pelo codigo do guardiao e nao por suposicao.

    Fora do executavel congelado, `_guard_args` relanca `cert_windows.py` — que
    tem o proprio dispatch de `--guard`. Dentro do .exe, o programa relancado e o
    proprio .exe, e o dispatch esta no `main.py` legado. Nenhum dos dois caminhos
    passa por `local.py`.
    """
    guardiao = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")

    assert 'return [f\'"{Path(__file__).resolve()}"\'] + extra' in guardiao
    assert 'if len(sys.argv) >= 4 and sys.argv[1] == "--guard":' in guardiao

    assert "--guard" not in (RAIZ / "local.py").read_text(encoding="utf-8").replace(
        "O modo `--guard` NÃO passa por este arquivo.", ""
    ).split('"""')[2]


def test_o_runner_nao_trata_guard_como_parametro():
    """§19: `--guard` e INTERNAL_GUARD_ENTRYPOINT, e nunca input de execucao."""
    assert "guard" not in _codigo_sem_docstrings(RAIZ / "runner.py").lower()


def test_o_main_legado_mantem_o_dispatch_do_guardiao():
    """§20: transformar `main.py` num alias de `local.py` quebraria o .exe."""
    fonte = (RAIZ / "main.py").read_text(encoding="utf-8-sig")

    assert 'sys.argv[1] == "--guard"' in fonte
    assert "selecionar_planilha" in fonte, "a UI desktop continua"


def test_o_main_legado_esta_marcado_como_legado():
    fonte = (RAIZ / "main.py").read_text(encoding="utf-8-sig")

    assert "LEGACY_DESKTOP_ENTRYPOINT" in fonte
