"""Segredo embutido no binario e diagnosticos que ficam em disco.

Escrito ANTES de qualquer mudanca da fatia 13B.

Nenhum teste chama o Gemini, valida credencial, le o `.env` do operador, abre
Chrome, pede UAC ou toca o registro real. Nenhuma credencial real entra aqui:
so sentinelas ficticias, e a estrutura e verificada pela FORMA, nunca pelo
valor.
"""
import inspect
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SPEC = RAIZ / "debitos_em_aberto.spec"

# Sentinela. Nao e credencial, nao se parece com uma, e nao vai a lugar nenhum.
CHAVE_FICTICIA = "chave-ficticia-para-teste"
CN_FICTICIO = "ALFA FICTICIA LTDA:11111111000191"


# ── §1 · o caminho do segredo embutido ───────────────────────────────────────

def test_1_o_spec_NAO_le_mais_o_env_do_operador():
    """ANTES o `.spec` lia a chave do `.env` (gitignored) no momento do build.
    O literal nunca esteve no repositorio; o que existia era o MECANISMO."""
    fonte = SPEC.read_text(encoding="utf-8")

    assert "GEMINI_API_KEY" not in fonte
    assert "_env_origem" not in fonte


def test_1_e_nao_grava_mais_arquivo_de_chave_nem_o_injeta():
    """ANTES gravava `build/chave_gemini.env` e o punha em `datas`."""
    fonte = SPEC.read_text(encoding="utf-8")

    assert "chave_gemini" not in fonte
    assert "_chave" not in fonte


def test_1_o_build_nao_imprime_mais_pedaco_nenhum_da_chave():
    """ANTES o console do build mostrava prefixo e sufixo — violando a proibicao
    permanente do projeto sobre a API key."""
    fonte = SPEC.read_text(encoding="utf-8")

    assert "[:6]" not in fonte and "[-4:]" not in fonte


def test_1_main_NAO_le_mais_o_bundle():
    """ANTES havia um terceiro fallback, depois do ambiente e do `.env`: a copia
    embutida no binario. Ele saiu inteiro."""
    import main

    fonte = inspect.getsource(main._resolver_gemini_key)
    corpo = fonte[fonte.rindex(chr(34) * 3) + 3:]

    assert "_MEIPASS" not in corpo
    assert "_CHAVE_EMBUTIDA" not in corpo
    ordem = [corpo.index(m) for m in ("os.environ.get", "env_local")]
    assert ordem == sorted(ordem), "ambiente, e depois o .env do lado"


def test_1_sem_configuracao_externa_o_exe_NAO_tem_mais_chave(
    tmp_path, monkeypatch
):
    """LEGACY_EMBEDDED_SECRET_REMOVAL_BEHAVIOR_CHANGE, medido.

    ANTES: nada configurado, e mesmo assim havia chave — a que viajou no
    binario. AGORA nao ha, e quem decide o que fazer com isso e o chamador.
    """
    import main

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main, "LOGIN_ECAC_DIR", tmp_path)
    embutido = tmp_path / "bundle"
    embutido.mkdir()
    (embutido / "chave_gemini.env").write_text(
        f"GEMINI_API_KEY={CHAVE_FICTICIA}\n", encoding="utf-8"
    )
    monkeypatch.setattr(main.sys, "_MEIPASS", str(embutido), raising=False)

    chave, origem = main._resolver_gemini_key()

    assert (chave, origem) == ("", "nenhuma")


def test_1_e_sem_chave_o_desktop_legado_PARA(monkeypatch, capsys):
    """§3: falha segura de configuracao, e nao aviso. A mensagem e constante —
    sem valor parcial da chave, sem caminho de arquivo, sem bundle."""
    import main

    fonte = inspect.getsource(main)
    trecho = fonte[fonte.index("_chave, _origem = _resolver_gemini_key()"):]
    trecho = trecho[: trecho.index("renderer = Renderer()")]

    assert "sys.exit(5)" in trecho
    assert "Configuração ausente" in trecho
    for proibido in ("_chave[", "env_local", "_MEIPASS", "{_chave}"):
        assert proibido not in trecho


