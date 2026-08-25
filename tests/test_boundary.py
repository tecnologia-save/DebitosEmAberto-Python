"""A fronteira de entrada: forma valida entra, o resto e recusado com seguranca.

Nenhum teste toca o disco. Os caminhos usados NAO EXISTEM — e a fronteira tem de
aceita-los mesmo assim, porque existencia e pergunta da fatia de planilha.
"""
import builtins
import os
import pathlib
import subprocess
import sys

import pytest

from automation.boundary import (
    EXTENSAO_ACEITA,
    EntradaDebitosEmAberto,
    EntradaInvalida,
    montar_entrada,
)

INEXISTENTE = "C:/nao/existe/em/lugar/nenhum/planilha.xlsx"
RAIZ = pathlib.Path(__file__).resolve().parents[1]


# ── Entrada valida ────────────────────────────────────────────────────────────

def test_input_minimo_valido():
    entrada = montar_entrada({"planilha": INEXISTENTE})

    assert entrada == EntradaDebitosEmAberto(planilha=INEXISTENTE)
    assert entrada.planilha == INEXISTENTE


def test_o_arquivo_nao_precisa_existir():
    """Existencia e pergunta de disco — de outra fatia. A fronteira valida FORMA."""
    assert not pathlib.Path(INEXISTENTE).exists()
    assert montar_entrada({"planilha": INEXISTENTE}).planilha == INEXISTENTE


def test_espaco_em_volta_e_removido():
    assert montar_entrada({"planilha": f"  {INEXISTENTE}  "}).planilha == INEXISTENTE


@pytest.mark.parametrize("caminho", [
    "relativo/planilha.xlsx",
    "planilha.xlsx",
    "C:/pasta com espaço/PLANILHA MODELO.XLSX",
    "//servidor/compartilhada/base.xlsx",
    "C:/pasta/arquivo.com.pontos.xlsx",
])
def test_formas_de_caminho_aceitas(caminho):
    assert montar_entrada({"planilha": caminho}).planilha


def test_o_input_e_imutavel():
    import dataclasses

    entrada = montar_entrada({"planilha": INEXISTENTE})
    with pytest.raises(dataclasses.FrozenInstanceError):
        entrada.planilha = "outro.xlsx"


# ── Recusas ───────────────────────────────────────────────────────────────────

def test_obrigatorio_ausente():
    with pytest.raises(EntradaInvalida, match="planilha"):
        montar_entrada({})


@pytest.mark.parametrize("valor", ["", "   ", "\t"])
def test_valor_vazio(valor):
    with pytest.raises(EntradaInvalida):
        montar_entrada({"planilha": valor})


@pytest.mark.parametrize(
    "valor",
    [None, 3, 3.5, True, ["a.xlsx"], {"p": 1}, pathlib.Path("a.xlsx")],
)
def test_tipo_invalido(valor):
    """Inclusive `Path`: a fronteira recebe texto, e converter aqui esconderia de
    quem chama que o contrato e string."""
    with pytest.raises(EntradaInvalida):
        montar_entrada({"planilha": valor})


def test_parametros_nao_sao_um_mapa():
    for valor in [None, "planilha.xlsx", ["planilha.xlsx"], 7]:
        with pytest.raises(EntradaInvalida):
            montar_entrada(valor)


@pytest.mark.parametrize("caminho", [
    "base.csv", "base.xls", "base.xlsm", "base.txt", "base", "base.xlsx.bak", "base.",
])
def test_extensao_nao_suportada(caminho):
    """So .xlsx atravessa a automacao inteira — ver LEGACY_DESKTOP_ONLY."""
    with pytest.raises(EntradaInvalida, match=EXTENSAO_ACEITA):
        montar_entrada({"planilha": caminho})


def test_campo_desconhecido_e_recusado():
    """RECUSAR, nao ignorar: a fronteira nao recebe metadata externa, entao um
    campo a mais so pode ser engano de quem chamou."""
    with pytest.raises(EntradaInvalida, match="planilhas"):
        montar_entrada({"planilhas": INEXISTENTE})

    with pytest.raises(EntradaInvalida):
        montar_entrada({"planilha": INEXISTENTE, "log": "execucao.log"})


# ── Nada que atravessa a fronteira pode vazar ────────────────────────────────

