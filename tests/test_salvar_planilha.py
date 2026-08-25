"""KNOWN_STATIC_DEFECT_F821 — `registrar_erro` chamado sem definicao.

O caminho: `salvar_planilha` captura a falha de gravacao, imprime, chama
`registrar_erro` — que nao existe em main.py — e so entao devolveria False.

Nenhum teste abre planilha real. O workbook e um objeto falso, porque o que se
prova aqui e o TRATAMENTO DE ERRO, nao a gravacao.
"""
import pytest

import main


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
    original = dict(main._sessao_planilha)
    wb = WorkbookQueNaoSalva()
    main._sessao_planilha.update(
        {"caminho": "C:/nao/existe/planilha.xlsx", "wb": wb, "sujo": True, "status": None}
    )
    yield wb
    main._sessao_planilha.clear()
    main._sessao_planilha.update(original)


def test_a_falha_de_salvamento_nao_e_mais_mascarada(sessao_suja, log_isolado, capsys):
    """Estes dois testes afirmavam o DEFEITO ate o commit 4236b40: `NameError`
    no lugar de `False`, apagando a causa real — e, dentro do `finally` de
    `processar_cnpj`, apagando tambem a excecao que estava em voo.

    Agora afirmam a correcao. O historico preserva o antes.
    """
    assert main.salvar_planilha() is False

    assert "Falha ao salvar a planilha" in capsys.readouterr().out


def test_a_sessao_continua_suja_para_a_proxima_tentativa(sessao_suja, log_isolado):
    """O contrato prometido no docstring da funcao: uma falha nao descarta o
    dado, a proxima chamada tenta de novo."""
    assert main.salvar_planilha() is False
    assert main._sessao_planilha["sujo"] is True

    main._sessao_planilha["wb"] = WorkbookQueSalva()
    assert main.salvar_planilha() is True, "a retentativa grava"
    assert main._sessao_planilha["sujo"] is False


def test_o_diagnostico_gravado_nao_carrega_o_caminho(sessao_suja, log_isolado):
    """A mensagem do openpyxl traz o caminho completo — nome de cliente incluso —
    e este log vai para disco. So o TIPO do erro e registrado."""
    main.salvar_planilha()

    gravado = "".join(f.read_text(encoding="utf-8") for f in log_isolado.glob("*.txt"))
    assert "PermissionError" in gravado, "o tipo orienta a acao"
    for proibido in ["ACME", "11222333000199", "C:/Clientes", "Permission denied"]:
        assert proibido not in gravado


def test_o_caminho_feliz_nao_passa_pelo_defeito(sessao_suja):
    """Delimita o defeito: so a falha de gravacao o alcanca."""
    main._sessao_planilha["sujo"] = False
    assert main.salvar_planilha() is False, "nada pendente, nada a fazer"


def test_registrar_erro_existe_no_projeto():
    """A intencao nao e adivinhada pelo nome: a funcao existe no pacote de login
    que main.py ja importa, e e usada exatamente assim em servicos_rf_login."""
    from servicos_rf_login.log_manager import registrar_erro

    assert callable(registrar_erro)