# ── §1 · e os entrypoints novos NAO dependem disso ───────────────────────────

@pytest.mark.parametrize("arquivo", ["runner.py", "local.py"])
def test_1_runner_e_local_nao_conhecem_o_bundle(arquivo):
    fonte = (RAIZ / arquivo).read_text(encoding="utf-8")

    assert "_MEIPASS" not in fonte
    assert "chave_gemini" not in fonte


def test_1_o_app_recebe_a_chave_por_parametro():
    from automation import app, captcha

    assert "api_key" in inspect.signature(captcha.ConfigCaptcha).parameters
    fonte = inspect.getsource(app)
    for proibido in ("_MEIPASS", "chave_gemini", "GEMINI_API_KEY"):
        assert proibido not in fonte


def test_1_secret_persisted_to_disk_continua_resolvido():
    """A fatia 10 tirou a escrita da chave no `.env`. Isso nao volta."""
    from automation import maquina

    fonte = inspect.getsource(maquina.preparar_ambiente_do_certificado)
    corpo = fonte[fonte.rindex(chr(34) * 3) + 3:]

    assert "GEMINI_API_KEY" not in corpo


# ── §10 · o log do guardiao ──────────────────────────────────────────────────

def test_10_o_guardiao_NAO_grava_mais_log_nenhum():
    """ANTES acumulava um arquivo ao lado do modulo, em modo append."""
    import cert_windows

    fonte = inspect.getsource(cert_windows.guardiao)

    assert "_glog" not in fonte
    assert "open(" not in fonte
    assert "_log(" not in fonte


def test_10_e_o_CN_nao_vai_mais_para_lugar_nenhum(registro_de_guardiao):
    """ANTES o guardiao registrava `diagnostico()`, que devolve o CN de cada
    colmeia — ou seja, o nome do cliente — num arquivo permanente."""
    import cert_windows

    fonte = inspect.getsource(cert_windows.guardiao)

    assert "diagnostico()" not in fonte

    # E `diagnostico()` continua devolvendo o CN: ela nao mudou, apenas deixou
    # de ser chamada por quem escrevia em disco.
    # E `diagnostico()` devolve o CN, comprovadamente — sobre o registro falso,
    # com um CN ficticio.
    from registro_falso import RegistroFalso

    falso = RegistroFalso()
    original_winreg, original_colmeias = cert_windows.winreg, cert_windows._COLMEIAS
    try:
        cert_windows.winreg = falso
        cert_windows._COLMEIAS = (("HKCU", "HKCU"), ("HKLM", "HKLM"))
        cert_windows.definir_autoselect(CN_FICTICIO)
        assert CN_FICTICIO in cert_windows.diagnostico()
    finally:
        cert_windows.winreg, cert_windows._COLMEIAS = original_winreg, original_colmeias


def test_10_e_o_pid_tampouco(registro_de_guardiao):
    """ANTES a primeira linha do log era `guardiao start pid=...`."""
    import cert_windows

    assert "pid=" not in inspect.getsource(cert_windows.guardiao)


def test_10_ninguem_le_esse_arquivo(registro_de_guardiao):
    """Nao ha consumidor funcional: o guardiao escreve e ninguem abre."""
    vivos = [p for p in RAIZ.glob("*.py")] + [
        p for p in (RAIZ / "automation").glob("*.py")
    ]
    for caminho in vivos:
        fonte = caminho.read_text(encoding="utf-8-sig")
        if "_guard_log" not in fonte:
            continue
        assert caminho.name == "cert_windows.py", "so quem escreve o menciona"
        assert "read_text" not in fonte.split("_guard_log")[1][:200]


