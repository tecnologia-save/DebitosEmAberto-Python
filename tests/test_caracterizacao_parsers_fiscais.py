"""Caracterizacao dos leitores de conteudo fiscal COMO ELES SAO HOJE.

As tres funcoes sao `page.evaluate` com JavaScript grande. Um dublê de pagina
prova o ENCANAMENTO — argumentos, propagacao, paginacao, acumulacao — e o SCHEMA
e afirmado sobre o proprio script, porque e literalmente onde ele esta escrito.

Nenhum navegador real. Dados ficticios.
"""
import pytest

import main

CNPJ = "11111111000191"


class PaginaDeParsing:
    """Registra o que foi avaliado e devolve payloads combinados."""

    def __init__(self, payloads=None, credito_visivel=False, credito=""):
        self.payloads = list(payloads or [])
        self.credito_visivel = credito_visivel
        self.credito = credito
        self.scripts = []
        self.argumentos = []
        self.esperas = []
        self.cliques = []
        self.paginas_restantes = 0

    def evaluate(self, script, *args):
        self.scripts.append(script)
        if args:
            self.argumentos.append(args[0])
        if "processo-credito" in script:
            return self.credito
        if "chevron-down" in script:
            return 0
        if self.payloads:
            return self.payloads.pop(0)
        return []

    def locator(self, seletor):
        return LocatorDeParsing(self, seletor)

    def get_by_role(self, papel, name=None):
        return LocatorDeParsing(self, f"role:{name}")

    def wait_for_timeout(self, ms):
        self.esperas.append(ms)


class LocatorDeParsing:
    def __init__(self, pagina, seletor):
        self.pagina = pagina
        self.seletor = seletor

    @property
    def first(self):
        return self

    def is_visible(self, **kwargs):
        return "processo de crédito" in self.seletor.lower() and self.pagina.credito_visivel

    def is_disabled(self):
        if "Página seguinte" in self.seletor:
            restantes = self.pagina.paginas_restantes
            self.pagina.paginas_restantes -= 1
            return restantes <= 0
        return True

    def click(self, **kwargs):
        self.pagina.cliques.append(self.seletor)

    def wait_for(self, **kwargs):
        pass

    def text_content(self):
        return self.pagina.credito

    def count(self):
        return 0


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    monkeypatch.setattr(main, "_aguardar_networkidle", lambda page, **k: None)


def linhas(quantidade, **extra):
    return [dict({"cnpj": CNPJ, "tipo": f"T{n}"}, **extra) for n in range(1, quantidade + 1)]


# ── D · o schema real, lido do proprio script ─────────────────────────────────

def test_d_schema_do_dctfweb():
    """Oito chaves. Elas nao carregam nome de seletor nem de tag — sao conceitos
    do dominio fiscal, e e por isso que os dicts podem continuar."""
    pagina = PaginaDeParsing(payloads=[[]])
    main._extrair_dados_pagina(pagina, CNPJ)

    script = pagina.scripts[0]
    for chave in ("cnpj", "tipo", "tributo", "receita",
                  "pa_ex", "dt_vcto", "valor_original", "saldo"):
        assert chave in script
    assert "td" not in script.split("resultado.push")[1], "o push nao expoe HTML"


def test_d_schema_do_card_de_processo():
    """Oito chaves, seis em comum com o DCTFWeb, mais `processo_credito` e sem
    `tributo`."""
    pagina = PaginaDeParsing(payloads=[[]])
    main._extrair_dados_pagina_processo(pagina, CNPJ, "PROC-FICTICIO-1")

    script = pagina.scripts[0]
    assert "processo_credito" in script
    assert "'Tributo'" not in script, "o card nao le Tributo"
    assert "'Valor original (R$)'" in script


def test_d_os_rotulos_lidos_da_secao_expandida():
    """Os rótulos são texto do portal e ficam no script — é o único lugar onde
    o formato externo aparece."""
    pagina = PaginaDeParsing(payloads=[[]])
    main._extrair_dados_pagina(pagina, CNPJ)

    script = pagina.scripts[0]
    assert "labelText === 'Tributo'" in script
    assert "labelText === 'Valor original (R$)'" in script


def test_d_o_cnpj_atravessa_como_argumento_e_nao_no_script():
    """SEGURANCA: o CNPJ nao e interpolado no JavaScript."""
    pagina = PaginaDeParsing(payloads=[[]])
    main._extrair_dados_pagina(pagina, CNPJ)

    assert pagina.argumentos == [CNPJ]
    assert CNPJ not in pagina.scripts[0]


def test_d_o_processo_de_credito_atravessa_junto_do_cnpj():
    pagina = PaginaDeParsing(payloads=[[]])
    main._extrair_dados_pagina_processo(pagina, CNPJ, "PROC-FICTICIO-1")

    assert pagina.argumentos == [[CNPJ, "PROC-FICTICIO-1"]]
    assert "PROC-FICTICIO-1" not in pagina.scripts[0]


# ── E · card de processo: encanamento ─────────────────────────────────────────

def test_e_card_sem_processo_de_credito(capsys):
    pagina = PaginaDeParsing(payloads=[linhas(2)])

    resultado = main._processar_card_processo(pagina, CNPJ)

    assert len(resultado) == 2
    assert pagina.argumentos[-1] == [CNPJ, ""], "credito vazio ainda atravessa"


def test_e_card_com_processo_de_credito_expande_e_propaga():
    pagina = PaginaDeParsing(
        payloads=[linhas(1)], credito_visivel=True, credito="PROC-FICTICIO-9"
    )

    main._processar_card_processo(pagina, CNPJ)

    assert any("processo de crédito" in c.lower() for c in pagina.cliques)
    assert pagina.argumentos[-1] == [CNPJ, "PROC-FICTICIO-9"]


