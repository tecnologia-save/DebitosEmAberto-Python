"""Os diagnosticos que o fork de login deixa em disco.

Escrito ANTES de qualquer mudanca da fatia 13B.1.

Nenhum teste abre navegador, contata o portal, chama o Gemini ou usa
certificado real. O Playwright inteiro e substituido por um duble, e os
caminhos de falha sao alcancados desligando os auxiliares do proprio modulo.

Todas as URLs, CNPJs e CNs sao ficticios.
"""
import inspect
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
LOGIN = RAIZ / "servicos_rf_login" / "login.py"

# Sentinela de URL AUTENTICADA. Nao existe, nao resolve, e e o que os testes
# procuram em disco e no console.
URL_SENTINELA = "https://portal.invalid/SENTINELA-SEGREDO?sessao=NAO-DEVE-VAZAR"
CNPJ_FICTICIO = "11111111000191"


# ── O duble ──────────────────────────────────────────────────────────────────

class PaginaFalsa:
    """A superficie que `main()` usa entre o lancamento e o retorno."""

    def __init__(self, url=URL_SENTINELA, falhar_no_locator=False):
        self.url = url
        self.falhar_no_locator = falhar_no_locator
        self.screenshots = []

    def goto(self, *a, **k):
        return None

    def wait_for_load_state(self, *a, **k):
        return None

    def screenshot(self, path=None, **k):
        self.screenshots.append(path)
        Path(path).write_bytes(b"png de mentira")

    def wait_for_timeout(self, *a, **k):
        return None

    def locator(self, *a, **k):
        # `page.locator(...).first` fica FORA do `try` do fork: quem falha e o
        # `wait_for`, e o duble tem de imitar isso para o caminho de falha ser o
        # mesmo que a producao percorre.
        return self

    @property
    def first(self):
        return self

    def wait_for(self, *a, **k):
        if self.falhar_no_locator:
            raise TimeoutError("elemento nao apareceu")

    def click(self, *a, **k):
        return None


class ContextoFalso:
    def __init__(self, pagina):
        self.pages = [pagina]
        self.fechado = False

    def close(self):
        self.fechado = True


class PlaywrightFalso:
    def __init__(self, pagina):
        self.pagina = pagina
        self.parado = False
        self.chromium = self

    def start(self):
        return self

    def launch_persistent_context(self, **k):
        return ContextoFalso(self.pagina)

    def stop(self):
        self.parado = True


@pytest.fixture
def login(monkeypatch, tmp_path):
    """`servicos_rf_login.login` com o mundo desligado e o disco em tmp."""
    from servicos_rf_login import login as modulo

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(modulo.time, "sleep", lambda _s: None)
    monkeypatch.setattr(modulo, "_ja_logado", lambda page: False)
    monkeypatch.setattr(modulo, "_acesso_bloqueado", lambda page: False)
    monkeypatch.setattr(modulo, "_try_solve_captcha",
                        lambda *a, **k: True, raising=False)
    monkeypatch.setattr(modulo, "_clicar_certificado", lambda page: True)
    return modulo


def rodar(modulo, monkeypatch, pagina, tmp_path):
    """Chama `main()` no modo Windows Store, com o duble no lugar do Playwright."""
    monkeypatch.setattr(modulo, "sync_playwright", lambda: PlaywrightFalso(pagina))
    return modulo.main(
        project_dir=tmp_path,
        cert_subject_cn="ALFA FICTICIA LTDA:11111111000191",
        policy_ok=True,
        gemini_api_key="chave-ficticia",
    )


def log_do_dia(tmp_path):
    arquivos = list((tmp_path / "logs").glob("*_servicos_rf.txt"))
    return arquivos[0].read_text(encoding="utf-8") if arquivos else ""


# ── §2 A · o screenshot do botao gov.br ──────────────────────────────────────

def test_2a_botao_govbr_nao_encontrado_persiste_screenshot(login, monkeypatch,
                                                           tmp_path):
    pagina = PaginaFalsa(falhar_no_locator=True)

    resultado = rodar(login, monkeypatch, pagina, tmp_path)

    assert (tmp_path / "_debug_govbr_btn.png").exists()
    assert resultado is None, "e o retorno da falha e None"


def test_2a_e_o_cleanup_da_12B1_acontece_do_mesmo_jeito(login, monkeypatch,
                                                        tmp_path):
    """§4: o screenshot convive com o cleanup, e nao o substitui."""
    pagina = PaginaFalsa(falhar_no_locator=True)
    falso = PlaywrightFalso(pagina)
    monkeypatch.setattr(login, "sync_playwright", lambda: falso)

    login.main(project_dir=tmp_path,
               cert_subject_cn="ALFA FICTICIA LTDA:11111111000191",
               policy_ok=True, gemini_api_key="chave-ficticia")

    assert falso.parado is True, "Playwright parado"


# ── §2 B · o screenshot do botao de certificado ──────────────────────────────

def test_2b_botao_de_certificado_nao_encontrado_persiste_screenshot(
    login, monkeypatch, tmp_path
):
    monkeypatch.setattr(login, "_clicar_certificado", lambda page: False)
    pagina = PaginaFalsa()

    resultado = rodar(login, monkeypatch, pagina, tmp_path)

    assert (tmp_path / "_debug_cert_button.png").exists()
    assert resultado is None


# ── §2 C · o screenshot POS-certificado ──────────────────────────────────────

