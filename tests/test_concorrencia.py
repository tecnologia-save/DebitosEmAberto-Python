"""O contrato de concorrencia do host: o que duas execucoes compartilham.

Simulacoes PURAS. Nenhum teste abre navegador, toca o registro, roda PowerShell
ou lanca processo. O que se prova aqui e o MECANISMO — se o codigo torna um
desfecho possivel — e nunca que ele ja aconteceu.

Nada e corrigido nesta fatia: ela decide o contrato.

Dois modelos, e os riscos NAO sao os mesmos:

    MODELO A   dois processos DebitosEmAberto no mesmo host
    MODELO B   duas chamadas `app.executar` no mesmo processo Python

CNPJs e certificados ficticios.
"""
import ast
import os
import pathlib

import pytest

from automation import maquina, representacao

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CN_A = "ALFA FICTICIA:11111111000191"
CN_B = "BETA FICTICIA:22222222000172"


# ── MODELO B · o mesmo processo compartilha os.environ ────────────────────────

def test_modelo_b_a_segunda_execucao_sobrescreve_a_identidade_da_primeira(
    monkeypatch, tmp_path
):
    """IDENTITY_RISK, no nivel do mecanismo.

    `CERT_SUBJECT_CN` e uma variavel do PROCESSO. Duas execucoes no mesmo
    interpretador tem uma so, e a ultima a escrever vence — mesmo que a primeira
    ainda esteja no meio do seu certificado.
    """
    monkeypatch.setattr(maquina, "diretorio_de_perfil", lambda: str(tmp_path))

    maquina.preparar_ambiente_do_certificado(CN_A)
    assert os.environ["CERT_SUBJECT_CN"] == CN_A

    maquina.preparar_ambiente_do_certificado(CN_B)

    assert os.environ["CERT_SUBJECT_CN"] == CN_B, (
        "a execucao A passa a ler a identidade de B"
    )


def test_modelo_b_o_env_compartilhado_alimenta_a_escolha_por_UI(monkeypatch, tmp_path):
    """Por que o item acima e IDENTITY e nao apenas AVAILABILITY.

    Quando a policy nao esta confiavel (UAC negado), o fork resolve a janela
    nativa de certificado por UI — e a thread que faz isso le o CN do AMBIENTE,
    sem parametro. E o unico ponto do caminho vivo em que `os.environ` decide
    QUAL certificado e escolhido.
    """
    fonte = (RAIZ / "servicos_rf_login" / "login.py").read_text(encoding="utf-8")

    assert 'args=(os.getenv("CERT_SUBJECT_CN", "").strip(), cert_serial),' in fonte
    assert "_selecionar_cert_dialog" in fonte
    assert "not policy_ok" in fonte, "so acontece com a policy inativa"


def test_o_lancamento_do_chrome_usa_o_parametro_e_nao_o_ambiente():
    """O contrapeso, e ele limita o alcance do risco acima.

    A flag --auto-select-certificate-for-urls e montada com o CN que veio por
    PARAMETRO; o ambiente e so fallback para quando o parametro esta vazio, o que
    nao acontece no caminho vivo. O navegador em si nao e contaminado.
    """
    fonte = (RAIZ / "servicos_rf_login" / "login.py").read_text(encoding="utf-8")

    assert "chrome_args.append(_build_auto_select_cert_flag(cert_subject_cn))" in fonte
    assert 'subject_cn = (subject_cn or os.getenv("CERT_SUBJECT_CN", "")).strip()' in fonte


# ── MODELO A · o .env compartilhado NAO contamina o processo vivo ─────────────

