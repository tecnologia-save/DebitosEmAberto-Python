"""O README nao carrega identificador de cliente.

CLIENT_IDENTIFIERS_IN_VERSIONED_README. A secao que explica a busca de
certificado trazia tres nomes que aparentavam empresas reais, num arquivo
versionado e publico. Foram trocados por exemplos obviamente sinteticos.

Como este arquivo NAO prende a regressao
----------------------------------------
Nao ha lista dos nomes antigos aqui: repeti-los seria devolve-los ao repositorio
pela porta dos fundos. E nao ha busca de frase inteira — isso ja produziu falso
positivo neste projeto mais de uma vez.

A guarda e estrutural: todo exemplo de NOME DE CERTIFICADO no README tem de se
declarar ficticio.
"""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
README = RAIZ / "README.md"

# O que faz um exemplo se declarar inventado.
MARCAS_DE_FICCAO = ("FICTICI", "FICTÍCI", "EXEMPLO", "DEMONSTRACAO",
                    "DEMONSTRAÇÃO", "MODELO", "XYZ")


def _exemplos_de_certificado() -> list[str]:
    """Os nomes em CAIXA ALTA citados na secao de formato da planilha.

    E ali que os exemplos de nome de certificado vivem — sao o argumento de
    'voce pode abreviar'. Cabecalhos de coluna sao curtos e ficam de fora pelo
    tamanho.
    """
    texto = README.read_text(encoding="utf-8")
    secao = texto[texto.index("## Formato da Planilha"):]
    secao = secao[: secao.index("## Execução")]

    return [achado for achado in re.findall(r"`([A-ZÀ-Ú][A-ZÀ-Ú ]{9,})`", secao)]


def test_ha_exemplos_de_certificado_para_conferir():
    """Guarda de sanidade: se a secao mudar de forma e a busca parar de achar
    nada, o teste abaixo passaria a vazio."""
    assert len(_exemplos_de_certificado()) >= 3


def test_todo_exemplo_de_certificado_se_declara_ficticio():
    for exemplo in _exemplos_de_certificado():
        assert any(marca in exemplo for marca in MARCAS_DE_FICCAO), exemplo


def test_o_README_nao_traz_CNPJ_de_aparencia_real():
    """Os unicos numeros longos permitidos sao os sinteticos evidentes: a
    sequencia crescente do exemplo e as mascaras de formato."""
    texto = README.read_text(encoding="utf-8")

    numeros = re.findall(r"\b\d{11,14}\b", texto)

    assert numeros == ["12345678000195"], numeros


def test_os_documentos_dos_casos_de_teste_sao_sinteticos():
    """SYNTHETIC_NAMES_LOOK_PLAUSIBLE_IN_TEST_FIXTURES — observado, e nao
    corrigido.

    Os certificados dos testes existem para exercitar FORMAS de nome: espaco
    entre iniciais, `&`, pontos, nome pessoal longo. Um deles ainda le como
    nome de pessoa plausivel.

    Nao foram trocados: sao literais carregando assercao em tres arquivos, o
    finding desta fase e o README, e o proprio enunciado pede para nao virar
    auditoria do repositorio inteiro. O que da para afirmar aqui e o que
    importa: os DOCUMENTOS sao evidentemente inventados, e nenhum deles
    identifica alguem.
    """
    fonte = (RAIZ / "tests" / "casos_certificado.py").read_text(encoding="utf-8")

    documentos = re.findall(r'"(\d{11,14})"', fonte)

    assert len(documentos) >= 6
    for documento in documentos:
        assert set(documento) <= {"0", "1", "2", "3", "4", "5"}, documento
        assert documento.startswith("0000000000"), documento
