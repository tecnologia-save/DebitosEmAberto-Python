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

def test_1_o_spec_le_o_env_do_operador_no_momento_do_build():
    """O literal NAO esta no repositorio: o `.spec` le o `.env` (gitignored) no
    build. O que existe e o MECANISMO, nao a chave."""
    fonte = SPEC.read_text(encoding="utf-8")

    assert "_env_origem = DEBITOS_DIR / '.env'" in fonte
    assert "startswith('GEMINI_API_KEY=')" in fonte


def test_1_e_grava_um_arquivo_que_entra_no_bundle():
    fonte = SPEC.read_text(encoding="utf-8")

    assert "_chave_arquivo = DEBITOS_DIR / 'build' / 'chave_gemini.env'" in fonte
    assert "_chave_arquivo.write_text(f'GEMINI_API_KEY={_chave}" in fonte
    assert "(str(_chave_arquivo), '.')" in fonte, "e vai para `datas`"


def test_1_o_build_imprime_um_pedaco_da_chave():
    """Proibicao permanente do projeto: nem prefixo nem sufixo da API key. O
    console do build viola isso."""
    fonte = SPEC.read_text(encoding="utf-8")

    assert "{_chave[:6]}...{_chave[-4:]}" in fonte


def test_1_main_le_o_bundle_como_ULTIMO_fallback():
    import main

    fonte = inspect.getsource(main._resolver_gemini_key)

    assert 'getattr(sys, "_MEIPASS", None)' in fonte
    assert "_CHAVE_EMBUTIDA" in fonte
    ordem = [fonte.index(m) for m in ("os.environ.get", "env_local", "_MEIPASS")]
    assert ordem == sorted(ordem), "ambiente, .env do lado, e so entao o bundle"


def test_1_sem_configuracao_externa_o_exe_ainda_funciona_pelo_bundle(
    tmp_path, monkeypatch
):
    """LEGACY_EMBEDDED_SECRET, medido: nada configurado, e mesmo assim ha chave."""
    import main

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main, "LOGIN_ECAC_DIR", tmp_path)
    embutido = tmp_path / "bundle"
    embutido.mkdir()
    (embutido / main._CHAVE_EMBUTIDA).write_text(
        f"GEMINI_API_KEY={CHAVE_FICTICIA}\n", encoding="utf-8"
    )
    monkeypatch.setattr(main.sys, "_MEIPASS", str(embutido), raising=False)

    chave, origem = main._resolver_gemini_key()

    assert chave == CHAVE_FICTICIA
    assert "embutida" in origem


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

def test_10_o_guardiao_grava_um_log_ao_lado_do_modulo():
    import cert_windows

    fonte = inspect.getsource(cert_windows.guardiao)

    assert '_glog = Path(__file__).parent / "_guard_log.txt"' in fonte
    assert 'open(_glog, "a"' in fonte, "acumula, nao sobrescreve"


def test_10_e_ele_registra_o_CN(registro_de_guardiao):
    """SENSITIVE_PERSISTENT_DIAGNOSTIC: `diagnostico()` devolve
    `HKCU=<CN>  HKLM=<CN>`, e isso vai para o arquivo."""
    import cert_windows

    fonte = inspect.getsource(cert_windows.guardiao)

    assert 'policy escrita={escrita} | {diagnostico()}' in fonte

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


def test_10_e_o_pid(registro_de_guardiao):
    import cert_windows

    assert 'guardiao start pid={pid}' in inspect.getsource(cert_windows.guardiao)


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


def test_10_o_dispatch_do_guard_tambem_grava_um_erro():
    import cert_windows

    fonte = Path(cert_windows.__file__).read_text(encoding="utf-8")
    trecho = fonte[fonte.index('sys.argv[1] == "--guard"'):]

    assert '_wincert_erro.log' in trecho[:600]
    assert "type(e).__name__" in trecho[:600]


@pytest.fixture
def registro_de_guardiao():
    """Nao ha o que montar: estes testes leem a FONTE, e nao o disco."""
    return None


# ── §8 · §9 · os screenshots do login ────────────────────────────────────────

LOGIN = RAIZ / "servicos_rf_login" / "login.py"


def test_8_o_login_persiste_tres_screenshots():
    fonte = LOGIN.read_text(encoding="utf-8")

    for nome in ("_debug_govbr_btn.png", "_debug_cert_button.png",
                 "_debug_pos_cert.png"):
        assert f'"{nome}"' in fonte, nome
    assert fonte.count("page.screenshot(path=shot, full_page=True)") == 3


def test_8_todos_em_caminhos_de_FALHA_e_nao_no_caminho_normal():
    """Nao sao capturados a cada execucao: so quando um botao nao aparece ou o
    redirecionamento nao acontece."""
    fonte = LOGIN.read_text(encoding="utf-8")

    for nome in ("_debug_govbr_btn.png", "_debug_cert_button.png",
                 "_debug_pos_cert.png"):
        antes = fonte[: fonte.index(nome)]
        assert "registrar_erro(" in antes[-1200:], f"{nome} vem depois de um erro"


def test_9_o_pos_cert_e_capturado_DEPOIS_da_selecao_do_certificado():
    """E o que o torna um screenshot possivelmente AUTENTICADO: o certificado ja
    foi escolhido, e o que falhou foi o redirecionamento."""
    fonte = LOGIN.read_text(encoding="utf-8")
    trecho = fonte[: fonte.index("_debug_pos_cert.png")]

    assert "_clicar_certificado(page)" in trecho
    assert "_ja_logado(page)" in trecho[-2000:]


def test_9_nenhum_fluxo_funcional_depende_dos_screenshots():
    """Sao diagnostico: capturados dentro de `try/except: pass`, e o valor nao e
    devolvido nem lido por ninguem."""
    fonte = LOGIN.read_text(encoding="utf-8")

    for nome in ("_debug_govbr_btn.png", "_debug_cert_button.png",
                 "_debug_pos_cert.png"):
        depois = fonte[fonte.index(nome):][:400]
        assert "except Exception:\n                    pass" in depois or \
               "except Exception:\n            pass" in depois

    vivos = [*(RAIZ / "automation").glob("*.py"), RAIZ / "main.py"]
    for caminho in vivos:
        assert "_debug_" not in caminho.read_text(encoding="utf-8-sig")


# ── a URL autenticada em disco ───────────────────────────────────────────────

def test_a_url_do_portal_vai_para_um_log_em_disco():
    """Mais grave que o screenshot, e no mesmo ponto: `registrar_erro` grava
    `page.url` num arquivo diario, e essa URL e a do portal ja autenticado."""
    fonte = LOGIN.read_text(encoding="utf-8")

    assert 'f"Login: redirecionamento não ocorreu. URL atual: {page.url}"' in fonte

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