def test_2c_redirecionamento_que_nao_ocorre_persiste_screenshot(
    login, monkeypatch, tmp_path
):
    """O mais grave dos tres: o certificado ja foi escolhido quando ele e
    capturado, entao a pagina pode estar autenticada."""
    pagina = PaginaFalsa()

    resultado = rodar(login, monkeypatch, pagina, tmp_path)

    assert (tmp_path / "_debug_pos_cert.png").exists()
    assert resultado is None


# ── §2 D · a URL autenticada em disco ────────────────────────────────────────

def test_2d_a_url_autenticada_vai_para_o_log_diario(login, monkeypatch,
                                                    tmp_path):
    pagina = PaginaFalsa()

    rodar(login, monkeypatch, pagina, tmp_path)

    conteudo = log_do_dia(tmp_path)
    assert "SENTINELA-SEGREDO" in conteudo
    assert "sessao=NAO-DEVE-VAZAR" in conteudo


def test_2d_e_tambem_para_o_console(login, monkeypatch, tmp_path, capsys):
    pagina = PaginaFalsa()

    rodar(login, monkeypatch, pagina, tmp_path)

    assert "SENTINELA-SEGREDO" in capsys.readouterr().out


# ── §2 · nenhum consumidor funcional ─────────────────────────────────────────

def test_2_nenhum_screenshot_tem_consumidor(login):
    """Capturados dentro de `try/except: pass`, e o caminho nao le o arquivo."""
    fonte = LOGIN.read_text(encoding="utf-8")

    for nome in ("_debug_govbr_btn.png", "_debug_cert_button.png",
                 "_debug_pos_cert.png"):
        depois = fonte[fonte.index(nome):][:400]
        assert "except Exception:" in depois
        assert "read" not in depois.split("except Exception:")[0]

    nossos = [
        *(RAIZ / "automation").glob("*.py"),
        RAIZ / "runner.py", RAIZ / "local.py", RAIZ / "main.py",
    ]
    for caminho in nossos:
        assert "_debug_" not in caminho.read_text(encoding="utf-8-sig")


# ── §6 · o inventario focado dos `registrar_erro` vivos ──────────────────────

def test_6_sao_oito_chamadas_e_quatro_carregam_dado_dinamico(login):
    fonte = LOGIN.read_text(encoding="utf-8")

    assert fonte.count("registrar_erro(") == 8

    dinamicas = [linha for linha in fonte.splitlines()
                 if "registrar_erro(f\"" in linha]
    assert len(dinamicas) == 3, "as tres de uma linha so"


def test_6_duas_carregam_a_mensagem_BRUTA_do_navegador(login):
    """Alem dos quatro caracterizados, o inventario acha estas duas: a excecao
    do Playwright entra inteira no log, e a mensagem dele costuma trazer a URL
    da pagina e o seletor.

    Mesmo TIPO de vazamento, e nao sao os quatro autorizados. Ficam registradas
    e NAO alteradas nesta fatia.
    """
    fonte = LOGIN.read_text(encoding="utf-8")

    assert 'registrar_erro(f"Login: erro ao abrir URL (1ª navegação). ' \
           '{type(e).__name__}: {e}")' in fonte
    assert "registrar_erro(f\"Login: botão 'Entrar com gov.br' não encontrado. " \
           '{type(e).__name__}: {e}")' in fonte


def test_6_e_uma_carrega_o_CNPJ_DO_CLIENTE(login):
    """O achado inequivoco do inventario, e o mais grave depois da URL: o CNPJ
    da empresa vai para um arquivo de log permanente.

    Mesmo TIPO, fora dos quatro caracterizados. Registrado e NAO alterado.
    """
    fonte = LOGIN.read_text(encoding="utf-8")

    assert 'registrar_erro(f"Login: falha ao representar CNPJ {cnpj}.")' in fonte


def test_6_as_outras_quatro_sao_constantes_e_seguras(login):
    fonte = LOGIN.read_text(encoding="utf-8")

    for constante in (
        '"Login: captcha não resolvido após \'Entrar com gov.br\'."',
        '"Login: acesso bloqueado após \'Entrar com gov.br\' — recuperação falhou."',
        '"Login: botão \'Seu certificado digital\' não encontrado."',
        '"Login: acesso bloqueado após certificado — recuperação esgotada."',
    ):
        assert f"registrar_erro({constante})" in fonte


# ── §8 · o contrato do log diario ────────────────────────────────────────────

def test_8_o_log_e_diario_append_e_fica_no_cwd(login, tmp_path):
    from servicos_rf_login import log_manager

    fonte = inspect.getsource(log_manager.registrar_erro)

    assert 'Path.cwd() / "logs"' in fonte
    assert '"a"' in fonte, "acumula"
    assert '"%d-%m-%Y"' in fonte, "um arquivo por dia"


def test_8_e_o_nosso_lado_so_manda_TIPO_de_falha(login):
    """`main.Renderer` ja e seguro: registra o tipo da excecao, nunca a
    mensagem — que carregaria o caminho do arquivo."""
    import main

    fonte = inspect.getsource(main.Renderer)

    assert "evento.tipo_da_falha" in fonte
    assert "{erro}" not in fonte


# ── §12 · o que a 13B removeu nao volta ──────────────────────────────────────

def test_12_o_guardiao_continua_sem_log(login):
    import cert_windows

    fonte = inspect.getsource(cert_windows.guardiao)

    assert "_glog" not in fonte and "open(" not in fonte


def test_12_o_dispatch_do_guard_continua_sem_arquivo_de_erro(login):
    import cert_windows

    fonte = Path(cert_windows.__file__).read_text(encoding="utf-8")
    trecho = fonte[fonte.index('sys.argv[1] == "--guard"'):]

    assert "write_text" not in trecho
