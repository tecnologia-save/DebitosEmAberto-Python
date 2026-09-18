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


@pytest.fixture(autouse=True)
def sem_janela_real(monkeypatch):
    """Nenhum teste abre janela de verdade. Os da janela instalam um Tk falso."""
    def sem_tk(page, fim):
        raise RuntimeError("sem Tk no teste")
    monkeypatch.setattr(solver, "_esperar_com_aviso", sem_tk)


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


# ── o aviso na tela ───────────────────────────────────────────────────────────

class _TkFalso:
    """O pedaco de `tkinter` que o aviso usa, com `after` rodando no relogio falso."""

    def __init__(self, relogio):
        self.relogio = relogio
        self.janelas, self.botoes = [], []
        modulo = self

        class Tk:
            def __init__(self):
                self.fila, self.destruida, self.atributos = [], False, {}
                self.sinos, self.fechar_no_x, self.proximo = 0, None, 0
                modulo.janelas.append(self)

            def title(self, _t): pass
            def resizable(self, *_a): pass
            def attributes(self, nome, valor): self.atributos[nome] = valor
            def protocol(self, nome, funcao): self.fechar_no_x = funcao
            def lift(self): pass
            def focus_force(self): pass
            def bell(self): self.sinos += 1
            def destroy(self): self.destruida = True

            def after(self, ms, funcao):
                self.proximo += 1
                self.fila.append((self.proximo, ms, funcao))
                return self.proximo

            def after_cancel(self, ident):
                self.fila = [f for f in self.fila if f[0] != ident]

            def mainloop(self):
                while not self.destruida and self.fila:
                    _id, ms, funcao = self.fila.pop(0)
                    modulo.relogio.sleep(ms / 1000)
                    funcao()

        class _Widget:
            def __init__(self, *_a, **kw):
                self.kw = kw
                if "command" in kw:
                    modulo.botoes.append(self)
            def pack(self, **_k): pass
            def config(self, **kw): self.kw.update(kw)

        self.Tk, self.Label, self.Frame, self.Button = Tk, _Widget, _Widget, _Widget


@pytest.fixture
def tk_falso(monkeypatch, relogio):
    import sys
    falso = _TkFalso(relogio)
    monkeypatch.setitem(sys.modules, "tkinter", falso)
    # Desfaz o `sem_janela_real` so neste teste: aqui o aviso roda de verdade,
    # sobre o Tk falso.
    monkeypatch.setattr(solver, "_esperar_com_aviso", _ESPERAR_COM_AVISO)
    return falso


_ESPERAR_COM_AVISO = solver._esperar_com_aviso


def test_o_aviso_fica_na_frente_toca_e_fecha_sozinho_quando_resolve(tk_falso, monkeypatch):
    _desafio_some_depois_de(monkeypatch, leituras=20)

    assert solver._aguardar_humano(_Pagina(), limite_s=600) is True

    janela, = tk_falso.janelas
    assert janela.atributos.get("-topmost") is True
    assert janela.sinos >= 1
    assert janela.destruida


def test_o_aviso_expira_sem_ninguem(tk_falso, monkeypatch, relogio):
    monkeypatch.setattr(solver, "_challenge_visible", lambda _p: True)

    assert solver._aguardar_humano(_Pagina(), limite_s=90) is False
    assert tk_falso.janelas[0].destruida
    assert relogio.agora >= 1_000.0 + 90


def test_parar_de_esperar_devolve_false(tk_falso, monkeypatch):
    def clicar_no_terceiro_segundo(_page):
        if tk_falso.relogio.agora >= 1_003:
            tk_falso.botoes[0].kw["command"]()
        return True
    monkeypatch.setattr(solver, "_challenge_visible", clicar_no_terceiro_segundo)

    assert solver._aguardar_humano(_Pagina(), limite_s=600) is False
    assert tk_falso.relogio.agora < 1_000.0 + 10


def test_fechar_no_x_nao_encerra_a_espera(tk_falso, monkeypatch):
    leituras = []

    def visivel(_page):
        leituras.append(1)
        if len(leituras) == 2:
            tk_falso.janelas[0].fechar_no_x()
        return len(leituras) <= 5
    monkeypatch.setattr(solver, "_challenge_visible", visivel)

    assert solver._aguardar_humano(_Pagina(), limite_s=600) is True
    assert len(leituras) >= 7, "continuou esperando depois do X"


def test_sem_tk_a_espera_segue_pelo_log(monkeypatch, relogio, capsys):
    _desafio_some_depois_de(monkeypatch, leituras=3)

    assert solver._aguardar_humano(_Pagina(), limite_s=600) is True
    assert "Aviso na tela indisponível" in capsys.readouterr().out