@pytest.mark.parametrize("parametros", [
    {"planilha": "C:/Clientes/ACME Participacoes/DEBITOS 11222333000199.csv"},
    {"planilha": ""},
    {"planilha": None},
    {},
])
def test_a_mensagem_de_erro_nao_ecoa_o_valor(parametros):
    with pytest.raises(EntradaInvalida) as erro:
        montar_entrada(parametros)

    mensagem = str(erro.value)
    for proibido in ["ACME", "Participacoes", "11222333000199", "C:/", "Clientes", "DEBITOS"]:
        assert proibido not in mensagem


def test_nome_de_campo_arbitrario_nao_e_ecoado():
    """Uma CHAVE desconhecida tambem e input arbitrario — ja mordeu antes.

    So nome estrutural volta na mensagem; qualquer outra coisa e substituida.
    """
    with pytest.raises(EntradaInvalida) as erro:
        montar_entrada({"C:/Clientes/ACME/planilha 11222333000199.xlsx": 1})

    mensagem = str(erro.value)
    assert "ACME" not in mensagem
    assert "11222333000199" not in mensagem
    assert "não nomeável" in mensagem


def test_nome_estrutural_ainda_ajuda_quem_errou():
    """O contrapeso: um typo comum precisa ser dizivel, senao a mensagem e inutil."""
    with pytest.raises(EntradaInvalida, match="planila"):
        montar_entrada({"planila": INEXISTENTE})


def test_nome_estrutural_longo_demais_nao_passa():
    with pytest.raises(EntradaInvalida) as erro:
        montar_entrada({"p" * 200: 1})
    assert "p" * 200 not in str(erro.value)


# ── Pureza da fronteira ───────────────────────────────────────────────────────

def _exercitar():
    ok = montar_entrada({"planilha": INEXISTENTE})
    for ruim in [{}, {"planilha": ""}, {"planilha": 1}, {"x": 1}, {"planilha": "a.csv"}]:
        with pytest.raises(EntradaInvalida):
            montar_entrada(ruim)
    return ok


def test_a_fronteira_nao_abre_arquivo(monkeypatch):
    def proibido(*a, **k):
        raise AssertionError("a fronteira abriu um arquivo")

    monkeypatch.setattr(builtins, "open", proibido)
    for alvo in ("exists", "open", "is_file", "stat"):
        monkeypatch.setattr(
            pathlib.Path, alvo,
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("tocou o disco")),
        )
    assert _exercitar()


def test_a_fronteira_nao_le_o_ambiente(monkeypatch):
    class Proibido(dict):
        def __getitem__(self, chave):
            raise AssertionError("a fronteira leu o ambiente")

        def get(self, *a, **k):
            raise AssertionError("a fronteira leu o ambiente")

    monkeypatch.setattr(os, "environ", Proibido())
    assert _exercitar()


def test_importar_a_fronteira_nao_arrasta_efeito_externo():
    codigo = (
        "import sys; import automation.boundary; "
        "pesados=[m for m in ('pandas','openpyxl','tkinter','patchright','playwright',"
        "'subprocess','ctypes','requests') if m in sys.modules]; "
        "print(','.join(pesados))"
    )
    saida = subprocess.run(  # noqa: S603 — comando fixo, montado aqui mesmo
        [sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True, check=True
    )
    assert saida.stdout.strip() == "", f"a fronteira carregou {saida.stdout.strip()}"


# ── A ponte TRANSITIONAL ─────────────────────────────────────────────────────

def test_a_ponte_produz_a_entrada_tipada():
    """`main._entrada_da_execucao` é o único ponto do legado que conhece a
    fronteira. Some junto com o argparse local quando o runner existir."""
    import main

    assert main._entrada_da_execucao(INEXISTENTE) == EntradaDebitosEmAberto(planilha=INEXISTENTE)


def test_a_ponte_recusa_o_que_a_fronteira_recusa():
    import main

    with pytest.raises(EntradaInvalida):
        main._entrada_da_execucao("base.csv")


def test_a_ponte_esta_marcada_e_tem_condicao_de_remocao():
    import main

    doc = main._entrada_da_execucao.__doc__
    assert "TRANSITIONAL" in doc
    assert "remoção" in doc


def test_construir_a_entrada_nao_precisa_de_tkinter():
    """O critério de sucesso da fatia: dá para montar o input da execução sem UI,
    Windows, navegador, pandas ou plataforma."""
    codigo = (
        "import sys; from automation.boundary import montar_entrada; "
        "e = montar_entrada({'planilha': 'x.xlsx'}); "
        "assert e.planilha == 'x.xlsx'; "
        "print('tkinter' in sys.modules)"
    )
    saida = subprocess.run(  # noqa: S603 — comando fixo, montado aqui mesmo
        [sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True, check=True
    )
    assert saida.stdout.strip() == "False"
