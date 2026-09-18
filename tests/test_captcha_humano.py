"""Uma falha do solver e o captcha passa ao usuario.

Pedido do operador depois de uma execucao real (D8.4): sob pico de demanda do
Gemini o solver insistia por minutos e quem resolvia era a pessoa na frente da
maquina. Agora, na PRIMEIRA falha, a automacao traz o Chrome para a frente,
avisa e espera o desafio sumir — com prazo.

Nenhum teste abre navegador ou chama Gemini: pagina, relogio e estrategias sao
falsos.
"""
import pytest

from resolvedor_captcha import solver


class _Relogio:
    def __init__(self):
        self.agora = 1_000.0

    def time(self):
        return self.agora

    def sleep(self, segundos):
        self.agora += segundos


class _Pagina:
    def __init__(self):
        self.na_frente = 0

    def bring_to_front(self):
        self.na_frente += 1

    def wait_for_timeout(self, _ms):
        pass


@pytest.fixture
def relogio(monkeypatch):
    falso = _Relogio()
    monkeypatch.setattr(solver.time, "time", falso.time)
    monkeypatch.setattr(solver.time, "sleep", falso.sleep)
    return falso


def _desafio_some_depois_de(monkeypatch, leituras):
    """O desafio fica visivel nas `leituras` primeiras consultas, depois some."""
    consultas = []

    def visivel(_page):
        consultas.append(1)
        return len(consultas) <= leituras
    monkeypatch.setattr(solver, "_challenge_visible", visivel)
    return consultas


# ── a espera ──────────────────────────────────────────────────────────────────

def test_espera_o_usuario_e_devolve_true_quando_o_desafio_some(monkeypatch, relogio, capsys):
    _desafio_some_depois_de(monkeypatch, leituras=30)
    pagina = _Pagina()

    assert solver._aguardar_humano(pagina, limite_s=600) is True

    assert pagina.na_frente == 1, "a janela vem para a frente"
    saida = capsys.readouterr().out
    assert "RESOLVA O CAPTCHA NA JANELA DO CHROME" in saida
    assert "resolvido pelo usuário" in saida


def test_um_sumico_de_um_segundo_nao_basta(monkeypatch, relogio):
    """Entre uma pagina e outra do desafio o frame some por um instante."""
    sequencia = iter([True, False, True, True, False, False])
    monkeypatch.setattr(solver, "_challenge_visible", lambda _p: next(sequencia))

    assert solver._aguardar_humano(_Pagina(), limite_s=600) is True
    assert relogio.agora == 1_000.0 + 5, "so confirma na segunda leitura seguida"


def test_sem_ninguem_a_espera_expira(monkeypatch, relogio, capsys):
    monkeypatch.setattr(solver, "_challenge_visible", lambda _p: True)

    assert solver._aguardar_humano(_Pagina(), limite_s=120) is False
    assert relogio.agora >= 1_000.0 + 120
    assert "Tempo esgotado" in capsys.readouterr().out


def test_espera_zero_desliga(monkeypatch, relogio):
    pagina = _Pagina()
    monkeypatch.setattr(solver, "_challenge_visible", lambda _p: True)

    assert solver._aguardar_humano(pagina, limite_s=0) is False
    assert pagina.na_frente == 0


def test_janela_que_nao_vem_para_a_frente_nao_derruba(monkeypatch, relogio):
    _desafio_some_depois_de(monkeypatch, leituras=0)

    class _Teimosa(_Pagina):
        def bring_to_front(self):
            raise RuntimeError("sem janela")

    assert solver._aguardar_humano(_Teimosa(), limite_s=60) is True


# ── solve_hcaptcha: uma falha basta ───────────────────────────────────────────

@pytest.fixture
def hcaptcha(monkeypatch):
    chamadas = {"estrategia": 0, "humano": 0}
    monkeypatch.setattr(solver, "_click_checkbox_widget", lambda page, timeout_ms: False)
    monkeypatch.setattr(solver, "_detect_challenge_type",
                        lambda page, timeout_ms: "grade_fused")

    def humano(page):
        chamadas["humano"] += 1
        return True
    monkeypatch.setattr(solver, "_aguardar_humano", humano)

    def estrategia(resultado):
        def _solve(page, api_key):
            chamadas["estrategia"] += 1
            return resultado
        monkeypatch.setattr(solver, "_solve_grade_fused", _solve)
    return chamadas, estrategia


def test_solver_que_nao_resolve_passa_ao_usuario_na_primeira_vez(hcaptcha):
    chamadas, estrategia = hcaptcha
    estrategia(False)

    assert solver.solve_hcaptcha(_Pagina(), api_key="chave-ficticia") is True
    assert chamadas == {"estrategia": 1, "humano": 1}


def test_desafio_que_continua_depois_da_resposta_passa_ao_usuario(hcaptcha, monkeypatch):
    chamadas, estrategia = hcaptcha
    estrategia(True)
    monkeypatch.setattr(solver, "_challenge_visible", lambda _p: True)

    assert solver.solve_hcaptcha(_Pagina(), api_key="chave-ficticia") is True
    assert chamadas == {"estrategia": 1, "humano": 1}


def test_solver_que_resolve_nao_incomoda_o_usuario(hcaptcha, monkeypatch):
    chamadas, estrategia = hcaptcha
    estrategia(True)
    monkeypatch.setattr(solver, "_challenge_visible", lambda _p: False)

    assert solver.solve_hcaptcha(_Pagina(), api_key="chave-ficticia") is True
    assert chamadas == {"estrategia": 1, "humano": 0}


def test_as_quatro_estrategias_desistem_no_primeiro_erro_do_gemini():
    """grade, grade_fused e imagem trocaram `continue` por `return False`; cartao
    ja saia do laco com `break` e devolve False."""
    import pathlib
    fonte = pathlib.Path(solver.__file__).read_text(encoding="utf-8")

    assert fonte.count("return False  # uma falha basta: a vez passa ao usuário") == 3
    assert "break  # todos os modelos falharam" in fonte
