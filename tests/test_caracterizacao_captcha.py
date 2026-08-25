"""Caracterizacao do captcha COMO ELE E HOJE — so o caminho que DebitosEmAberto usa.

Nenhum teste chama Gemini, abre navegador, resolve captcha real ou usa chave
real. As fronteiras substituidas sao: o ambiente, o arquivo .env e a funcao
`solve_hcaptcha` do fork.

A chave usada nos testes e uma SENTINELA ficticia, escolhida para ser
reconhecivel se vazar para alguma mensagem.
"""
import os
import textwrap
from pathlib import Path

import pytest

import main
import resolvedor_captcha
from servicos_rf_login import login as login_rf

# Sentinela ficticia. Nao e chave de nada.
CHAVE = "AIzaSy-SENTINELA-FICTICIA-NAO-E-CHAVE-0000"
RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    yield


# ── E · de onde a chave vem hoje ──────────────────────────────────────────────

def test_e_precedencia_1_variavel_de_ambiente(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)

    chave, origem = main._resolver_gemini_key()

    assert chave == CHAVE
    assert origem == "variável de ambiente"


def test_e_precedencia_2_env_ao_lado_do_executavel(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(f"GEMINI_API_KEY={CHAVE}\n", encoding="utf-8")
    monkeypatch.setattr(main, "LOGIN_ECAC_DIR", tmp_path)

    chave, origem = main._resolver_gemini_key()

    assert chave == CHAVE
    assert ".env" in origem


def test_e_precedencia_3_chave_embutida_no_executavel(monkeypatch, tmp_path):
    """LEGACY_EMBEDDED_SECRET: no .exe congelado, a chave vem de dentro do bundle."""
    embutido = tmp_path / "bundle"
    embutido.mkdir()
    (embutido / main._CHAVE_EMBUTIDA).write_text(f"GEMINI_API_KEY={CHAVE}\n", encoding="utf-8")
    monkeypatch.setattr(main, "LOGIN_ECAC_DIR", tmp_path / "vazio")
    monkeypatch.setattr(main.sys, "_MEIPASS", str(embutido), raising=False)

    chave, origem = main._resolver_gemini_key()

    assert chave == CHAVE
    assert "embutida no executável" in origem


def test_e_ambiente_vence_o_env(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("GEMINI_API_KEY=do-arquivo\n", encoding="utf-8")
    monkeypatch.setattr(main, "LOGIN_ECAC_DIR", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", CHAVE)

    assert main._resolver_gemini_key()[0] == CHAVE


def test_e_sem_chave_em_lugar_nenhum(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "LOGIN_ECAC_DIR", tmp_path)
    monkeypatch.delattr(main.sys, "_MEIPASS", raising=False)

    assert main._resolver_gemini_key() == ("", "nenhuma")


@pytest.mark.parametrize(
    ("conteudo", "esperado"),
    [
        (f"GEMINI_API_KEY={CHAVE}", CHAVE),
        (f"  GEMINI_API_KEY={CHAVE}  ", CHAVE),
        (f"OUTRA=1\nGEMINI_API_KEY={CHAVE}\nMAIS=2", CHAVE),
        ("OUTRA=1", ""),
        ("", ""),
        ("GEMINI_API_KEY=", ""),
    ],
)
def test_e_leitura_do_arquivo_env(tmp_path, conteudo, esperado):
    arquivo = tmp_path / ".env"
    arquivo.write_text(conteudo, encoding="utf-8")

    assert main._ler_chave_de(arquivo) == esperado


def test_e_arquivo_inexistente_nao_levanta(tmp_path):
    assert main._ler_chave_de(tmp_path / "nao_existe.env") == ""


def test_e_a_chave_e_transportada_por_variavel_de_ambiente_global():
    """CAPTCHA_INTEGRATION_COUPLING: o fork le `os.environ` por conta propria.

    E o unico canal por onde o segredo chega ate ele — nao ha parametro.
    """
    fonte = (RAIZ / "resolvedor_captcha" / "solver.py").read_text(encoding="utf-8")

    assert 'os.environ.get("GEMINI_API_KEY", "")' in fonte
    assert "def solve_hcaptcha(page, max_rounds: int = 6) -> bool:" in fonte


# ── SENSITIVE_OUTPUT: o que o main imprime hoje ───────────────────────────────

def test_o_main_imprime_prefixo_e_sufixo_da_chave():
    """SENSITIVE_OUTPUT caracterizado: seis primeiros e quatro ultimos caracteres
    da chave vao para o console — e para o arquivo, quando `--log` esta ligado."""
    fonte = (RAIZ / "main.py").read_text(encoding="utf-8-sig")

    assert "_chave[:6]" in fonte
    assert "_chave[-4:]" in fonte


# ── B · a API realmente consumida do fork ─────────────────────────────────────

def test_b_a_api_publica_do_fork():
    assert resolvedor_captcha.__all__ == ["solve_hcaptcha", "solve_captcha", "cell_to_viewport"]


def test_b_so_solve_hcaptcha_e_consumido():
    """Dos tres nomes exportados, o projeto usa UM. `solve_captcha` e alias e
    `cell_to_viewport` nao tem nenhum consumidor fora do proprio fork."""
    consumidores = [RAIZ / "main.py", RAIZ / "servicos_rf_login" / "login.py"]
    for arquivo in consumidores:
        fonte = arquivo.read_text(encoding="utf-8-sig")
        assert "solve_hcaptcha" in fonte
        assert "cell_to_viewport" not in fonte
        # `_try_solve_captcha` contem a substring `solve_captcha`; o que importa e
        # que ninguem IMPORTA o alias.
        assert "import solve_captcha" not in fonte
        assert "resolvedor_captcha.solve_captcha" not in fonte


def test_b_solve_captcha_e_apenas_um_alias():
    assert resolvedor_captcha.solve_captcha is resolvedor_captcha.solve_hcaptcha


def test_b_cell_to_viewport_nem_sequer_esta_implementada():
    """Exportada no `__all__`, sem consumidor, e levanta se alguem chamar."""
    with pytest.raises(NotImplementedError):
        resolvedor_captcha.cell_to_viewport("A1", 0.0, 0.0, 10.0)


# ── K · precondicao: chave ausente ou placeholder ─────────────────────────────

class PaginaProibida:
    """Se o solver tocar na pagina, o teste falha — a validacao vem antes."""

    def __getattr__(self, nome):
        raise AssertionError(f"o solver tocou a pagina ({nome}) antes de validar a chave")


@pytest.mark.parametrize("valor", ["", "cole-sua-chave-aqui"])
def test_k_chave_ausente_ou_placeholder_levanta_antes_de_tocar_a_pagina(monkeypatch, valor):
    monkeypatch.setenv("GEMINI_API_KEY", valor)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        resolvedor_captcha.solve_hcaptcha(PaginaProibida())


def test_k_defeito_chave_so_com_espacos_passa_pela_validacao(monkeypatch):
    """CAPTCHA_POSSIBLE_DEFECT: a validacao nao faz `.strip()`, entao uma chave
    com apenas espacos e considerada valida e o solver segue para o navegador —
    e so falha bem depois, na chamada ao Gemini."""
    monkeypatch.setenv("GEMINI_API_KEY", "   ")

    with pytest.raises(AssertionError, match="tocou a pagina"):
        resolvedor_captcha.solve_hcaptcha(PaginaProibida())


def test_k_a_mensagem_de_precondicao_nao_ecoa_a_chave(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "cole-" + CHAVE)

    with pytest.raises(RuntimeError) as erro:
        resolvedor_captcha.solve_hcaptcha(PaginaProibida())

    assert "SENTINELA" not in str(erro.value)


# ── G · H · o contrato de retry, e quantas chamadas ele permite ───────────────

def test_g_o_retry_do_login_sao_tres_tentativas(monkeypatch, capsys):
    chamadas = []
    monkeypatch.setattr(login_rf, "solve_hcaptcha", lambda page: chamadas.append(page) or False)

    assert login_rf._try_solve_captcha("pagina", "etapa") is False
    assert len(chamadas) == 3


def test_g_sucesso_encerra_o_retry_na_primeira(monkeypatch):
    chamadas = []

    def resolver(page):
        chamadas.append(page)
        return True

    monkeypatch.setattr(login_rf, "solve_hcaptcha", resolver)

    assert login_rf._try_solve_captcha("pagina", "etapa") is True
    assert len(chamadas) == 1


def test_g_excecao_do_solver_e_engolida_e_a_tentativa_continua(monkeypatch, capsys):
    """CAPTCHA_POSSIBLE_DEFECT: `except Exception` no caller — chave ausente,
    Gemini fora do ar e bug nosso produzem o MESMO desfecho, e ainda gastam as
    tres tentativas."""
    def resolver(page):
        raise RuntimeError("GEMINI_API_KEY não configurada no ambiente.")

    monkeypatch.setattr(login_rf, "solve_hcaptcha", resolver)

    assert login_rf._try_solve_captcha("pagina", "etapa") is False
    assert capsys.readouterr().out.count("RuntimeError") == 3


def test_g_o_solver_nao_espera_entre_as_tentativas_do_login(monkeypatch):
    """Sem delay: as tres tentativas do login acontecem em sequencia imediata."""
    fonte = (RAIZ / "servicos_rf_login" / "login.py").read_text(encoding="utf-8")
    trecho = fonte[fonte.index("def _try_solve_captcha"):fonte.index("def _ja_logado")]

    assert "sleep" not in trecho
    assert "wait_for_timeout" not in trecho


def test_h_o_retry_esta_duplicado_em_tres_niveis():
    """CAPTCHA_RETRY_CONTRACT — o achado de custo da fatia.

    Cada nivel multiplica o anterior, e cada rodada do solver pode chamar o
    Gemini varias vezes. Nada disso e coordenado num lugar so.
    """
    solver = (RAIZ / "resolvedor_captcha" / "solver.py").read_text(encoding="utf-8")

    assert "def solve_hcaptcha(page, max_rounds: int = 6)" in solver, "6 rodadas internas"
    assert "MAX_GEMINI_TRIES       = 5" in solver, "5 tentativas por chamada"

    login = (RAIZ / "servicos_rf_login" / "login.py").read_text(encoding="utf-8")
    assert "max_attempts: int = 3" in login, "3 tentativas no login"

    principal = (RAIZ / "main.py").read_text(encoding="utf-8-sig")
    assert principal.count("for tentativa in range(1, 3):") == 2, "2 tentativas no main"


# ── O bloco do main, congelado antes da extracao ──────────────────────────────

FONTE_ORIGINAL = (Path(__file__).parent / "fonte_original_captcha.txt").read_text(
    encoding="utf-8"
)


def test_a_transcricao_confere_com_o_codigo():
    """O bloco do main foi congelado para que estes testes continuem valendo
    depois da extracao, em vez de virarem cadaveres a editar."""
    assert FONTE_ORIGINAL.count("solve_hcaptcha(") == 2, "popup e inline"
    assert FONTE_ORIGINAL.count("for tentativa in range(1, 3):") == 2
    assert "page.wait_for_timeout(2_000)" in FONTE_ORIGINAL
    assert "except Exception as e:" in FONTE_ORIGINAL

    codigo = textwrap.dedent(FONTE_ORIGINAL[FONTE_ORIGINAL.index("        if captcha_tipo"):])
    compile(codigo, "<congelado>", "exec")


def test_o_main_espera_dois_segundos_entre_as_tentativas():
    """Unico delay do contrato inteiro, e ele esta no main — nao no solver."""
    assert FONTE_ORIGINAL.count("page.wait_for_timeout(2_000)") == 2


def test_o_main_nao_distingue_resolvido_de_ausente():
    """CAPTCHA_POSSIBLE_DEFECT: `solve_hcaptcha` devolve True para "resolvido" E
    para "nenhum desafio ativo". Quem chama nao tem como saber qual dos dois foi."""
    solver = (RAIZ / "resolvedor_captcha" / "solver.py").read_text(encoding="utf-8")

    assert "True  — captcha resolvido ou ausente" in solver


# ── S · arquivos temporarios ──────────────────────────────────────────────────

def test_s_o_caminho_real_nao_cria_arquivo_temporario_nenhum():
    """A resposta de ownership desta fatia: NAO HA arquivo temporario.

    O fork tem `_salvar_debug`, que gravaria PNGs em `debug_screenshots/` dentro
    do proprio pacote e nunca os removeria — mas ela nao e chamada em lugar
    nenhum. E codigo morto, e o diretorio nem existe. Screenshots trafegam como
    BYTES, direto para o Gemini.
    """
    solver = (RAIZ / "resolvedor_captcha" / "solver.py").read_text(encoding="utf-8")

    assert solver.count("_salvar_debug") == 1, "so a definicao, nenhuma chamada"
    assert not (RAIZ / "resolvedor_captcha" / "debug_screenshots").exists()


def test_s_os_screenshots_trafegam_como_bytes():
    """`.screenshot()` do Playwright devolve bytes; nada e escrito em disco."""
    solver = (RAIZ / "resolvedor_captcha" / "solver.py").read_text(encoding="utf-8")

    assert "png = ref_loc.screenshot()" in solver
    # `xpath=` casaria com "path=" — o que importa e nenhum `.screenshot(path=...)`.
    assert "screenshot(path" not in solver, "nenhum screenshot com destino em arquivo"
    # Que nada e escrito em disco no caminho real ja esta provado acima, pelo
    # fato de `_salvar_debug` nunca ser chamada.


# ── T · dependencias no caminho real ──────────────────────────────────────────

def test_t_pillow_e_google_genai_sao_import_opcional_no_fork():
    """DIRECT_IMPORT_DEPENDENCY, mas dentro de try/except: a ausencia so aparece
    quando o caminho que precisa deles e executado."""
    solver = (RAIZ / "resolvedor_captcha" / "solver.py").read_text(encoding="utf-8")

    assert "from PIL import Image" in solver
    assert "from google import genai as _genai_lib" in solver
    assert "_PIL = False" in solver
    assert "google.genai nao disponivel" in solver


def test_t_o_fork_nao_e_alcancado_por_import_do_nucleo():
    """Nem domain, nem status_portal, nem boundary conhecem o solver."""
    for modulo in ("domain.py", "status_portal.py", "boundary.py"):
        fonte = (RAIZ / "automation" / modulo).read_text(encoding="utf-8")
        assert "resolvedor_captcha" not in fonte
        assert "genai" not in fonte


def test_t_o_ambiente_do_teste_nao_precisa_de_chave():
    """Nenhum destes testes depende de GEMINI_API_KEY estar configurada."""
    assert os.environ.get("GEMINI_API_KEY", "") == ""