def test_10_o_dispatch_do_guard_tambem_parou_de_gravar():
    """ANTES gravava um arquivo de erro com a mensagem da excecao, que pode
    carregar caminho de registro."""
    import cert_windows

    fonte = Path(cert_windows.__file__).read_text(encoding="utf-8")
    trecho = fonte[fonte.index('sys.argv[1] == "--guard"'):]

    assert "write_text" not in trecho
    assert "type(e).__name__" not in trecho


@pytest.fixture
def registro_de_guardiao():
    """Nao ha o que montar: estes testes leem a FONTE, e nao o disco."""
    return None


# ── §8 · §9 · os screenshots do login ────────────────────────────────────────

LOGIN = RAIZ / "servicos_rf_login" / "login.py"


def test_8_o_login_NAO_persiste_mais_screenshot_nenhum():
    """ANTES eram tres: gov.br, botao de certificado e pos-certificado."""
    fonte = LOGIN.read_text(encoding="utf-8")

    assert "screenshot" not in fonte
    assert fonte.count("full_page") == 0


def test_9_o_pos_cert_era_o_pior_e_por_isso_nada_e_capturado_ali():
    """ANTES ele era capturado DEPOIS da selecao do certificado — a pagina podia
    estar autenticada, e o que falhara era so o redirecionamento.

    AGORA aquele bloco nao captura nada: entre o `_clicar_certificado` e o
    retorno da falha nao ha `screenshot`.
    """
    fonte = LOGIN.read_text(encoding="utf-8")
    trecho = fonte[fonte.index("_clicar_certificado(page)"):]

    assert "screenshot" not in trecho


def test_9_e_nada_em_codigo_nosso_captura_tela():
    vivos = [*(RAIZ / "automation").glob("*.py"), RAIZ / "main.py"]
    for caminho in vivos:
        fonte = caminho.read_text(encoding="utf-8-sig")
        assert "_debug_" not in fonte
        assert "screenshot" not in fonte


# ── a URL autenticada em disco ───────────────────────────────────────────────

def test_a_url_do_portal_NAO_vai_mais_para_o_log_em_disco():
    """ANTES `registrar_erro` gravava `page.url` num arquivo diario, e essa URL
    era a do portal ja autenticado. A mensagem ficou; a URL saiu."""
    fonte = LOGIN.read_text(encoding="utf-8")

    assert "URL atual:" not in fonte
    assert 'registrar_erro("Login: redirecionamento não ocorreu.")' in fonte

    # §8: o sistema de log continua existindo, e serve as outras falhas.
    log = (RAIZ / "servicos_rf_login" / "log_manager.py").read_text(encoding="utf-8")
    assert 'open(log_dir / nome_arquivo, "a"' in log
    assert 'Path.cwd() / "logs"' in log


def test_a_o_app_so_manda_TIPO_de_falha_para_esse_log():
    """O nosso lado ja e seguro: `main.Renderer` registra o tipo da excecao, e
    nunca a mensagem — que carrega o caminho do arquivo."""
    import main

    fonte = inspect.getsource(main.Renderer)

    assert "evento.tipo_da_falha" in fonte
    assert "{erro}" not in fonte


# ── §13 · o inventario do caminho vivo ───────────────────────────────────────

def test_13_o_solver_tem_um_gravador_de_debug_que_ninguem_chama():
    """`_salvar_debug` existe em `resolvedor_captcha/solver.py` e nao e chamado
    de lugar nenhum: `debug_screenshots/` nunca e criado pelo caminho vivo."""
    fonte = (RAIZ / "resolvedor_captcha" / "solver.py").read_text(encoding="utf-8")

    assert "def _salvar_debug(" in fonte
    assert fonte.count("_salvar_debug(") == 1, "so a definicao"


def test_13_nada_sensivel_esta_rastreado_no_git():
    """O que existe em disco e local: nem chave, nem log, nem screenshot entrou
    no repositorio."""
    import subprocess

    saida = subprocess.run(
        ["git", "ls-files"],  # noqa: S607
        cwd=RAIZ, capture_output=True, text=True, check=True,
    ).stdout

    for proibido in ("_guard_log", "_debug_", "chave_gemini", ".env",
                     "_wincert_erro"):
        assert proibido not in saida, f"{proibido} esta rastreado"


