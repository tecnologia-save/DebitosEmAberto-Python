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


def test_o_defeito_f821_mascara_a_falha_de_salvamento(sessao_suja, capsys):
    """Prova do bug, ANTES da correcao.

    A falha real e PermissionError. Mas o tratamento dela chama um nome que nao
    existe, entao quem chama recebe NameError — e a causa verdadeira some.

    Pior no ponto que importa: `processar_cnpj` chama `salvar_planilha()` dentro
    de um `finally`. Uma excecao levantada ali SUBSTITUI a que estava em voo,
    entao o NameError apaga tambem o erro original do processamento do CNPJ.
    """
    with pytest.raises(NameError, match="registrar_erro") as erro:
        main.salvar_planilha()

    assert "Falha ao salvar a planilha" in capsys.readouterr().out
    # A causa real vira contexto encadeado: quem captura OSError/PermissionError
    # nao pega nada, e o traceback aponta para o nome inexistente.
    assert isinstance(erro.value.__context__, PermissionError)
    assert not isinstance(erro.value, OSError)


def test_o_return_false_nunca_e_alcancado(sessao_suja):
    """A consequencia funcional: `salvar_planilha` promete devolver False numa
    falha, e a sessao suja deveria sobreviver para a proxima tentativa."""
    with pytest.raises(NameError):
        main.salvar_planilha()

    assert main._sessao_planilha["sujo"] is True, "o dado nao foi descartado"


def test_o_caminho_feliz_nao_passa_pelo_defeito(sessao_suja):
    """Delimita o defeito: so a falha de gravacao o alcanca."""
    main._sessao_planilha["sujo"] = False
    assert main.salvar_planilha() is False, "nada pendente, nada a fazer"


def test_registrar_erro_existe_no_projeto():
    """A intencao nao e adivinhada pelo nome: a funcao existe no pacote de login
    que main.py ja importa, e e usada exatamente assim em servicos_rf_login."""
    from servicos_rf_login.log_manager import registrar_erro

    assert callable(registrar_erro)
