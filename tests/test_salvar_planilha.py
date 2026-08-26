"""O caminho de falha ao gravar a planilha — o caso que justificou o seam de eventos.

Nasceu como KNOWN_STATIC_DEFECT_F821 (`registrar_erro` chamado sem definicao) e
hoje caracteriza um contrato operacional inteiro:

    a gravacao falha
    -> a sessao CONTINUA suja
    -> o app emite SALVAMENTO_PLANILHA_FALHOU na hora
    -> o adapter mostra e registra em disco
    -> uma gravacao posterior recupera o progresso.

CHARACTERIZATION_TARGET_CHANGE (fatia 9B): o caminho era `main.salvar_planilha`.
Ele foi partido em dois — `app._Execucao.salvar` decide, `main.Renderer`
apresenta — e os testes passam a exercitar os dois JUNTOS. Nenhuma afirmacao
mudou; a que era sobre o retorno `False` passou a ser sobre o estado sujo, que e
o que aquele `False` significava.

Nenhum teste abre planilha real: o workbook e falso, porque o que se prova aqui
e o TRATAMENTO DE ERRO, nao a gravacao.
"""
import pytest

import main
from automation import app
from automation.captcha import ConfigCaptcha
from automation.planilha import SessaoPlanilha

CONFIG = ConfigCaptcha(api_key="AIzaSy-SENTINELA-FICTICIA-0000")


class WorkbookQueNaoSalva:
    """Falha ao salvar como um arquivo aberto no Excel falharia."""

    def __init__(self, erro=None):
        self.erro = erro or PermissionError(
            "[Errno 13] Permission denied: 'C:/Clientes/ACME/DEBITOS 11222333000199.xlsx'"
        )
        self.fechado = False

    def save(self, caminho):
        raise self.erro

    def close(self):
        self.fechado = True


class WorkbookQueSalva:
    def __init__(self):
        self.salvou = False

    def save(self, caminho):
        self.salvou = True

    def close(self):
        pass


@pytest.fixture
def log_isolado(tmp_path, monkeypatch):
    """`registrar_erro` grava em `Path.cwd() / "logs"`. Nos testes, em tmp_path."""
    destino = tmp_path / "logs"
    original = main.registrar_erro
    monkeypatch.setattr(
        main, "registrar_erro", lambda mensagem: original(mensagem, log_dir=destino)
    )
    return destino


@pytest.fixture
def sessao_suja():
    """Sessao com alteracao pendente e um workbook que falha ao gravar."""
    sessao = SessaoPlanilha()
    sessao.estado.update(
        {"caminho": "C:/nao/existe/planilha.xlsx", "wb": WorkbookQueNaoSalva(),
         "sujo": True, "status": None}
    )
    return sessao


def salvar(sessao, emissor=None):
    """Roda o `salvar` do app sobre esta sessao, com o emissor dado."""
    app._Execucao(sessao, sessao.estado["caminho"], CONFIG, emissor).salvar()


def test_a_falha_de_salvamento_nao_e_mais_mascarada(sessao_suja, log_isolado, capsys):
    """Este teste afirmava o DEFEITO ate o commit 4236b40: `NameError` no lugar
    de `False`, apagando a causa real — e, dentro do `finally` da unidade CNPJ,
    apagando tambem a excecao que estava em voo.

    Agora afirma a correcao. O historico preserva o antes.
    """
    salvar(sessao_suja, main.Renderer())

    assert "Falha ao salvar a planilha" in capsys.readouterr().out


def test_a_falha_nao_interrompe_a_execucao(sessao_suja):
    """A gravacao e o unico toque no disco por CNPJ, e ela roda num `finally`.
    Levantar dali mataria a execucao inteira por um arquivo aberto no Excel."""
    salvar(sessao_suja)   # sem emissor: nao levanta, nao imprime


def test_a_sessao_continua_suja_para_a_proxima_tentativa(sessao_suja, log_isolado):
    """O contrato: uma falha nao descarta o dado, a proxima gravacao tenta de novo.

    E o comportamento inteiro que justificou o seam de eventos — sem o aviso em
    tempo real, o operador nao tem como liberar o arquivo a tempo.
    """
    salvar(sessao_suja, main.Renderer())
    assert sessao_suja.sujo is True, "o dado nao foi descartado"

    sessao_suja.estado["wb"] = WorkbookQueSalva()
    salvar(sessao_suja, main.Renderer())
    assert sessao_suja.sujo is False, "a retentativa grava"


def test_o_diagnostico_gravado_nao_carrega_o_caminho(sessao_suja, log_isolado):
    """A mensagem do openpyxl traz o caminho completo — nome de cliente incluso —
    e este log vai para disco. So o TIPO do erro e registrado."""
    salvar(sessao_suja, main.Renderer())

    gravado = "".join(f.read_text(encoding="utf-8") for f in log_isolado.glob("*.txt"))
    assert "PermissionError" in gravado, "o tipo orienta a acao"
    for proibido in ["ACME", "11222333000199", "C:/Clientes", "Permission denied"]:
        assert proibido not in gravado


def test_o_evento_nao_carrega_a_mensagem_da_excecao(sessao_suja):
    """O evento e o que atravessa a fronteira; ele leva o NOME DA CLASSE e nada
    mais. A mensagem do openpyxl fica onde nasceu."""
    emitidos = []
    salvar(sessao_suja, emitidos.append)

    (evento,) = emitidos
    assert evento.tipo_da_falha == "PermissionError"
    for proibido in ["ACME", "11222333000199", "C:/Clientes", "Permission denied",
                     "planilha.xlsx"]:
        assert proibido not in str(evento)


def test_o_caminho_feliz_nao_emite_nada(sessao_suja):
    """Delimita o defeito: so a falha de gravacao o alcanca."""
    sessao_suja.estado["sujo"] = False
    emitidos = []

    salvar(sessao_suja, emitidos.append)

    assert emitidos == [], "nada pendente, nada a fazer"


def test_registrar_erro_existe_no_projeto():
    """A intencao nao e adivinhada pelo nome: a funcao existe no pacote de login
    que main.py ja importa, e e usada exatamente assim em servicos_rf_login."""
    from servicos_rf_login.log_manager import registrar_erro

    assert callable(registrar_erro)
