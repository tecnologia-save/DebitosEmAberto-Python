"""Ponto de entrada — Automação Débitos em Aberto.

Fluxo:
    1. Abre janela para seleção da planilha (ui_upload)
    2. Lê a aba 'Empresas' da planilha:
         Coluna A = CNPJ
         Coluna B = EMPRESA
         Coluna C = CERTIFICADO
         Coluna D = resultado (preenchida pela automação)
    3. Ordena pela coluna C (certificado) para minimizar logins no eCAC
    4. Para o primeiro CNPJ de cada grupo de certificado:
         - Login no eCAC via LoginEcac
         - Navega para servicos.receitafederal.gov.br e autentica
    5. Para cada CNPJ (incluindo os subsequentes do mesmo certificado):
         - Aguarda intervalo mínimo de 30s entre trocas de CNPJ no portal
         - Representa o CNPJ como Procurador no portal
         - Navega para a página de pendências
         - Verifica status e escreve resultado na coluna D
"""

import argparse
import difflib
import json
import logging
import os
import openpyxl
from openpyxl.styles import Alignment
import re
import sys
import time
import unicodedata
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# servicos_rf_login / resolvedor_captcha: pacotes na mesma pasta (repo)
# ou em pastas irmãs para layout de desenvolvimento legado
if getattr(sys, 'frozen', False):
    LOGIN_ECAC_DIR = Path(sys.executable).parent
else:
    LOGIN_ECAC_DIR = Path(__file__).parent
    _cap_here    = Path(__file__).parent / "resolvedor_captcha"
    _cap_sibling = Path(__file__).parent.parent / "ResolvedorCaptcha"
    if not _cap_here.exists() and _cap_sibling.exists():
        sys.path.insert(0, str(_cap_sibling))

from servicos_rf_login import fazer_login                                  # noqa: E402
from servicos_rf_login.login import fechar_tutorial_pos_login              # noqa: E402
from resolvedor_captcha import solve_hcaptcha                                  # noqa: E402
from ui_upload import main as selecionar_planilha                         # noqa: E402

CERTIFICADOS_DIR = Path(r"C:\Certificados")   # pode ser sobrescrito em main()
SENHAS_JSON      = CERTIFICADOS_DIR / "senhas.json"

# Chave Gemini — configure via .env (GEMINI_API_KEY=sua_chave) ou variável de ambiente
_GEMINI_API_KEY_PADRAO = os.environ.get("GEMINI_API_KEY", "")

# Nome do arquivo com a chave embutida no exe pelo debitos_em_aberto.spec
_CHAVE_EMBUTIDA = "chave_gemini.env"


def _ler_chave_de(caminho: Path) -> str:
    """Lê GEMINI_API_KEY de um arquivo no formato .env. '' se não achar."""
    try:
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            if linha.strip().startswith("GEMINI_API_KEY="):
                return linha.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def _resolver_gemini_key() -> tuple[str, str]:
    """Resolve a chave do Gemini. Retorna (chave, origem) para o log.

    Ordem de precedência:
        1. Variável de ambiente já definida — permite trocar a chave numa máquina
           específica sem rebuild.
        2. .env ao lado do executável (ou do script, em desenvolvimento).
        3. Cópia embutida no binário durante o build.

    A chave nunca está no repositório: o .env é gitignored e a cópia embutida é
    gerada pelo .spec a partir dele no momento do build.
    """
    valor = os.environ.get("GEMINI_API_KEY", "").strip()
    if valor:
        return valor, "variável de ambiente"

    env_local = LOGIN_ECAC_DIR / ".env"
    valor = _ler_chave_de(env_local)
    if valor:
        return valor, f"{env_local.name} ao lado do executável"

    base_embutida = getattr(sys, "_MEIPASS", None)
    if base_embutida:
        valor = _ler_chave_de(Path(base_embutida) / _CHAVE_EMBUTIDA)
        if valor:
            return valor, "chave embutida no executável"

    return "", "nenhuma"

URL_SERVICOS_RF        = "https://servicos.receitafederal.gov.br/"
URL_PENDENCIAS         = "https://servicos.receitafederal.gov.br/servico/pendencias/"
URL_ANALISE_PENDENCIAS = "https://servicos.receitafederal.gov.br/servico/pendencias/#/analise-pendencias"
# Classe específica do botão gov.br — mais robusto que XPath posicional
# (o botão contém <img alt="gov.br">, por isso has-text("gov.br") não funciona)
BTN_ENTRAR_GOV  = 'button.login-banner-button'

XPATH_STATUS = (
    "xpath=/html/body/app-root/mf-portal-layout/portal-main-layout/div/main/"
    "ng-component/app-consultar-dividas-pendencias/div[1]/app-resultado-analise-fiscal/"
    "div/div[2]/span"
)

# ── Timer de troca de CNPJ ────────────────────────────────────────────────────
# O portal não permite trocar de CNPJ com intervalo menor que 30 segundos.
_ultimo_troca_cnpj: float = 0.0
_INTERVALO_TROCA           = 30   # segundos


class FalhaPermanente(Exception):
    """Erro permanente que impede processar este CNPJ — não retentar.
    Exemplos: procuração expirada/inválida, CNPJ não autorizado pelo portal.

    `status_coluna_d`, quando informado, é gravado na coluna D da aba 'Empresas'
    para registrar o motivo na planilha em vez de deixar a linha em branco.
    """

    def __init__(self, mensagem: str, status_coluna_d: str | None = None):
        super().__init__(mensagem)
        self.status_coluna_d = status_coluna_d


# Palavras que o portal exibe em span.mensagemErro para indicar que o CNPJ
# não pode ser representado (procuração inexistente, vencida, CNPJ inválido…).
# Erros com essas palavras levantam FalhaPermanente em vez de serem retentados.
_PALAVRAS_ERRO_PERMANENTE = (
    "procuração", "procuracao", "vencid", "expirad",
    "não possui", "sem procuração", "não encontrad",
    "cnpj inválid", "não autorizado",
)

# Recusas com status próprio na coluna D: {trecho da mensagem: status}.
# A comparação é feita sem acento e em minúsculas.
#
# "Sua autorização como procurador não permite acesso a este serviço" não casava
# com nenhuma palavra da tupla acima — ela tem "procuração" e "autorizado", e a
# mensagem traz "procurador" e "autorização". O resultado era o loop de espera
# rodar até estourar os 60s sem classificar o erro.
_ERROS_COM_STATUS = {
    "nao permite acesso a este servico": "Procuração sem autorização",
}


def _status_erro_permanente(mensagem: str) -> str | None:
    """Status para a coluna D quando a recusa é definitiva e conhecida."""
    msg = _remover_acentos(mensagem.lower())
    for trecho, status in _ERROS_COM_STATUS.items():
        if trecho in msg:
            return status
    return None


def _erro_permanente(mensagem: str) -> bool:
    """True se a mensagem do portal caracteriza recusa que não deve ser retentada."""
    if _status_erro_permanente(mensagem):
        return True
    msg = _remover_acentos(mensagem.lower())
    return any(_remover_acentos(p) in msg for p in _PALAVRAS_ERRO_PERMANENTE)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _remover_acentos(texto: str) -> str:
    """Remove acentos e diacríticos (NFD → ASCII)."""
    return unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode("ascii")


def _normalizar_cnpj(valor) -> str:
    """Remove qualquer formatação de CNPJ e retorna apenas 14 dígitos."""
    return re.sub(r"\D", "", str(valor)).zfill(14)


def _aguardar_intervalo_troca() -> None:
    """Aguarda o intervalo mínimo de 30s entre trocas de CNPJ no portal.

    Chamado sempre antes de clicar em 'Representar'. Se o intervalo já passou,
    retorna imediatamente sem bloqueio.
    """
    global _ultimo_troca_cnpj
    if _ultimo_troca_cnpj == 0.0:
        return
    decorrido = time.time() - _ultimo_troca_cnpj
    if decorrido < _INTERVALO_TROCA:
        espera = _INTERVALO_TROCA - decorrido
        print(f"    → Aguardando {espera:.0f}s (intervalo mínimo de {_INTERVALO_TROCA}s entre trocas)...")
        time.sleep(espera)


def _goto_seguro(page, url: str, label: str = "", timeout: int = 60_000) -> None:
    """Navega para `url` com logging detalhado e diagnóstico em caso de falha.

    Usa wait_until='domcontentloaded' em vez de 'networkidle':
    portais Angular mantêm conexões abertas e nunca atingem networkidle
    dentro de 30s, causando TimeoutError mesmo quando a página está pronta.
    O conteúdo real é verificado por wait_for() nos elementos seguintes.
    """
    prefixo = f"[{label}] " if label else ""
    url_antes = page.url
    print(f"    → {prefixo}Navegando para {url}\n"
          f"          (URL atual: {url_antes[:100]})")
    _t0 = time.time()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except Exception as _err:
        _elapsed = time.time() - _t0
        _url_apos = page.url
        try:
            _titulo = page.title()
        except Exception:
            _titulo = "(indisponível)"
        print(
            f"    [!] {prefixo}Falha na navegação após {_elapsed:.1f}s:\n"
            f"          Erro       : {type(_err).__name__}: {_err}\n"
            f"          URL origem : {url_antes}\n"
            f"          URL destino: {url}\n"
            f"          URL atual  : {_url_apos}\n"
            f"          Título pág.: {_titulo!r}"
        )
        raise
    _elapsed = time.time() - _t0
    print(f"    [✓] {prefixo}Carregado em {_elapsed:.1f}s. URL: {page.url[:100]}")