def test_modelo_a_o_override_do_dotenv_nao_alcanca_o_caminho_vivo():
    """CORRECAO de uma afirmacao da fatia 10.

    Eu documentei que o `.env` precisava ser escrito porque `fazer_login` chamava
    `load_dotenv(..., override=True)` e sobrescreveria o ambiente no meio da
    execucao. A chamada existe — mas dentro de `_resolver_certificado`, que so
    roda no ramo `.pfx`. No modo Windows Store, o unico usado, ela NAO e
    alcancada.

    A consequencia importa para esta fatia: entre PROCESSOS, o `.env`
    compartilhado nao contamina a identidade de uma execucao em andamento.
    """
    fonte = (RAIZ / "servicos_rf_login" / "login.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)

    resolver = next(f for f in ast.walk(arvore)
                    if isinstance(f, ast.FunctionDef) and f.name == "_resolver_certificado")
    dentro = [n for n in ast.walk(resolver)
              if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "load_dotenv"]
    assert len(dentro) == 1, "o override mora dentro do resolvedor de .pfx"

    # E o resolvedor de .pfx so e chamado quando NAO ha CN — isto e, nunca aqui.
    assert fonte.count("_resolver_certificado(") == 2, "definicao + uma chamada"
    trecho = fonte[fonte.index("usar_windows_store = bool("):]
    trecho = trecho[: trecho.index("user_data_dir = ")]
    assert trecho.index("else:") < trecho.index("_resolver_certificado(")


def test_o_env_em_disco_e_reescrito_por_quem_chegar_depois(monkeypatch, tmp_path):
    """O arquivo e compartilhado por DIRETORIO DE PROJETO, e nao tem dono.

    Nao contamina a execucao em andamento (teste acima), mas o valor que fica em
    disco e o da ultima execucao — estado STALE para quem o ler depois.
    """
    monkeypatch.setattr(maquina, "diretorio_de_perfil", lambda: str(tmp_path))

    maquina.preparar_ambiente_do_certificado(CN_A)
    maquina.preparar_ambiente_do_certificado(CN_B)

    conteudo = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"CERT_SUBJECT_CN={CN_B}" in conteudo
    assert CN_A not in conteudo


# ── A policy: sem dono, sem namespace ─────────────────────────────────────────

def test_a_policy_e_um_valor_so_para_a_maquina_inteira():
    """GLOBAL_CERT_POLICY_CONCURRENCY_RISK, na fonte.

    Um caminho de registro fixo, sem PID, sem sessao, sem nada que identifique
    QUEM escreveu. A segunda execucao nao acrescenta: ela apaga os valores
    existentes e escreve os seus.
    """
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")

    assert 'REG_PATH = r"Software\\Policies\\Google\\Chrome\\AutoSelectCertificateForUrls"' in fonte
    for marca in ("pid", "getpid", "uuid", "owner", "session"):
        assert marca not in fonte.split("REG_PATH")[1][:400], f"nenhum {marca} no caminho"

    escrita = fonte[fonte.index("def definir_autoselect"):fonte.index("def limpar_autoselect")]
    assert "winreg.DeleteValue(key, str(i))" in escrita, "apaga o que estava la"


def test_a_policy_vai_para_as_duas_colmeias_inclusive_a_da_maquina():
    """E o que torna WINDOWS_USER insuficiente como escopo do contrato.

    HKCU e do usuario; HKLM e da MAQUINA. Duas contas Windows diferentes no mesmo
    host disputam a mesma HKLM.
    """
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")

    assert '("HKCU", winreg.HKEY_CURRENT_USER)' in fonte
    assert '("HKLM", winreg.HKEY_LOCAL_MACHINE)' in fonte


def test_a_limpeza_da_policy_e_cega():
    """POLICY_STALE_OWNERSHIP_GAP e o risco de concorrencia, juntos aqui.

    `limpar_autoselect` faz `DeleteKey` incondicional nas duas colmeias. Nao le o
    CN antes, nao compara com o que escreveu, nao verifica se ainda e o dono. O
    guardiao de B remove a policy de A sem saber que ela existe.
    """
    fonte = (RAIZ / "cert_windows.py").read_text(encoding="utf-8")
    limpeza = fonte[fonte.index("def limpar_autoselect"):fonte.index("def _ler_cn")]

    assert "winreg.DeleteKey(raiz, REG_PATH)" in limpeza
    assert "_ler_cn" not in limpeza, "não compara com o CN que escreveu"
    assert "if " not in limpeza.split("for rotulo")[1].split("try:")[0]


# ── Profile e porta ───────────────────────────────────────────────────────────

def test_o_profile_e_a_porta_sao_fixos():
    """BROWSER_PROFILE_CONCURRENCY_RISK.

    O diretorio de perfil e derivado do projeto; a porta de depuracao e literal.
    Uma porta TCP e do HOST — nem duas contas Windows a compartilham.

    O que o Chrome FAZ quando dois processos disputam o mesmo perfil e a mesma
    porta e UNKNOWN_REQUIRING_VALIDATION: nosso codigo nao decide isso, e este
    teste nao inventa comportamento de navegador.
    """
    fonte = (RAIZ / "servicos_rf_login" / "login.py").read_text(encoding="utf-8")

    assert 'user_data_dir = str(project_dir / "chrome_debug_profile")' in fonte
    assert '"--remote-debugging-port=9222"' in fonte
    assert "9222" in fonte and "porta" not in fonte.lower().split("9222")[0][-200:]


def test_o_diretorio_de_perfil_vem_do_projeto():
    """PROJECT_DIRECTORY_SCOPE: duas copias do projeto tem perfis diferentes; a
    porta, nao."""
    import inspect

    fonte = inspect.getsource(maquina.diretorio_de_perfil)

    assert "sys.executable" in fonte or "__file__" in fonte
    assert "9222" not in fonte, "a porta não é derivada daqui"


# ── O rate limit: throttling, e nao identidade ────────────────────────────────

def test_o_intervalo_de_troca_e_um_global_do_processo(monkeypatch):
    """MODELO B. Uma execucao que nunca trocou de CNPJ espera por causa da outra."""
    relogio = [1000.0]
    esperas = []
    monkeypatch.setattr(representacao.time, "time", lambda: relogio[0])
    monkeypatch.setattr(representacao.time, "sleep", esperas.append)
    monkeypatch.setattr(representacao, "_ultimo_troca_cnpj", relogio[0])

    representacao._aguardar_intervalo_troca()

    assert esperas == [30.0], "esperou o intervalo inteiro sem ter trocado nada"


def test_o_intervalo_compartilhado_nunca_ENCURTA_a_espera(monkeypatch):
    """A propriedade que mantem isto em AVAILABILITY e fora de DATA_INTEGRITY.

    O global so pode ficar MAIS RECENTE quando outra execucao o toca, e a regra
    e "espere ate 30s depois do ultimo". Interferencia so pode fazer esperar
    mais, nunca menos — entao o limite do portal nunca e violado por concorrencia.
    """
    relogio = [1000.0]
    monkeypatch.setattr(representacao.time, "time", lambda: relogio[0])

    esperas = []
    monkeypatch.setattr(representacao.time, "sleep", esperas.append)

    # A trocou ha 31s: sozinha, nao esperaria.
    monkeypatch.setattr(representacao, "_ultimo_troca_cnpj", 969.0)
    representacao._aguardar_intervalo_troca()
    assert esperas == [], "sem interferencia, nao espera"

    # B tocou o global agora: A passa a esperar. Mais, e nunca menos.
    monkeypatch.setattr(representacao, "_ultimo_troca_cnpj", 1000.0)
    representacao._aguardar_intervalo_troca()
    assert esperas == [30.0]


def test_o_intervalo_nao_influencia_qual_certificado_e_usado():
    """Separando as categorias: isto e tempo, e nao identidade.

    A funcao nao recebe, nao le e nao devolve nada que identifique certificado —
    ela so olha o relogio. Uma execucao interferir na outra atrasa; nao muda com
    QUEM o portal esta falando.
    """
    import ast
    import inspect

    arvore = ast.parse(inspect.getsource(representacao._aguardar_intervalo_troca))
    for no in ast.walk(arvore):   # fora a docstring, que fala de CNPJ em prosa
        corpo = getattr(no, "body", None)
        if (corpo and isinstance(corpo[0], ast.Expr)
                and isinstance(corpo[0].value, ast.Constant)):
            corpo.pop(0)
    codigo = ast.unparse(arvore).lower()

    # "cnpj" nao entra na lista: o proprio global se chama _ultimo_troca_cnpj, e
    # isso e o NOME do intervalo, nao a identidade de ninguem.
    for identidade in ("subject_cn", "certificado", "cert_", "chave"):
        assert identidade not in codigo
    assert list(ast.parse(inspect.getsource(
        representacao._aguardar_intervalo_troca
    )).body[0].args.args) == [], "nao recebe parametro nenhum"


# ── O primeiro efeito global da execucao ──────────────────────────────────────

def test_o_primeiro_efeito_compartilhado_e_a_policy():
    """§13: se um dia houver exclusividade, ela precisa existir ANTES disto.

    A ordem em `executar` e: descobrir certificados (leitura), abrir a planilha
    (arquivo de entrada), montar os itens — e so entao `_percorrer`, cuja
    primeira acao por certificado e garantir a policy. A policy e a primeira
    MUTACAO de estado compartilhado do host.
    """
    import inspect

    fonte = inspect.getsource(__import__("automation.app", fromlist=["x"]).executar)
    percorrer = inspect.getsource(__import__("automation.app", fromlist=["x"])._percorrer)
    trocar = inspect.getsource(
        __import__("automation.app", fromlist=["x"])._Execucao.trocar_certificado
    )

    assert "descobrir()" in fonte
    assert fonte.index("descobrir()") < fonte.index("_percorrer(execucao, itens)")
    assert "trocar_certificado" in percorrer
    assert "garantir_policy_do_windows" in trocar
    assert trocar.index("garantir_policy_do_windows") < len(trocar)

    # E o login — que prepara o ambiente e lanca o navegador — vem DEPOIS.
    assert percorrer.index("trocar_certificado") < percorrer.index("autenticar(item)")


@pytest.mark.parametrize("adapter", ["runner.py", "local.py"])
def test_nenhum_adapter_adquire_exclusividade_hoje(adapter):
    """O estado ATUAL, registrado: o contrato ainda nao e imposto por ninguem."""
    fonte = (RAIZ / adapter).read_text(encoding="utf-8")

    for mecanismo in ("mutex", "lockfile", "lock(", "flock", "CreateMutex"):
        assert mecanismo.lower() not in fonte.lower()