# ── §21 L · a chave nao aparece em lugar nenhum ──────────────────────────────

def test_L_nenhum_evento_carrega_a_chave():
    """O seam de eventos e fechado: 34 codigos e oito campos, e nenhum deles e
    a chave nem nada que a componha."""
    from automation import eventos

    campos = set(eventos.EventoOperacional.__dataclass_fields__)

    assert "api_key" not in campos and "chave" not in campos
    fonte = inspect.getsource(eventos)
    for proibido in ("GEMINI", "api_key", "gemini"):
        assert proibido not in fonte


def test_L_o_apresentador_nao_tem_como_renderizar_a_chave():
    from automation import apresentacao_eventos

    fonte = inspect.getsource(apresentacao_eventos)
    # "chave privada" aparece numa frase sobre certificados — assertiva de texto
    # colidindo com prosa e o tropeco recorrente deste projeto. Marcadores do
    # que importa, entao: o nome do segredo e o do campo.
    for proibido in ("GEMINI", "api_key", "gemini"):
        assert proibido not in fonte


def test_L_o_main_so_imprime_a_ORIGEM_e_nunca_o_valor():
    """Quando ha chave, o console recebe de onde ela veio — e nada do valor."""
    import main

    fonte = inspect.getsource(main)
    trecho = fonte[fonte.index("_chave, _origem = _resolver_gemini_key()"):]
    trecho = trecho[: trecho.index("renderer = Renderer()")]

    assert "{_origem}" in trecho
    assert "{_chave" not in trecho, "nem o valor, nem uma fatia dele"


def test_L_o_solver_recebe_a_chave_por_parametro_e_nao_a_imprime():
    """O consumidor final: a chave desce por parametro desde a fatia 9B.1."""
    from automation import captcha

    fonte = inspect.getsource(captcha)

    assert "api_key" in fonte
    for proibido in ("print(api_key", "print(f\"{api_key", "api_key[:"):
        assert proibido not in fonte


# ── §21 M · o screenshot autenticado ─────────────────────────────────────────

def test_M_nenhum_screenshot_e_produzido_por_codigo_NOSSO():
    """Os tres screenshots do login vivem no fork `servicos_rf_login`, e o fork
    nao foi tocado nesta fatia. O que se prova aqui e a fronteira: nada em
    `automation/`, `runner.py`, `local.py`, `main.py` ou `cert_windows.py`
    captura tela.

    SENSITIVE_PERSISTENT_DIAGNOSTIC continua ABERTO por causa deles.
    """
    nossos = [
        *(RAIZ / "automation").glob("*.py"),
        RAIZ / "runner.py", RAIZ / "local.py", RAIZ / "main.py",
        RAIZ / "cert_windows.py",
    ]
    for caminho in nossos:
        fonte = caminho.read_text(encoding="utf-8-sig")
        assert "screenshot" not in fonte, caminho.name
        assert ".png" not in fonte, caminho.name


def test_M_o_fork_so_foi_tocado_para_SEGURANCA():
    """AUTHORIZED_FORK_SECURITY_EDIT (fatia 13B.1).

    A 13B parou nesta fronteira e reportou; a autorizacao veio explicita e
    apenas para diagnostico sensivel. O que entrou no fork foi remocao — e o
    que continua igual e o fluxo: retornos, retries, seletores, captcha,
    navegacao e o cleanup da 12B.1.
    """
    fonte = LOGIN.read_text(encoding="utf-8")

    assert "screenshot" not in fonte
    assert "URL atual:" not in fonte
    # O que NAO podia mudar, e nao mudou.
    assert "MAX_TENTATIVAS_CERT = 3" in fonte
    assert fonte.count("context.close()") == 7, "as sete saidas da 12B.1"
    assert fonte.count("p.stop()") == 7