def _aguardar_networkidle(page, timeout: int = 60_000, label: str = "") -> None:
    """Aguarda networkidle com fallback gracioso para SPAs Angular.

    SPAs Angular podem manter conexões abertas indefinidamente.
    Em vez de lançar exceção no timeout, registra o aviso e prossegue —
    o elemento-alvo é verificado pelo wait_for() da etapa seguinte.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception as _err:
        _label_txt = f"[{label}] " if label else ""
        print(f"    → {_label_txt}networkidle não atingido "
              f"({type(_err).__name__}) — prosseguindo. URL: {page.url[:80]}")


# ── Certificados ──────────────────────────────────────────────────────────────

def _resolver_dir_certificados() -> Path:
    """Resolve a pasta de certificados na seguinte ordem:

    1. C:\\Certificados                    — caminho padrão (máquina do dev)
    2. <pasta do script>\\Certificados     — ao lado do main.py / iniciar.bat
    3. Dialog tkinter                      — usuário seleciona manualmente
    """
    import tkinter as tk
    from tkinter import filedialog, messagebox

    def _valida(p: Path) -> bool:
        return p.is_dir() and (p / "senhas.json").exists()

    # 1. Caminho padrão
    padrao = Path(r"C:\Certificados")
    if _valida(padrao):
        print(f"[cert] Pasta de certificados: {padrao}")
        return padrao

    # 2. Pasta 'Certificados' ao lado do executável (frozen) ou do script (dev)
    _base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    local = _base / "Certificados"
    if _valida(local):
        print(f"[cert] Pasta de certificados: {local}")
        return local

    # 3. Nenhum caminho padrão encontrado — pede ao usuário
    print("[!] Pasta de certificados não encontrada nos caminhos padrão.")
    print("    Abrindo seletor de pasta...")

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    while True:
        pasta = filedialog.askdirectory(
            title="Selecione a pasta com os certificados (.pfx) e senhas.json",
            parent=root,
        )

        if not pasta:
            continuar = messagebox.askyesno(
                "Certificados não encontrados",
                "Nenhuma pasta selecionada.\nDeseja tentar novamente?",
                parent=root,
            )
            if not continuar:
                root.destroy()
                print("[!] Operação cancelada pelo usuário. Encerrando.")
                sys.exit(1)
            continue

        caminho = Path(pasta)
        if not (caminho / "senhas.json").exists():
            messagebox.showwarning(
                "Pasta inválida",
                f"O arquivo 'senhas.json' não foi encontrado em:\n{pasta}\n\n"
                "Selecione a pasta correta.",
                parent=root,
            )
            continue

        root.destroy()
        print(f"[cert] Pasta de certificados informada pelo usuário: {caminho}")
        return caminho


def carregar_certificados() -> dict[str, tuple[Path, str]]:
    """Lê senhas.json e retorna {nome_normalizado: (pfx_path, senha)}."""
    with open(SENHAS_JSON, encoding="utf-8") as f:
        senhas: dict[str, str] = json.load(f)
    return {
        _remover_acentos(Path(nome).stem.lower()): (CERTIFICADOS_DIR / nome, senha)
        for nome, senha in senhas.items()
    }


def _palavras_cert(texto: str) -> set[str]:
    """Palavras normalizadas de um nome de certificado, sem acento nem pontuação."""
    return {p for p in re.split(r"[^a-z0-9]+", _remover_acentos(str(texto).lower())) if p}


def _buscar_certificado(nome: str, certs: dict) -> str | None:
    """Resolve o nome escrito na planilha para uma chave de `certs`.

    Ordem de tentativa:
        1. Igualdade exata do nome normalizado.
        2. Palavras inteiras — todas as palavras do nome aparecem no certificado.
           É o caso comum: a planilha traz "Cristiano" e o arquivo é
           "Cristiano Vasconcelos.pfx". difflib sozinho não resolvia isso, porque
           compara as strings inteiras e a razão fica em 0,60, abaixo do cutoff.
        3. Substring bidirecional ("save tec" → "save tecnologia").
        4. Aproximação por difflib.

    Nos passos 2 a 4, empate NÃO é resolvido por chute: com "Save Inteligência" e
    "Save Tecnologia" na pasta, o nome "Save" descreve os dois, e usar o errado
    significa autenticar na conta de outra empresa. A ambiguidade é reportada e a
    linha fica sem certificado.
    """
    chave = _remover_acentos(Path(nome).stem.lower())
    if chave in certs:
        return chave

    def _decidir(candidatos: list[str], criterio: str) -> str | None:
        if len(candidatos) == 1:
            print(f"    → Certificado '{nome}' resolvido para "
                  f"'{candidatos[0]}' ({criterio}).")
            return candidatos[0]
        print(f"    [!] Nome de certificado ambíguo: '{nome}' ({criterio}) "
              f"corresponde a {len(candidatos)}: {', '.join(sorted(candidatos))}.")
        print(f"         Escreva na planilha um nome que identifique só um deles.")
        return None

    # 2. Todas as palavras do nome aparecem como palavras inteiras no certificado
    palavras = _palavras_cert(chave)
    por_palavra = [k for k in certs if palavras and palavras <= _palavras_cert(k)]
    if por_palavra:
        return _decidir(por_palavra, "palavras inteiras")

    # 3. Substring bidirecional
    por_substring = [k for k in certs if chave in k or k in chave]
    if por_substring:
        return _decidir(por_substring, "correspondência parcial")

    # 4. Aproximação
    matches = difflib.get_close_matches(chave, list(certs.keys()), n=1, cutoff=0.75)
    if matches:
        print(f"    → Certificado '{nome}' resolvido para '{matches[0]}' "
              f"(correspondência aproximada).")
        return matches[0]
    return None


def atualizar_env_certificado(pfx_path: Path, passphrase: str) -> None:
    """Atualiza CERT_PFX_PATH e CERT_PFX_PASSPHRASE no .env do LoginEcac."""
    env_path = LOGIN_ECAC_DIR / ".env"
    existentes: dict[str, str] = {}
    if env_path.exists():
        for linha in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.startswith("#"):
                chave, _, valor = linha.partition("=")
                existentes[chave.strip()] = valor.strip()
    if "GEMINI_API_KEY" not in existentes:
        existentes["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", _GEMINI_API_KEY_PADRAO)
    existentes["CERT_PFX_PATH"]       = str(pfx_path)
    existentes["CERT_PFX_PASSPHRASE"] = passphrase
    conteudo = "\n".join(f"{k}={v}" for k, v in existentes.items()) + "\n"
    env_path.write_text(conteudo, encoding="utf-8")
    print(f"    [cert] Configurado: {pfx_path.name}")


# ── Planilha ──────────────────────────────────────────────────────────────────

def ler_e_ordenar(caminho: str) -> pd.DataFrame:
    """Lê a aba 'Empresas', remove duplicatas e ordena pela coluna C (certificado)."""
    ext = Path(caminho).suffix.lower()
    if ext in (".xlsx", ".xls"):
        df = pd.read_excel(caminho, sheet_name="Empresas", dtype=str)
    else:
        df = pd.read_csv(caminho, dtype=str)

    df = df.dropna(how="all").reset_index(drop=True)

    total_antes = len(df)
    df = df.drop_duplicates(ignore_index=True)
    removidas = total_antes - len(df)
    if removidas:
        print(f"  ⚠ {removidas} linha(s) duplicada(s) removida(s) da planilha.")

    col_certificado = df.columns[2]   # Coluna C = CERTIFICADO
    df = df.sort_values(by=col_certificado, kind="stable", ignore_index=True)
    return df


# ── Sessão de planilha ────────────────────────────────────────────────────────
# Antes, cada leitura recarregava o arquivo inteiro do disco e cada escrita o
# regravava inteiro. Medido numa planilha de 1031 empresas e 20 mil linhas de
# resultado: 1,2s só para ler duas células e 2,9s por gravação, com até quatro
# gravações por CNPJ. Só a verificação de "já concluído" custava 21 minutos.
#
# Agora o arquivo é carregado uma vez e mantido em memória; as escritas apenas
# marcam a sessão como suja e o disco é tocado uma vez por CNPJ. Se o processo
# morrer no meio de um CNPJ, esse CNPJ perde o que não foi salvo — mas ele
# também não foi marcado como concluído, então a próxima execução o refaz.
_sessao_planilha: dict = {"caminho": None, "wb": None, "sujo": False, "status": None}


def _wb_sessao(caminho_planilha: str):
    """Workbook da sessão, carregado do disco só na primeira chamada."""
    if _sessao_planilha["wb"] is None or _sessao_planilha["caminho"] != caminho_planilha:
        fechar_planilha()
        _sessao_planilha["caminho"] = caminho_planilha
        _sessao_planilha["wb"] = openpyxl.load_workbook(caminho_planilha)
        _sessao_planilha["sujo"] = False
    return _sessao_planilha["wb"]


def salvar_planilha() -> bool:
    """Grava no disco se houver alteração pendente. Retorna True se gravou.

    Uma falha (arquivo aberto no Excel, por exemplo) mantém a sessão suja para
    que a próxima chamada tente de novo, em vez de descartar os dados.
    """
    if _sessao_planilha["wb"] is None or not _sessao_planilha["sujo"]:
        return False
    try:
        _sessao_planilha["wb"].save(_sessao_planilha["caminho"])
    except Exception as e:
        print(f"    [!] Falha ao salvar a planilha: {type(e).__name__}: {e}")
        registrar_erro(f"Planilha: falha ao salvar. {type(e).__name__}: {e}")
        return False
    _sessao_planilha["sujo"] = False
    return True


def fechar_planilha() -> None:
    """Salva o que estiver pendente e descarta o workbook da memória."""
    salvar_planilha()
    if _sessao_planilha["wb"] is not None:
        try:
            _sessao_planilha["wb"].close()
        except Exception:
            pass
    _sessao_planilha.update({"caminho": None, "wb": None, "sujo": False, "status": None})


def mapa_status(caminho_planilha: str) -> dict[str, tuple[str, str]]:
    """Mapa {cnpj: (coluna_D, coluna_E)} da aba 'Empresas', montado uma só vez.

    Varrer a aba a cada consulta era O(n) por CNPJ; com o mapa a consulta vira
    uma busca em dicionário. Também é o que permite filtrar as linhas já
    concluídas antes de abrir qualquer navegador.
    """
    if _sessao_planilha["status"] is None or _sessao_planilha["caminho"] != caminho_planilha:
        ws = _wb_sessao(caminho_planilha)["Empresas"]
        mapa: dict[str, tuple[str, str]] = {}
        for linha in ws.iter_rows(min_row=2):
            if not linha[0].value:
                continue
            val_d = str(linha[3].value or "").strip() if len(linha) > 3 else ""
            val_e = str(linha[4].value or "").strip() if len(linha) > 4 else ""
            mapa[_normalizar_cnpj(linha[0].value)] = (val_d, val_e)
        _sessao_planilha["status"] = mapa
    return _sessao_planilha["status"]


def _escrever_status(caminho_planilha: str, cnpj: str, valor: str,
                     coluna: int, rotulo: str) -> None:
    """Escreve `valor` na coluna indicada da linha do CNPJ na aba 'Empresas'."""
    ws = _wb_sessao(caminho_planilha)["Empresas"]

    for linha in ws.iter_rows(min_row=2):
        celula_cnpj = linha[0]   # Coluna A
        if celula_cnpj.value and _normalizar_cnpj(celula_cnpj.value) == cnpj:
            celula = linha[coluna]
            celula.value = valor
            celula.alignment = Alignment(horizontal="center", vertical="center")
            _sessao_planilha["sujo"] = True

            # Mantém o mapa em sincronia com a célula
            mapa = _sessao_planilha["status"]
            if mapa is not None:
                val_d, val_e = mapa.get(cnpj, ("", ""))
                mapa[cnpj] = (valor, val_e) if coluna == 3 else (val_d, valor)

            print(f"    [✓] Coluna {rotulo} → '{valor}'  (CNPJ {cnpj})")
            return

    print(f"    [!] CNPJ {cnpj} não encontrado na planilha para escrita em {rotulo}.")


def escrever_coluna_d(caminho_planilha: str, cnpj: str, valor: str) -> None:
    """Escreve o status do DCTFWeb na coluna D da aba 'Empresas'."""
    _escrever_status(caminho_planilha, cnpj, valor, coluna=3, rotulo="D")


def escrever_coluna_e(caminho_planilha: str, cnpj: str, valor: str) -> None:
    """Escreve o status dos Processos Fiscais na coluna E da aba 'Empresas'."""
    _escrever_status(caminho_planilha, cnpj, valor, coluna=4, rotulo="E")


def ler_status_cnpj(caminho_planilha: str, cnpj: str) -> tuple[str, str]:
    """Lê os valores das colunas D e E da aba 'Empresas' para o CNPJ dado.

    Retorna (val_d, val_e) — strings vazias quando as células estiverem em branco.
    Consulta o mapa em memória, então é uma busca em dicionário e já enxerga o
    que foi escrito nesta execução.
    """
    return mapa_status(caminho_planilha).get(cnpj, ("", ""))


def filtrar_pendentes(df: pd.DataFrame, caminho_planilha: str) -> tuple[pd.DataFrame, int]:
    """Remove do DataFrame as linhas com as colunas D e E já preenchidas.

    Roda antes de abrir o navegador. Sem isso a automação fazia login num
    certificado para só então descobrir, CNPJ a CNPJ, que todas as linhas dele
    já estavam prontas — pagando um login inteiro à toa.

    Returns:
        (df_pendentes, quantidade_de_linhas_ja_concluidas)
    """
    mapa = mapa_status(caminho_planilha)
    col_cnpj = df.columns[0]

    def _pendente(valor) -> bool:
        cnpj = _normalizar_cnpj(re.sub(r"\.0+$", "", str(valor).strip()))
        val_d, val_e = mapa.get(cnpj, ("", ""))
        return not (val_d and val_e)

    mask = df[col_cnpj].map(_pendente)
    return df[mask].reset_index(drop=True), int((~mask).sum())


def _linha_vazia(ws, idx: int) -> bool:
    """True se a linha não tem nenhum valor.

    Só o conteúdo conta. Formatação remanescente, bordas e preenchimento de
    linhas que um dia tiveram dados e foram apagadas são ignorados — é por isso
    que não se pode usar ws.max_row / ws.append() aqui: eles enxergam essas
    linhas fantasma como ocupadas e empurram a gravação para muito abaixo.
    """
    if idx > ws.max_row:
        return True
    return all(
        celula.value is None or str(celula.value).strip() == ""
        for celula in ws[idx]
    )


def _proximas_linhas_vazias(ws, quantidade: int) -> list[int]:
    """Índices das próximas `quantidade` linhas vazias, varrendo de cima para baixo.

    Começa na linha 2 (linha 1 é o cabeçalho) e devolve toda linha sem conteúdo,
    tenha ela sido usada antes ou não. Linhas ocupadas são puladas, nunca
    sobrescritas — então se a lacuna do topo for menor que o volume de dados, o
    restante continua depois da última linha ocupada.
    """
    livres: list[int] = []
    idx = 2
    while len(livres) < quantidade:
        if _linha_vazia(ws, idx):
            livres.append(idx)
        idx += 1
    return livres


def _anexar_linhas(ws, linhas: list[list]) -> list[int]:
    """Escreve cada linha na próxima linha vazia. Retorna os índices usados."""
    destinos = _proximas_linhas_vazias(ws, len(linhas))
    for destino, valores in zip(destinos, linhas):
        for col, valor in enumerate(valores, start=1):
            ws.cell(row=destino, column=col, value=valor)
    return destinos


def _descrever_destinos(destinos: list[int]) -> str:
    """Resumo legível das linhas usadas, sinalizando quando não são contíguas."""
    if not destinos:
        return "nenhuma linha"
    if len(destinos) == 1:
        return f"L{destinos[0]}"
    contiguo = destinos == list(range(destinos[0], destinos[0] + len(destinos)))
    faixa = f"L{destinos[0]}-L{destinos[-1]}"
    return faixa if contiguo else f"{faixa} (com saltos)"


def escrever_aba_debitos(caminho_planilha: str, dados: list[dict]) -> None:
    """Adiciona os dados extraídos da tabela DCTFWeb na aba 'Débitos' da planilha.

    Se a aba ainda não existir, ela é criada com cabeçalho.
    Os dados são sempre acrescentados após a última linha preenchida.
    """
    wb = _wb_sessao(caminho_planilha)

    if "Débitos" not in wb.sheetnames:
        ws = wb.create_sheet("Débitos")
        cabecalho = [
            "CNPJ", "TIPO", "TRIBUTO", "Rec.", "PA/Ex.",
            "Dt.Vcto.", "Valor Original", "Saldo Devedor",
        ]
        ws.append(cabecalho)
    else:
        ws = wb["Débitos"]

    destinos = _anexar_linhas(ws, [
        [
            d.get("cnpj", ""),
            d.get("tipo", ""),
            d.get("tributo", ""),
            d.get("receita", ""),
            d.get("pa_ex", ""),
            d.get("dt_vcto", ""),
            d.get("valor_original", ""),
            d.get("saldo", ""),
        ]
        for d in dados
    ])

    _sessao_planilha["sujo"] = True
    print(f"    [✓] Aba 'Débitos': {len(dados)} linha(s) gravada(s) em "
          f"{_descrever_destinos(destinos)}.")


# ── DCTFWeb: extração da tabela ────────────────────────────────────────────────

def _selecionar_n_por_pagina(page, n: int) -> None:
    """Seleciona N itens por página no ng-select de paginação (div.pagination-per-page).

    O ng-dropdown-panel é renderizado fora do container (appendTo body), por isso a
    opção é buscada diretamente pela span.ng-option-label com texto exato.
    """
    try:
        ng_sel = page.locator('div.pagination-per-page ng-select').first
        ng_sel.wait_for(state="visible", timeout=10_000)
        ng_sel.click()
        page.wait_for_timeout(600)

        opcao = page.locator(f'span.ng-option-label:text-is("{n}")').first
        opcao.wait_for(state="visible", timeout=5_000)
        opcao.click()
        page.wait_for_timeout(1_500)
        print(f"    [✓] Itens por página: {n}.")
    except Exception as exc:
        print(f"    [!] Não foi possível mudar para {n} itens/página: {exc}")


def _expandir_todas_as_linhas(page) -> None:
    """Expande todas as linhas da tabela de uma só vez via JavaScript.

    Um único evaluate clica todos os botões chevron-down simultaneamente,
    eliminando os N roundtrips Playwright + N × 350 ms da abordagem anterior.
    Aguarda 1 s para o Angular processar todas as mudanças de estado.
    """
    contagem = page.evaluate(
        """
        () => {
            const btns = Array.from(
                document.querySelectorAll('button.br-button.circle.small')
            ).filter(b => b.querySelector('i.fa-chevron-down'));
            btns.forEach(b => b.click());
            return btns.length;
        }
        """
    )
    if contagem:
        page.wait_for_timeout(1_000)   # aguarda Angular renderizar tudo
        print(f"    → {contagem} linha(s) expandida(s).")
    else:
        print("    → Nenhuma linha para expandir.")


def _extrair_dados_pagina(page, cnpj: str) -> list[dict]:
    """Extrai as linhas de dados visíveis na tabela DCTFWeb (JavaScript evaluate)."""
    return page.evaluate(
        """
        (cnpj) => {
            const resultado = [];

            /* Linhas principais: <tr> que contêm o botão de expandir */
            const linhas = document.querySelectorAll(
                'tbody tr:has(button.br-button.circle.small)'
            );

            for (const tr of linhas) {
                const tds = Array.from(tr.querySelectorAll('td'));

                /* Receita: única <td class="text-nowrap"> */
                const tdReceita = tds.find(td => td.classList.contains('text-nowrap'));
                const receita   = tdReceita?.textContent?.trim() ?? '';

                /* Saldo devedor consolidado: última <td class="text-right"> */
                const tdsSaldoDir = tds.filter(td => td.classList.contains('text-right'));
                const saldo = tdsSaldoDir[tdsSaldoDir.length - 1]
                                ?.textContent?.trim() ?? '';

                /* Tipo (Situação do débito): <td> que contém <span class="text-nowrap"> */
                const tdTipo = tds.find(td => td.querySelector('span.text-nowrap'));
                const tipo   = tdTipo?.querySelector('span.text-nowrap')
                                      ?.textContent?.trim() ?? '';

                /* <td> sem classe especial, sem botão e sem input (checkbox)
                   Ordem esperada no DOM: PA/Ex., Dt.Vcto., Saldo devedor (R$) */
                const tdsSimples = tds.filter(td =>
                    !td.classList.contains('text-nowrap') &&
                    !td.classList.contains('text-right') &&
                    !td.querySelector('span.text-nowrap') &&
                    !td.querySelector('button.br-button') &&
                    !td.querySelector('input')
                );
                const pa_ex   = tdsSimples[0]?.textContent?.trim() ?? '';
                const dt_vcto = tdsSimples[1]?.textContent?.trim() ?? '';

                /* Detalhe expandido: próxima <tr> irmã, revelada ao clicar na seta */
                let tributo       = '';
                let valor_original = '';
                const proxTr = tr.nextElementSibling;
                if (proxTr && proxTr.tagName === 'TR') {
                    const labels = proxTr.querySelectorAll('p.label');
                    for (const labelEl of labels) {
                        const labelText = labelEl.textContent.trim();
                        const divPai    = labelEl.closest('div');
                        const ps        = divPai
                            ? Array.from(divPai.querySelectorAll('p'))
                            : [];
                        /* Valor é o primeiro <p> que não tem class="label" */
                        const valorEl = ps.find(p => !p.classList.contains('label'));
                        const valor   = valorEl?.textContent?.trim() ?? '';
                        if (labelText === 'Tributo')              tributo        = valor;
                        if (labelText === 'Valor original (R$)')  valor_original = valor;
                    }
                }

                resultado.push({
                    cnpj, tipo, tributo, receita,
                    pa_ex, dt_vcto, valor_original, saldo,
                });
            }

            return resultado;
        }
        """,
        cnpj,
    )


def extrair_debitos_dctfweb(page, cnpj: str, caminho_planilha: str) -> None:
    """Após clicar no botão 'Dívida DCTFWeb', extrai todos os dados da tabela
    (paginando se necessário) e grava na aba 'Débitos' + coluna D (Empresas)."""

    print("    → Aguardando tabela DCTFWeb carregar...")
    _aguardar_networkidle(page, label="DCTFWeb")
    page.wait_for_timeout(1_000)

    # Muda para 50 itens por página
    _selecionar_n_por_pagina(page, 50)

    todos_dados: list[dict] = []
    pagina = 1

    while True:
        print(f"    → Extraindo dados (página {pagina})...")
        page.wait_for_timeout(1_500)

        _expandir_todas_as_linhas(page)

        dados_pag = _extrair_dados_pagina(page, cnpj)
        todos_dados.extend(dados_pag)
        print(f"    → {len(dados_pag)} linha(s) extraída(s) na página {pagina}.")

        # Verifica se há próxima página habilitada
        try:
            btn_proxima = page.locator(
                'button[aria-label="Página seguinte"]'
            ).first
            if not btn_proxima.is_disabled():
                print("    → Indo para próxima página...")
                btn_proxima.click()
                _aguardar_networkidle(page, label="DCTFWeb pág.")
                pagina += 1
                continue
        except Exception:
            pass
        break

    print(f"    [✓] Total: {len(todos_dados)} linha(s) de débito DCTFWeb.")
    escrever_aba_debitos(caminho_planilha, todos_dados)
    escrever_coluna_d(caminho_planilha, cnpj, "Concluído")


# ── Processo Fiscal: extração de cards ────────────────────────────────────────

def escrever_aba_processos_fiscais(caminho_planilha: str, dados: list[dict]) -> None:
    """Adiciona linhas na aba 'Processos Fiscais' (cria se não existir)."""
    wb = _wb_sessao(caminho_planilha)

    if "Processos Fiscais" not in wb.sheetnames:
        ws = wb.create_sheet("Processos Fiscais")
        ws.append([
            "CNPJ", "TIPO", "RECEITA", "PA/Ex.", "Dt.Vcto.",
            "Valor Original", "Saldo Devedor", "Processo de Crédito",
        ])
    else:
        ws = wb["Processos Fiscais"]

    destinos = _anexar_linhas(ws, [
        [
            d.get("cnpj", ""),
            d.get("tipo", ""),
            d.get("receita", ""),
            d.get("pa_ex", ""),
            d.get("dt_vcto", ""),
            d.get("valor_original", ""),
            d.get("saldo", ""),
            d.get("processo_credito", ""),
        ]
        for d in dados
    ])

    _sessao_planilha["sujo"] = True
    print(f"    [✓] Aba 'Processos Fiscais': {len(dados)} linha(s) gravada(s) em "
          f"{_descrever_destinos(destinos)}.")


def _extrair_dados_pagina_processo(page, cnpj: str,
                                   processo_credito: str) -> list[dict]:
    """Extrai linhas da tabela de débitos de um card de processo fiscal."""
    return page.evaluate(
        """
        ([cnpj, processo_credito]) => {
            const resultado = [];
            const linhas = document.querySelectorAll(
                'tbody tr:has(button.br-button.circle.small)'
            );

            for (const tr of linhas) {
                const tds = Array.from(tr.querySelectorAll('td'));

                /* Receita: td.text-nowrap */
                const tdReceita = tds.find(td => td.classList.contains('text-nowrap'));
                const receita   = tdReceita?.textContent?.trim() ?? '';

                /* Saldo devedor: td.text-right */
                const tdSaldo = tds.find(td => td.classList.contains('text-right'));
                const saldo   = tdSaldo?.textContent?.trim() ?? '';

                /* Tipo: td contendo span.text-nowrap */
                const tdTipo = tds.find(td => td.querySelector('span.text-nowrap'));
                const tipo   = tdTipo?.querySelector('span.text-nowrap')
                                      ?.textContent?.trim() ?? '';

                /* TDs simples (sem classe, sem botão, sem input) → PA/Ex., Dt.Vcto. */
                const tdsSimples = tds.filter(td =>
                    !td.classList.contains('text-nowrap') &&
                    !td.classList.contains('text-right') &&
                    !td.querySelector('span.text-nowrap') &&
                    !td.querySelector('button.br-button') &&
                    !td.querySelector('input')
                );
                const pa_ex   = tdsSimples[0]?.textContent?.trim() ?? '';
                const dt_vcto = tdsSimples[1]?.textContent?.trim() ?? '';

                /* Valor original: seção expandida (próxima <tr> irmã) */
                let valor_original = '';
                const proxTr = tr.nextElementSibling;
                if (proxTr && proxTr.tagName === 'TR') {
                    const labels = proxTr.querySelectorAll('p.label');
                    for (const labelEl of labels) {
                        const labelText = labelEl.textContent.trim();
                        const divPai    = labelEl.closest('div');
                        const ps        = divPai
                            ? Array.from(divPai.querySelectorAll('p'))
                            : [];
                        const valorEl = ps.find(p => !p.classList.contains('label'));
                        const valor   = valorEl?.textContent?.trim() ?? '';
                        if (labelText === 'Valor original (R$)') valor_original = valor;
                    }
                }

                resultado.push({
                    cnpj, tipo, receita, pa_ex, dt_vcto,
                    valor_original, saldo, processo_credito,
                });
            }

            return resultado;
        }
        """,
        [cnpj, processo_credito],
    )


def _processar_card_processo(page, cnpj: str) -> list[dict]:
    """Extrai dados de um card de processo fiscal já aberto (nova página).

    Retorna lista de dicts com as linhas da tabela de débitos do card.
    """
    _aguardar_networkidle(page, label="card")
    page.wait_for_timeout(1_000)

    # ── "Processo de crédito" (expande se existir) ────────────────────────────
    processo_credito = ""
    btn_cred = page.locator(
        'button[aria-label="Expandir processo de crédito"]'
    ).first
    try:
        if btn_cred.is_visible(timeout=3_000):
            btn_cred.click()
            page.wait_for_timeout(800)
            # Após o clique o Angular muda o aria-label do botão (Expandir → Recolher),
            # por isso NÃO buscamos o botão novamente no JS.
            # Buscamos diretamente o div revelado: div.processo-credito
            try:
                div_proc = page.locator('div.processo-credito').first
                div_proc.wait_for(state="visible", timeout=3_000)
                processo_credito = (div_proc.text_content() or "").strip()
            except Exception:
                # Fallback via JS — cobre o caso de o div já existir mas sem estado "visible"
                processo_credito = page.evaluate(
                    "() => {"
                    "  const d = document.querySelector('div.processo-credito');"
                    "  return d ? d.textContent.trim() : '';"
                    "}"
                )
            print(f"    → Processo de crédito: {processo_credito}")
    except Exception:
        pass

    # ── Tabela de débitos do card ─────────────────────────────────────────────
    _selecionar_n_por_pagina(page, 50)

    todos_dados: list[dict] = []
    pagina = 1

    while True:
        print(f"    → Extraindo débitos do card (página {pagina})...")
        page.wait_for_timeout(1_500)
        _expandir_todas_as_linhas(page)

        dados_pag = _extrair_dados_pagina_processo(page, cnpj, processo_credito)
        todos_dados.extend(dados_pag)
        print(f"    → {len(dados_pag)} linha(s) na página {pagina}.")

        try:
            btn_prox = page.locator('button[aria-label="Página seguinte"]').first
            if not btn_prox.is_disabled():
                btn_prox.click()
                _aguardar_networkidle(page, label="card pág.")
                pagina += 1
                continue
        except Exception:
            pass
        break

    return todos_dados


def extrair_processo_fiscal(page, cnpj: str, caminho_planilha: str) -> None:
    """Percorre todos os cards de processo fiscal, extrai os dados de cada um
    e grava na aba 'Processos Fiscais' da planilha."""

    print("    → Aguardando página de processos fiscais carregar...")
    _aguardar_networkidle(page, label="proc.fiscal")
    page.wait_for_timeout(1_000)

    # Mostra 20 cards por página
    _selecionar_n_por_pagina(page, 20)

    todos_dados: list[dict] = []
    pagina_cards = 1
    SELETOR_CARD = (
        'button[aria-label="Expandir informações complementares do processo fiscal"]'
    )

    while True:
        print(f"    → Processando cards (página {pagina_cards})...")
        page.wait_for_timeout(1_500)

        total_cards = page.locator(SELETOR_CARD).count()
        print(f"    → {total_cards} card(s) nesta página.")

        for i in range(total_cards):
            print(f"    → Entrando no card {i + 1}/{total_cards}...")
            # Rebusca o botão fresco a cada iteração (evita stale reference)
            btn_card = page.locator(SELETOR_CARD).nth(i)
            btn_card.scroll_into_view_if_needed()
            btn_card.click()

            dados_card = _processar_card_processo(page, cnpj)
            todos_dados.extend(dados_card)

            # Volta para a lista de cards
            page.go_back()
            _aguardar_networkidle(page, label="voltar")
            page.wait_for_timeout(1_000)

        # Verifica próxima página de cards
        try:
            btn_prox = page.locator('button[aria-label="Página seguinte"]').first
            if not btn_prox.is_disabled():
                print("    → Indo para próxima página de cards...")
                btn_prox.click()
                _aguardar_networkidle(page, label="cards pág.")
                pagina_cards += 1
                continue
        except Exception:
            pass
        break

    print(f"    [✓] Total: {len(todos_dados)} linha(s) de processo fiscal.")
    escrever_aba_processos_fiscais(caminho_planilha, todos_dados)
    escrever_coluna_e(caminho_planilha, cnpj, "Concluído")


# ── Navegação — Portal ────────────────────────────────────────────────────────

def _fazer_logout(page) -> None:
    """Encerra a sessão no portal servicos.receitafederal.gov.br.

    Sequência:
        1. Clica no avatar (#avatar-dropdown-trigger) para abrir o menu.
        2. Clica em 'Sair' (#btn-sair).
        3. Confirma clicando no botão primário 'Sair' (button.br-button.is-primary).
    """
    print("    → Fazendo logout do portal...")
    try:
        avatar = page.locator('#avatar-dropdown-trigger').first
        avatar.wait_for(state="visible", timeout=8_000)
        avatar.click()
        page.wait_for_timeout(600)

        btn_sair = page.locator('#btn-sair').first
        btn_sair.wait_for(state="visible", timeout=5_000)
        btn_sair.click()
        page.wait_for_timeout(600)

        btn_confirmar = page.locator('button.br-button.is-primary').first
        btn_confirmar.wait_for(state="visible", timeout=5_000)
        btn_confirmar.click()
        page.wait_for_timeout(1_500)

        print("    [✓] Logout realizado com sucesso.")
    except Exception as e:
        print(f"    [!] Logout não foi possível (página talvez já encerrada): {e}")


def _fechar_navegador(p, context, page=None) -> None:
    """Faz logout (se `page` fornecida), fecha o contexto e para o Playwright."""
    if page is not None:
        _fazer_logout(page)
    try:
        context.close()
    except Exception:
        pass
    try:
        p.stop()
    except Exception:
        pass
    print("    [✓] Navegador fechado.")


def trocar_perfil_procurador(page, cnpj: str) -> None:
    """Aguarda o intervalo de 30s, depois representa o CNPJ como Procurador
    no portal e navega para a página de pendências.

    Pode ser chamado tanto para o primeiro CNPJ (logo após entrar no portal)
    quanto para os seguintes (sem precisar voltar ao eCAC).
    """
    global _ultimo_troca_cnpj

    # ── Garante intervalo mínimo de 30s entre trocas ──────────────────────────
    _aguardar_intervalo_troca()

    _MAX_TENTATIVAS_REPR = 3

    for _tentativa_repr in range(1, _MAX_TENTATIVAS_REPR + 1):

        if _tentativa_repr > 1:
            print(
                f"    → Retentando representação "
                f"(tentativa {_tentativa_repr}/{_MAX_TENTATIVAS_REPR})..."
            )
            # Fecha dropdown se ainda estiver aberto
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            except Exception:
                pass
            page.wait_for_timeout(2_000)

        # ── Tutorial pós-login ────────────────────────────────────────────────
        # Rede de segurança: se estiver aberto, cobre a tela e intercepta o
        # clique no avatar. Timeout 0 para não pagar espera a cada CNPJ — quem
        # espera pelo tutorial é fazer_login(), uma vez por sessão.
        fechar_tutorial_pos_login(page, timeout_ms=0)

        # ── Abre menu do avatar ───────────────────────────────────────────────
        print(f"    → Abrindo menu do certificado...")
        avatar = page.locator('xpath=//*[@id="avatar-dropdown-trigger"]').first
        avatar.wait_for(state="visible", timeout=15_000)
        avatar.click()
        page.wait_for_timeout(600)

        # ── Preenche CNPJ ─────────────────────────────────────────────────────
        print(f"    → Digitando CNPJ {cnpj} no campo de representação...")
        campo_cnpj = page.locator('xpath=//*[@id="input-representar-cpfcnpj"]').first
        campo_cnpj.wait_for(state="visible", timeout=10_000)
        campo_cnpj.fill(cnpj)
        page.wait_for_timeout(400)

        # ── Seleciona Procurador ──────────────────────────────────────────────
        print("    → Selecionando 'Procurador' no dropdown...")
        ng_select = page.locator(
            'xpath=//*[@id="formularioRepresentacao"]/form/div/div[2]/br-select/div/div/div[1]/ng-select'
        ).first
        ng_select.wait_for(state="visible", timeout=10_000)
        ng_select.click()
        page.wait_for_timeout(400)

        opcao = page.get_by_role("option", name="Procurador").first
        opcao.wait_for(state="visible", timeout=5_000)
        opcao.click()
        page.wait_for_timeout(400)

        # ── Clica Representar ─────────────────────────────────────────────────
        print("    → Clicando em 'Representar'...")
        btn_representar = page.locator(
            'xpath=//*[@id="formularioRepresentacao"]/form/div/button'
        ).first
        btn_representar.wait_for(state="visible", timeout=10_000)

        # Listener de popup configurado ANTES do clique
        _popups: list = []

        def _on_new_page(p):
            _popups.append(p)

        page.context.on("page", _on_new_page)
        btn_representar.click()

        # Inicia cronômetro imediatamente após clicar em Representar
        _ultimo_troca_cnpj = time.time()

        # ── Aguarda "Carregando" APARECER antes de checar captcha ────────────
        # O captcha pode aparecer POR CIMA do spinner "Carregando".
        # Basta aguardar o spinner aparecer para saber que o servidor recebeu
        # o clique; não esperamos ele sumir — isso ocorre após resolver captcha.
        print("    → Aguardando 'Carregando' aparecer...")
        _carregando = page.locator(
            'xpath=/html/body/app-root/mf-portal-layout/portal-main-layout'
            '/br-loading/div/div/a/div[2]'
        ).first
        _carregando_apareceu = False
        try:
            _carregando.wait_for(state="visible", timeout=8_000)
            _carregando_apareceu = True
            print("    → [Carregando...] detectado.")
        except Exception:
            pass  # spinner não apareceu (resposta muito rápida) — segue

        # ── Espera ativa (até 25 s): erro / popup / captcha / confirmação ─────
        # Prioridade de verificação a cada ~800 ms:
        #   0. Erro "acesso automatizado" (span.mensagemErro) → retentar
        #   1. Popup (nova janela) → captcha em popup
        #   2. iframes hcaptcha VISIVEIS (challenge / checkbox) → captcha inline
        #   3. CNPJ mudou no cabeçalho → representação sem captcha
        _LIMITE_ESPERA_S  = 60
        _deadline_captcha = time.time() + _LIMITE_ESPERA_S
        captcha_tipo      = None    # "popup" | "inline" | None
        _srcs_logados     = False
        _erro_bloqueado   = False

        while time.time() < _deadline_captcha:

            # 0. Mensagem de erro do portal (anti-bot ou falha permanente)
            _err_msg = page.evaluate(
                "() => { const e = document.querySelector('span.mensagemErro'); "
                "return e ? e.textContent.trim() : ''; }"
            )
            if _err_msg:
                _err_lower = _err_msg.lower()
                if "automatizado" in _err_lower or "bloqueado" in _err_lower:
                    print(f"    → [!] Erro anti-bot: '{_err_msg}'")
                    _erro_bloqueado = True
                    break
                if _erro_permanente(_err_msg):
                    raise FalhaPermanente(
                        f"Portal recusou CNPJ {cnpj}: '{_err_msg}'",
                        status_coluna_d=_status_erro_permanente(_err_msg),
                    )

            # 1. Popup nova janela
            if _popups:
                captcha_tipo = "popup"
                break

            # 2. Captcha challenge ATIVO — verificado por múltiplos seletores internos
            # ─────────────────────────────────────────────────────────────────────
            # Iframes frame=challenge ficam pré-carregados no DOM mesmo sem captcha
            # ativo. Para evitar falsos positivos, executamos JavaScript DENTRO do
            # iframe (cross-origin acessível pelo Playwright via frame.evaluate) e
            # exigimos que TODOS os critérios abaixo sejam satisfeitos ao mesmo tempo:
            #
            #   1. .challenge-container   → existe E tem dimensões ≥ 100×100 px
            #   2. .prompt-text           → existe E tem texto não-vazio
            #                               (ex: "Toque em todos os seres vivos")
            #   3. .task-grid             → existe (grade de imagens do desafio)
            #   4. .button-submit         → existe E aria-disabled ≠ "true"
            #
            # Se qualquer critério falhar → captcha não está ativo.
            _hc_frames = [
                f for f in page.frames
                if "hcaptcha.com" in (f.url or "")
                and "frame=challenge" in (f.url or "")
            ]

            # Diagnóstico: loga srcs uma vez quando frames aparecerem no DOM
            if _hc_frames and not _srcs_logados:
                _srcs_logados = True
                print(f"    → hcaptcha: {len(_hc_frames)} frame(s) challenge no DOM.")
                for _hf in _hc_frames[:3]:
                    print(f"      src: {(_hf.url or '')[:220]}")

            _captcha_texto = None   # instrução do desafio, se ativo
            for _hf in _hc_frames:
                try:
                    _captcha_texto = _hf.evaluate("""() => {
                        // 1. challenge-container com dimensões reais
                        const container = document.querySelector('.challenge-container');
                        if (!container) return null;
                        const r = container.getBoundingClientRect();
                        if (r.width < 100 || r.height < 100) return null;

                        // 2. prompt-text com instrução preenchida
                        const prompt = document.querySelector('.prompt-text');
                        if (!prompt || !prompt.textContent.trim()) return null;

                        // 3. botão submit habilitado
                        // Nota: NÃO verificamos .task-grid pois só existe no tipo grade 3x3.
                        //       O tipo "imagem única" não tem .task-grid mas é igualmente válido.
                        const btn = document.querySelector('.button-submit');
                        if (!btn || btn.getAttribute('aria-disabled') === 'true') return null;

                        return prompt.textContent.trim();
                    }""")
                    if _captcha_texto:
                        break
                except Exception:
                    pass

            if _captcha_texto:
                print(f"    → Captcha ATIVO: '{_captcha_texto}'. Resolvendo...")
                captcha_tipo = "inline"
                break

            # 3. CNPJ já mudou no cabeçalho? (botão avatar, sempre visível)
            _cnpj_pag = page.evaluate(
                "() => {"
                "  const h = document.querySelector('.ni-pessoa:not(.ni-representante)');"
                "  if (h) return h.textContent.trim();"
                "  const r = document.querySelector('.ni-representacao');"
                "  return r ? r.textContent.trim() : '';"
                "}"
            )
            if re.sub(r"\D", "", _cnpj_pag).zfill(14) == cnpj:
                print("    → Representação concluída sem captcha!")
                break

            # 4. "Carregando" sumiu = servidor respondeu sem captcha aparecer
            if _carregando_apareceu and not _carregando.is_visible():
                print("    → [Carregando...] sumiu. Sem captcha necessário.")
                break

            page.wait_for_timeout(800)

        try:
            page.context.remove_listener("page", _on_new_page)
        except Exception:
            pass

        # ── Erro anti-bot → retentar ou desistir ─────────────────────────────
        if _erro_bloqueado:
            if _tentativa_repr < _MAX_TENTATIVAS_REPR:
                continue  # abre menu, preenche, clica de novo
            raise Exception(
                f"Acesso bloqueado por anti-bot após {_MAX_TENTATIVAS_REPR} "
                "tentativa(s). Reprocessar manualmente mais tarde."
            )

        # ── Resolve captcha conforme tipo ─────────────────────────────────────
        _captcha_repr_ok = True   # False se 2 tentativas falharem → retry repr
        if captcha_tipo == "popup":
            popup = _popups[0]
            print("    → Popup de captcha detectada. Resolvendo...")
            _popup_ok = False
            for tentativa in range(1, 3):   # máx. 2 tentativas
                try:
                    if solve_hcaptcha(popup):
                        print(f"    [✓] Captcha resolvido (tentativa {tentativa}/2).")
                        _popup_ok = True
                        break
                except Exception as e:
                    print(f"    → Captcha tentativa {tentativa}/2: {type(e).__name__}: {e}")
                if tentativa < 2:
                    page.wait_for_timeout(2_000)
            if not _popup_ok:
                _captcha_repr_ok = False
            try:
                popup.wait_for_close(timeout=15_000)
                print("    [✓] Popup do captcha fechada.")
            except Exception:
                pass
        elif captcha_tipo == "inline":
            _inline_ok = False
            for tentativa in range(1, 3):   # máx. 2 tentativas
                try:
                    if solve_hcaptcha(page):
                        print(f"    [✓] Captcha resolvido (tentativa {tentativa}/2).")
                        _inline_ok = True
                        break
                except Exception as e:
                    print(f"    → Captcha tentativa {tentativa}/2: {type(e).__name__}: {e}")
                if tentativa < 2:
                    page.wait_for_timeout(2_000)
            if not _inline_ok:
                _captcha_repr_ok = False
        else:
            print("    → Nenhum captcha detectado. Aguardando confirmação...")

        # Captcha não resolvido em 2 tentativas → reinicia o fluxo de representação
        if not _captcha_repr_ok:
            print("    → Captcha não resolvido em 2 tentativas. Refazendo 'Representar'...")
            if _tentativa_repr < _MAX_TENTATIVAS_REPR:
                continue
            raise Exception(
                f"Captcha não resolvido para CNPJ {cnpj} após "
                f"{_MAX_TENTATIVAS_REPR} tentativa(s)."
            )

        # ── Aguarda "Carregando" DESAPARECER após captcha ────────────────────
        # Quando captcha é resolvido, o "Carregando" ainda está visível por baixo.
        # Esperamos ele sumir para saber que o servidor processou a representação.
        # Se não houve captcha, o "Carregando" já sumiu no loop acima — esta espera
        # retorna imediatamente.
        if captcha_tipo:
            print("    → Aguardando [Carregando...] sumir após captcha...")
        try:
            _carregando.wait_for(state="hidden", timeout=30_000)
            if captcha_tipo:
                print("    → [Carregando...] sumiu.")
        except Exception:
            if captcha_tipo:
                # Página travou no spinner — recarrega e verifica a situação.
                # Se a representação já foi aceita pelo servidor, o CNPJ estará
                # confirmado no cabeçalho após o reload e a automação continua.
                # Se não, a verificação de CNPJ abaixo detecta e retenta.
                print("    → [!] [Carregando...] não sumiu no timeout. Recarregando página...")
                try:
                    page.reload(wait_until="domcontentloaded", timeout=60_000)
                    page.wait_for_timeout(1_500)
                    print(f"    → Página recarregada. URL: {page.url[:80]}. Verificando situação...")
                except Exception as _reload_err:
                    print(f"    → Erro ao recarregar ({type(_reload_err).__name__}): {_reload_err}")

        page.wait_for_timeout(800)

        # ── Verifica erro anti-bot após captcha ───────────────────────────────
        # O portal pode exibir "acesso bloqueado" também DEPOIS de resolver o
        # captcha, quando o servidor processa a representação e rejeita o acesso.
        _err_pos_captcha = page.evaluate(
            "() => { const e = document.querySelector('span.mensagemErro'); "
            "return e ? e.textContent.trim() : ''; }"
        )
        if _err_pos_captcha:
            _epc_lower = _err_pos_captcha.lower()
            if "automatizado" in _epc_lower or "bloqueado" in _epc_lower:
                print(f"    → [!] Erro anti-bot após captcha: '{_err_pos_captcha}'")
                if _tentativa_repr < _MAX_TENTATIVAS_REPR:
                    continue
                raise Exception(
                    f"Acesso bloqueado por anti-bot após captcha — "
                    f"{_MAX_TENTATIVAS_REPR} tentativa(s). Reprocessar manualmente."
                )
            if _erro_permanente(_err_pos_captcha):
                raise FalhaPermanente(
                    f"Portal recusou CNPJ {cnpj}: '{_err_pos_captcha}'",
                    status_coluna_d=_status_erro_permanente(_err_pos_captcha),
                )

        # ── Verifica que o CNPJ representado realmente mudou ──────────────────
        _cnpj_confirmado = False
        _cnpj_pag_final  = ""
        for _ in range(10):
            _cnpj_pag_final = page.evaluate(
                "() => {"
                "  const h = document.querySelector('.ni-pessoa:not(.ni-representante)');"
                "  if (h) return h.textContent.trim();"
                "  const r = document.querySelector('.ni-representacao');"
                "  return r ? r.textContent.trim() : '';"
                "}"
            )
            if re.sub(r"\D", "", _cnpj_pag_final).zfill(14) == cnpj:
                _cnpj_confirmado = True
                break
            page.wait_for_timeout(500)

        if _cnpj_confirmado:
            break  # ← sucesso, sai do loop de retry

        # CNPJ não confirmado → retentar se ainda houver tentativas
        if _tentativa_repr < _MAX_TENTATIVAS_REPR:
            print(
                f"    → CNPJ não confirmado (portal exibe '{_cnpj_pag_final}'). "
                "Retentando..."
            )
            continue

        raise Exception(
            f"Representação falhou para CNPJ {cnpj} após {_MAX_TENTATIVAS_REPR} "
            f"tentativa(s). Portal ainda exibe: '{_cnpj_pag_final}'."
        )

    print(f"    [✓] Perfil alterado para Procurador do CNPJ {cnpj}.")

    _goto_seguro(page, URL_PENDENCIAS, label="pendências")
    page.wait_for_timeout(500)


def verificar_pendencias(page, cnpj: str, caminho_planilha: str,
                          skip_dctfweb: bool = False,
                          skip_processo: bool = False) -> str:
    """Lê o status de pendências e toma a ação correspondente.

    Parâmetros de controle (usados quando uma das colunas já foi preenchida):
        skip_dctfweb  — True quando coluna D já está preenchida (DCTFWeb concluído).
                        Pula o botão 'Dívida DCTFWeb'; processa apenas Processo Fiscal.
        skip_processo — True quando coluna E já está preenchida (Fiscal concluído).
                        Pula o botão 'Processo Fiscal'; processa apenas DCTFWeb.

    Colunas escritas:
        D — status do DCTFWeb  ('Concluído' / 'Sem débitos' / 'Débitos não compensáveis')
        E — status do Fiscal   ('Concluído' / 'Sem Processos')

    Retorna:
        "sem_pendencia"   — sem débitos
        "nao_compensavel" — débitos não compensáveis
        "concluido"       — ao menos um fluxo foi processado nesta execução
        "desconhecido"    — status inesperado
    """
    print("    → Verificando status de pendências...")
    span_status = page.locator(XPATH_STATUS).first
    span_status.wait_for(state="visible", timeout=30_000)
    texto = (span_status.text_content() or "").strip()
    print(f"    → Status: '{texto}'")

    # ── Sem pendência ─────────────────────────────────────────────────────────
    if texto == "Sem pendência":
        if not skip_dctfweb:
            escrever_coluna_d(caminho_planilha, cnpj, "Sem débitos")
        if not skip_processo:
            escrever_coluna_e(caminho_planilha, cnpj, "Sem Processos")
        return "sem_pendencia"

    # ── Com pendência ─────────────────────────────────────────────────────────
    if texto == "Com pendência":
        btn_dctfweb = page.locator('button[aria-label*="DCTFWeb"]').first

        # Detecta presença dos botões via JS — retorna instantaneamente,
        # sem aguardar timeout de espera (página já carregada após wait_for do span).
        _botoes = page.evaluate(
            """
            () => ({
                dctfweb:  !!document.querySelector('button[aria-label*="DCTFWeb"]'),
                processo: !!document.querySelector(
                    'button[aria-label*="processo fiscal" i]'
                )
            })
            """
        )
        tem_dctfweb  = _botoes["dctfweb"]
        tem_processo = _botoes["processo"]

        # Nenhum botão de ação encontrado
        if not tem_dctfweb and not tem_processo:
            if not skip_dctfweb:
                escrever_coluna_d(caminho_planilha, cnpj, "Débitos não compensáveis")
            if not skip_processo:
                escrever_coluna_e(caminho_planilha, cnpj, "Sem Processos")
            return "nao_compensavel"

        # ── Dívida DCTFWeb ────────────────────────────────────────────────────
        if tem_dctfweb and not skip_dctfweb:
            print("    → Clicando em 'Dívida DCTFWeb'...")
            btn_dctfweb.click()
            print("    [✓] Botão 'Dívida DCTFWeb' clicado.")
            extrair_debitos_dctfweb(page, cnpj, caminho_planilha)
            # extrair_debitos_dctfweb já escreve "Concluído" na coluna D
        elif skip_dctfweb:
            print("    → DCTFWeb já processado anteriormente. Pulando...")

        # ── Processo Fiscal ───────────────────────────────────────────────────
        if tem_processo and not skip_processo:
            _goto_seguro(page, URL_ANALISE_PENDENCIAS, label="análise fiscal")
            page.wait_for_timeout(1_000)
            btn_proc_fresh = page.locator('button[aria-label*="processo fiscal"]').first
            btn_proc_fresh.wait_for(state="visible", timeout=10_000)
            btn_proc_fresh.click()
            print("    [✓] Botão 'Processo Fiscal' clicado.")
            extrair_processo_fiscal(page, cnpj, caminho_planilha)
            # extrair_processo_fiscal já escreve "Concluído" na coluna E
            if not tem_dctfweb and not skip_dctfweb:
                # Sem DCTFWeb e sem skip → marca D como concluído via Fiscal
                escrever_coluna_d(caminho_planilha, cnpj, "Concluído")
        elif tem_processo and skip_processo:
            print("    → Processos Fiscais já processados anteriormente. Pulando...")
        else:
            # Não existe botão de Processo Fiscal
            if not skip_processo:
                escrever_coluna_e(caminho_planilha, cnpj, "Sem Processos")

        return "concluido"

    # ── Status inesperado ─────────────────────────────────────────────────────
    print(f"    [!] Status não reconhecido: '{texto}'")
    return "desconhecido"


# ── Processamento por CNPJ ────────────────────────────────────────────────────

def processar_cnpj(page, cnpj: str, row: pd.Series,
                   caminho_planilha: str) -> str:
    """Executa o fluxo completo para um CNPJ no portal de Serviços RF.

    Antes de qualquer navegação, lê as colunas D e E da planilha para
    determinar o que já foi feito:

        D preenchida + E preenchida → linha já concluída, pula tudo.
        D preenchida, E vazia       → faz apenas Processos Fiscais (skip_dctfweb).
        E preenchida, D vazia       → faz apenas Débitos DCTFWeb   (skip_processo).
        Ambas vazias                → executa fluxo completo.

    O login e navegação iniciais são feitos por `fazer_login()` antes desta
    função ser chamada.
    """
    print(f"    → Processando CNPJ {cnpj}...")

    # ── Verifica o que já foi processado ─────────────────────────────────────
    val_d, val_e = ler_status_cnpj(caminho_planilha, cnpj)

    if val_d and val_e:
        print(f"    → Já totalmente processado (D='{val_d}', E='{val_e}'). Pulando.")
        return "ja_processado"

    skip_dctfweb  = bool(val_d)   # D preenchida → DCTFWeb já concluído
    skip_processo = bool(val_e)   # E preenchida → Processos Fiscais já concluídos

    if skip_dctfweb:
        print(f"    → Coluna D já preenchida ('{val_d}'). Fará apenas Processos Fiscais.")
    if skip_processo:
        print(f"    → Coluna E já preenchida ('{val_e}'). Fará apenas Débitos DCTFWeb.")

    # ── Navegação e processamento ─────────────────────────────────────────────
    # fazer_login() já autenticou no portal; ir direto para representação
    try:
        trocar_perfil_procurador(page, cnpj)

        status = verificar_pendencias(page, cnpj, caminho_planilha,
                                       skip_dctfweb=skip_dctfweb,
                                       skip_processo=skip_processo)
    except FalhaPermanente as e:
        # Registra o motivo na planilha antes de propagar, para que a recusa
        # fique visível em vez de a linha voltar em branco
        if e.status_coluna_d:
            escrever_coluna_d(caminho_planilha, cnpj, e.status_coluna_d)
        raise
    finally:
        # Único toque no disco por CNPJ. No finally para que uma falha no meio
        # do fluxo não descarte o que já foi extraído.
        salvar_planilha()

    return status


# ── Processamento principal ───────────────────────────────────────────────────

def processar(df: pd.DataFrame, certs: dict[str, tuple[Path, str]],
              caminho_planilha: str) -> None:
    """Itera pela planilha ordenada e realiza o fluxo completo para cada CNPJ.

    Estratégia:
    - Primeiro CNPJ de cada grupo de certificado: login no eCAC + entrada no portal.
    - CNPJs seguintes do mesmo grupo: apenas troca de perfil no portal
      (sem voltar ao eCAC), aguardando o intervalo mínimo de 30s.
    - Troca de certificado: fecha o navegador atual; próximo grupo faz login fresco.
    """
    rows     = list(df.iterrows())
    total    = len(rows)
    col_cnpj = df.columns[0]   # Coluna A = CNPJ
    col_cert = df.columns[2]   # Coluna C = CERTIFICADO

    cert_atual     = None
    browser_aberto = None   # (p, context, page) | None
    _MAX_RETENT_CNPJ    = 2                          # tentativas por CNPJ (inclui a 1ª)
    _retentativas_cnpj: dict[str, int] = {}          # contador por CNPJ

    i = 0
    while i < len(rows):
        idx, row = rows[i]

        cnpj_raw    = re.sub(r"\.0+$", "", str(row[col_cnpj]).strip())
        cnpj        = _normalizar_cnpj(cnpj_raw)
        certificado = str(row[col_cert]).strip()

        # Ignora linhas sem CNPJ válido
        if cnpj in ("", "nan", "None", "00000000000000"):
            print(f"  [{idx + 1}/{total}] CNPJ inválido/vazio. Ignorando linha.")
            i += 1
            continue

        # ── Troca de certificado ──────────────────────────────────────────────
        if certificado != cert_atual:
            print(f"\n{'═' * 60}")
            print(f"  Certificado: {certificado}")
            print(f"{'═' * 60}")

            # Fecha o navegador atual (faz logout no portal antes de fechar)
            if browser_aberto:
                p, context, page_atual = browser_aberto
                _fechar_navegador(p, context, page_atual)
                browser_aberto = None

            chave = _buscar_certificado(certificado, certs)
            if chave is None:
                print(f"  [!] Certificado '{certificado}' não encontrado em {SENHAS_JSON.name}.")
                print(f"       Disponíveis: {', '.join(sorted(certs.keys()))}")
                cert_atual = certificado
                i += 1
                continue

            pfx_path, passphrase = certs[chave]
            atualizar_env_certificado(pfx_path, passphrase)
            cert_atual = certificado

        chave = _buscar_certificado(cert_atual, certs)
        if chave is None:
            i += 1
            continue

        print(f"\n  [{idx + 1}/{total}] CNPJ: {cnpj}")

        # ── Login no portal (apenas quando não há sessão aberta) ─────────────────
        if browser_aberto is None:
            try:
                _res = fazer_login(
                    cert_pfx_path=str(pfx_path),
                    cert_pfx_passphrase=passphrase,
                    project_dir=LOGIN_ECAC_DIR,
                )
                if _res is None:
                    raise Exception("fazer_login() retornou None — login falhou.")
                p, context, page = _res
                browser_aberto = (p, context, page)
                print("    [✓] Login no portal concluído.")
            except Exception as e:
                print(f"    [!] Erro ao fazer login ({type(e).__name__}: {e}). Pulando CNPJ {cnpj}.")
                i += 1
                continue

        else:
            p, context, page = browser_aberto

        # ── Processa pendências deste CNPJ ────────────────────────────────────
        _avancar = True
        try:
            processar_cnpj(page, cnpj, row, caminho_planilha)

        except FalhaPermanente as e:
            # Procuração expirada/inválida ou CNPJ não autorizado — não retentar.
            print(f"    [!] Falha permanente — CNPJ {cnpj} ignorado: {e}")
            _fechar_navegador(p, context, page)
            browser_aberto = None

        except Exception as e:
            _retentativas_cnpj[cnpj] = _retentativas_cnpj.get(cnpj, 0) + 1
            _n = _retentativas_cnpj[cnpj]
            print(
                f"    [!] Erro ao processar CNPJ {cnpj} "
                f"(tentativa {_n}/{_MAX_RETENT_CNPJ}): {e}"
            )
            _fechar_navegador(p, context, page)
            browser_aberto = None
            if _n < _MAX_RETENT_CNPJ:
                print(f"    → Reabrindo sessão e retentando CNPJ {cnpj}...")
                _avancar = False   # não incrementa i; próxima iteração refaz login
            else:
                print(
                    f"    [!] Máximo de {_MAX_RETENT_CNPJ} tentativa(s) atingido. "
                    f"Pulando CNPJ {cnpj}."
                )

        if _avancar:
            i += 1

    # ── Fecha o navegador ao terminar ─────────────────────────────────────────
    if browser_aberto:
        p, context, page_final = browser_aberto
        _fechar_navegador(p, context, page_final)


# ── Entrada ───────────────────────────────────────────────────────────────────

def main() -> None:
    # ── Argumentos de linha de comando ────────────────────────────────────────
    parser = argparse.ArgumentParser(description="Automação Débitos em Aberto")
    parser.add_argument(
        "--planilha", metavar="ARQUIVO",
        help="Caminho da planilha .xlsx (pula o seletor gráfico)"
    )
    parser.add_argument(
        "--log", metavar="ARQUIVO",
        help="Grava os logs também em arquivo (ex.: execucao.log)"
    )
    args = parser.parse_args()

    # ── Logging opcional em arquivo ───────────────────────────────────────────
    if args.log:
        logging.basicConfig(
            filename=args.log,
            filemode="a",
            level=logging.DEBUG,
            format="%(asctime)s %(message)s",
            datefmt="%H:%M:%S",
            encoding="utf-8",
        )
        # Redireciona print para também escrever no log
        _log_file = open(args.log, "a", encoding="utf-8", buffering=1)
        class _Tee:
            def __init__(self, *streams): self.streams = streams
            def write(self, data):
                for s in self.streams:
                    s.write(data)
            def flush(self):
                for s in self.streams:
                    s.flush()
        sys.stdout = _Tee(sys.stdout, _log_file)
        sys.stderr = _Tee(sys.stderr, _log_file)

    # Passo 1: seleção da planilha (UI ou argumento)
    if args.planilha:
        planilha = args.planilha
    else:
        planilha = selecionar_planilha()
    if not planilha:
        print("Nenhuma planilha selecionada. Encerrando.")
        sys.exit(0)

    print(f"\nPlanilha: {planilha}")

    # Passo 2: resolve pasta de certificados (padrão → ao lado do script → dialog)
    global CERTIFICADOS_DIR, SENHAS_JSON
    CERTIFICADOS_DIR = _resolver_dir_certificados()
    SENHAS_JSON      = CERTIFICADOS_DIR / "senhas.json"

    # Carrega o .env local (traz CERT_PFX_*) e resolve a GEMINI_API_KEY antes de
    # qualquer chamada ao captcha solver
    _env_path = LOGIN_ECAC_DIR / ".env"
    if _env_path.exists():
        load_dotenv(dotenv_path=_env_path, override=True)

    _chave, _origem = _resolver_gemini_key()
    os.environ["GEMINI_API_KEY"] = _chave
    if _chave:
        print(f"Chave Gemini:  {_chave[:6]}...{_chave[-4:]}  (origem: {_origem})")
    else:
        print("  [!] GEMINI_API_KEY não encontrada — o captcha não será resolvido.")

    # Passo 3 (era 2): carrega mapeamento de certificados
    certs = carregar_certificados()

    # Passo 3: leitura e ordenação por certificado (coluna C)
    df           = ler_e_ordenar(planilha)
    col_cert     = df.columns[2]
    total_lido   = len(df)

    # Passo 4: descarta o que já está concluído ANTES de abrir o navegador.
    # Um certificado cujas linhas estejam todas prontas nem chega a ser aberto.
    _t0 = time.perf_counter()
    df, ja_concluidas = filtrar_pendentes(df, planilha)
    print(f"\nRegistros:    {total_lido}")
    print(f"Já concluídos: {ja_concluidas} (colunas D e E preenchidas) — "
          f"verificado em {time.perf_counter() - _t0:.1f}s")
    print(f"A processar:  {len(df)}")

    if df.empty:
        print("\nTodas as linhas já estão concluídas. Nada a fazer.")
        fechar_planilha()
        return

    certificados = df[col_cert].dropna().unique().tolist()
    print(f"Certificados: {len(certificados)}")
    for cert in certificados:
        qtd    = (df[col_cert] == cert).sum()
        chave  = _buscar_certificado(cert, certs)
        status = "✓" if chave is not None else "✗ NÃO ENCONTRADO"
        print(f"  {status}  {cert}  ({qtd} CNPJ{'s' if qtd > 1 else ''} pendente(s))")

    # Passo 5: fluxo completo para cada CNPJ pendente
    try:
        processar(df, certs, planilha)
    finally:
        # Garante que qualquer alteração pendente vá para o disco mesmo se o
        # processamento for interrompido
        fechar_planilha()

    print("\nProcessamento concluído.")


if __name__ == "__main__":
    main()