def test_j_o_card_pagina_e_acumula_tudo_antes_de_devolver():
    """PARTIAL DATA: a acumulacao e em memoria; nada e devolvido por pagina."""
    pagina = PaginaDeParsing(payloads=[linhas(2), linhas(3)])
    pagina.paginas_restantes = 1

    resultado = main._processar_card_processo(pagina, CNPJ)

    assert len(resultado) == 5, "duas paginas somadas"


def test_j_falha_na_segunda_pagina_descarta_a_primeira():
    """PARTIAL_DATA_LOST: se a extracao da pagina 2 levanta, a pagina 1 vai
    junto — nada foi persistido ainda, porque a gravacao e do adapter."""
    pagina = PaginaDeParsing()

    def quebrar_na_extracao(script, *args):
        pagina.scripts.append(script)
        if "chevron-down" in script:
            return 0            # expandir linhas: segue normal
        raise KeyError("estrutura inesperada")

    pagina.evaluate = quebrar_na_extracao

    with pytest.raises(KeyError):
        main._processar_card_processo(pagina, CNPJ)


# ── K · duplicacao ────────────────────────────────────────────────────────────

def test_k_nao_ha_deduplicacao_em_lugar_nenhum():
    """Se o portal repetir uma linha em duas paginas, ela sai duas vezes. Nao ha
    chave, nao ha set, nao ha comparacao — preservado."""
    iguais = [{"cnpj": CNPJ, "tipo": "T1"}]
    pagina = PaginaDeParsing(payloads=[list(iguais), list(iguais)])
    pagina.paginas_restantes = 1

    resultado = main._processar_card_processo(pagina, CNPJ)

    assert len(resultado) == 2, "as duas iguais sobrevivem"


# ── L · normalizacao ──────────────────────────────────────────────────────────

def test_l_a_unica_normalizacao_e_trim():
    """Nao ha conversao de moeda, data ou numero. Tudo sai como texto do portal,
    apenas com `.trim()` — o dado funcional e a string exibida."""
    for script in (
        _script_de(main._extrair_dados_pagina, CNPJ),
        _script_de(main._extrair_dados_pagina_processo, CNPJ, "P"),
    ):
        assert ".trim()" in script
        for conversao in ("parseFloat", "parseInt", "Number(", "replace(',', '.')",
                          "toFixed", "Date("):
            assert conversao not in script, f"ha conversao de tipo: {conversao}"


def _script_de(funcao, *args):
    pagina = PaginaDeParsing(payloads=[[]])
    funcao(pagina, *args)
    return pagina.scripts[0]


def test_l_ausencia_vira_string_vazia_e_nao_none():
    """`?? ''` em toda leitura — celula ausente e '' , nunca None nem KeyError."""
    script = _script_de(main._extrair_dados_pagina, CNPJ)

    assert script.count("?? ''") >= 5


# ── M · N · erros ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("erro", [KeyError("k"), TypeError("t"), AttributeError("a")])
def test_n_bug_do_leitor_sobe(erro):
    class PaginaQueQuebra(PaginaDeParsing):
        def evaluate(self, script, *args):
            raise erro

    with pytest.raises(type(erro)):
        main._extrair_dados_pagina(PaginaQueQuebra(), CNPJ)


def test_m_o_processo_de_credito_ausente_e_engolido_de_proposito():
    """O `except` largo em volta da expansao esta preservado: o botao pode nao
    existir, e a ausencia dele e um estado normal do portal."""
    class SemBotao(PaginaDeParsing):
        def locator(self, seletor):
            loc = super().locator(seletor)
            if "processo de crédito" in seletor.lower():
                loc.is_visible = _explodir
            return loc

    def _explodir(**kwargs):
        raise RuntimeError("botão não existe")

    pagina = SemBotao(payloads=[linhas(1)])

    assert len(main._processar_card_processo(pagina, CNPJ)) == 1
    assert pagina.argumentos[-1] == [CNPJ, ""], "segue sem processo de credito"


def test_m_a_busca_do_locator_fica_FORA_do_try():
    """NAVIGATION_POSSIBLE_DEFECT: `page.locator(...)` e chamado antes do `try`.
    Uma falha ali NAO e engolida e derruba o card inteiro — so `is_visible` em
    diante esta protegido."""
    import inspect

    fonte = inspect.getsource(main._processar_card_processo)
    posicao_locator = fonte.index('aria-label="Expandir processo de crédito"')
    posicao_try = fonte.index("    try:", posicao_locator - 400)

    assert posicao_try > posicao_locator, "o locator vem antes do try"


# ── O · helpers de navegacao ──────────────────────────────────────────────────

def test_o_selecionar_itens_por_pagina_e_best_effort(capsys):
    """Falhar em mudar a paginacao nao interrompe a extracao — so muda quantas
    paginas serao percorridas."""
    class SemNgSelect(PaginaDeParsing):
        def locator(self, seletor):
            raise RuntimeError("ng-select não apareceu")

    assert main._selecionar_n_por_pagina(SemNgSelect(), 50) is None
    assert "Não foi possível mudar" in capsys.readouterr().out


def test_o_expandir_linhas_usa_js_para_clicar_todas_de_uma_vez(capsys):
    pagina = PaginaDeParsing()

    main._expandir_todas_as_linhas(pagina)

    assert "chevron-down" in pagina.scripts[0]
    assert "Nenhuma linha para expandir" in capsys.readouterr().out
