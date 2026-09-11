"""RESOURCE_VALIDATION: da para ler E gravar esta planilha antes de comecar?

A checagem existia, mas dentro da UI desktop — logo o fluxo sem Tkinter ficava
sem ela. Aqui ela pertence a integracao.

Nenhum caminho usado nestes testes aponta para arquivo real de cliente. Os nomes
carregam sentinelas ficticias de proposito: e como se prova que a mensagem de
erro nao as repete.
"""
import zipfile

import pytest
from planilhas_sinteticas import criar_planilha

from automation.planilha import PlanilhaIndisponivel, validar_recurso

# Sentinelas ficticias plantadas no CAMINHO. Nenhuma pode reaparecer numa mensagem.
SENTINELAS = ["ACME", "Participacoes", "11222333000199", "Clientes", "DEBITOS"]


def caminho_com_sentinelas(tmp_path):
    pasta = tmp_path / "Clientes" / "ACME Participacoes"
    pasta.mkdir(parents=True)
    return pasta / "DEBITOS 11222333000199.xlsx"


def sem_vazamento(erro):
    mensagem = str(erro)
    for sentinela in SENTINELAS:
        assert sentinela not in mensagem, f"a mensagem repetiu '{sentinela}'"
    # `.xlsx` sozinho e permitido: a mensagem constante fala do formato exigido.
    # O que nao pode aparecer e o NOME do arquivo, que costuma ser o do cliente.
    assert "DEBITOS 11222333000199" not in mensagem
    return True


# ── O caminho feliz ───────────────────────────────────────────────────────────

def test_planilha_valida_passa(tmp_path):
    destino = caminho_com_sentinelas(tmp_path)
    criar_planilha(destino)

    assert validar_recurso(str(destino)) is None


def test_a_validacao_nao_altera_o_arquivo(tmp_path):
    """Pre-voo e leitura. Abrir em "r+b" testa o bloqueio sem escrever byte nenhum."""
    destino = caminho_com_sentinelas(tmp_path)
    criar_planilha(destino)
    antes = destino.read_bytes()

    validar_recurso(str(destino))

    assert destino.read_bytes() == antes


# ── As recusas ────────────────────────────────────────────────────────────────

def test_arquivo_inexistente(tmp_path):
    destino = caminho_com_sentinelas(tmp_path)

    with pytest.raises(PlanilhaIndisponivel, match="não foi encontrada") as erro:
        validar_recurso(str(destino))
    assert sem_vazamento(erro.value)


def test_caminho_e_uma_pasta(tmp_path):
    pasta = tmp_path / "Clientes" / "ACME Participacoes"
    pasta.mkdir(parents=True)

    with pytest.raises(PlanilhaIndisponivel, match="não é um arquivo") as erro:
        validar_recurso(str(pasta))
    assert sem_vazamento(erro.value)


@pytest.mark.parametrize("conteudo", [b"", b"nao sou uma planilha", b"PK\x03\x04lixo"])
def test_arquivo_que_nao_e_xlsx(tmp_path, conteudo):
    destino = caminho_com_sentinelas(tmp_path)
    destino.write_bytes(conteudo)

    with pytest.raises(PlanilhaIndisponivel, match="não é uma planilha") as erro:
        validar_recurso(str(destino))
    assert sem_vazamento(erro.value)


def test_zip_valido_sem_as_partes_do_xlsx(tmp_path):
    """Uma das tres familias confirmadas por sonda: KeyError, e nao BadZipFile."""
    destino = caminho_com_sentinelas(tmp_path)
    with zipfile.ZipFile(destino, "w") as z:
        z.writestr("qualquer.txt", "conteudo")

    with pytest.raises(PlanilhaIndisponivel, match="não é uma planilha") as erro:
        validar_recurso(str(destino))
    assert sem_vazamento(erro.value)


def test_planilha_sem_a_aba_empresas(tmp_path):
    destino = caminho_com_sentinelas(tmp_path)
    criar_planilha(destino, aba="Outra")

    with pytest.raises(PlanilhaIndisponivel, match="Empresas") as erro:
        validar_recurso(str(destino))
    assert sem_vazamento(erro.value)


def test_planilha_bloqueada_para_gravacao(tmp_path, monkeypatch):
    """A falha real mais comum: o arquivo aberto no Excel.

    Descobrir isso só na primeira gravação custa um login e um CNPJ inteiros.
    """
    import pathlib

    destino = caminho_com_sentinelas(tmp_path)
    criar_planilha(destino)

    original = pathlib.Path.open

    def recusar(self, modo="r", *args, **kwargs):
        if "+" in modo:
            raise PermissionError(f"[Errno 13] Permission denied: '{self}'")
        return original(self, modo, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", recusar)

    with pytest.raises(PlanilhaIndisponivel, match="Feche-a no Excel") as erro:
        validar_recurso(str(destino))
    assert sem_vazamento(erro.value)


def test_a_causa_original_nao_fica_encadeada(tmp_path):
    """`from None` corta o encadeamento: a exception do openpyxl carrega o caminho
    completo, e ela nao pode viajar pendurada na nossa."""
    destino = caminho_com_sentinelas(tmp_path)
    destino.write_bytes(b"nao sou uma planilha")

    with pytest.raises(PlanilhaIndisponivel) as erro:
        validar_recurso(str(destino))

    assert erro.value.__cause__ is None
    assert erro.value.__suppress_context__ is True


# ── A ponte no fluxo local ────────────────────────────────────────────────────

def test_o_erro_de_recurso_e_de_entrada_e_nao_falha_tecnica():
    """Quem executou pode corrigir: arquivo errado, arquivo aberto, aba faltando.
    Não é bug nosso e não deve virar traceback."""
    assert issubclass(PlanilhaIndisponivel, Exception)
    assert not issubclass(PlanilhaIndisponivel, (OSError, KeyError, ValueError))


def test_a_recusa_nao_deixa_o_ARQUIVO_PRESO(tmp_path):
    """Windows não deixa apagar arquivo aberto, e o openpyxl levanta com o zip
    ainda aberto lá dentro.

    `raise ... from None` suprime a EXIBIÇÃO do contexto, não a referência: a
    exceção original continua pendurada em `__context__`, e com ela os frames
    que seguram o arquivo. Enquanto a `PlanilhaIndisponivel` sobe, o handle
    segue vivo — e quem tentar limpar o diretório da execução leva "arquivo em
    uso por outro processo", longe daqui e sem explicação.

    Por isso o arquivo é aberto por nós e fechado por `with`.
    """
    arquivo = tmp_path / "pacote.xlsx"
    with zipfile.ZipFile(arquivo, "w") as zf:
        zf.writestr("leiame.txt", "zip valido, mas nao e xlsx")

    try:
        validar_recurso(str(arquivo))
    except PlanilhaIndisponivel:
        # Apagar DE DENTRO do except: é aqui que a exceção ainda está em voo,
        # que é exatamente a situação em que o defeito aparecia.
        arquivo.unlink()

    assert not arquivo.exists()
