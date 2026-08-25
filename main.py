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
import base64
import logging
import os
import re
import sys
import time
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

import cert_windows                                                        # noqa: E402
from servicos_rf_login import fazer_login                                  # noqa: E402
from servicos_rf_login.log_manager import registrar_erro                   # noqa: E402
from servicos_rf_login.login import fechar_tutorial_pos_login              # noqa: E402
from resolvedor_captcha import solve_hcaptcha                                  # noqa: E402
from ui_upload import main as selecionar_planilha                         # noqa: E402

from automation.domain import buscar_certificado                            # noqa: E402
from automation.domain import remover_acentos as _remover_acentos          # noqa: E402

# Os certificados vêm do Windows Certificate Store (Cert:\CurrentUser\My), não
# mais de uma pasta com .pfx e um senhas.json ao lado.

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
# Classe específica do botão gov.br — mais robusto que XPath posicional
# (o botão contém <img alt="gov.br">, por isso has-text("gov.br") não funciona)
BTN_ENTRAR_GOV  = 'button.login-banner-button'

# ── Timer de troca de CNPJ ────────────────────────────────────────────────────
# O portal não permite trocar de CNPJ com intervalo menor que 30 segundos.
_ultimo_troca_cnpj: float = 0.0
_INTERVALO_TROCA           = 30   # segundos


# ── Classificação de recusas do portal ────────────────────────────────────────
# A regra vive em automation/status_portal.py: entra o texto que o portal
# devolveu, sai "encerra a linha" ou "tenta de novo". Aqui só os apelidos usados
# no restante do módulo.
#
# As três constantes e `_erro_permanente` são reexportadas porque a suíte de
# caracterização afirma sobre elas em `main`; somem quando os pontos de chamada
# da navegação passarem a falar direto com o domínio.
from automation.status_portal import PALAVRAS_RECUSA_PERMANENTE as _PALAVRAS_ERRO_PERMANENTE
from automation.status_portal import RECUSAS_COM_STATUS as _ERROS_COM_STATUS
from automation.status_portal import STATUS_D_TERMINAIS as _STATUS_D_TERMINAIS
from automation.boundary import EntradaDebitosEmAberto, EntradaInvalida, montar_entrada
from automation import captcha, certificados_windows, consulta_fiscal, login
from automation import planilha, representacao
from patchright.sync_api import Error as PlaywrightError
from automation.planilha import PlanilhaIndisponivel, validar_recurso
from automation.status_portal import ANTIBOT as _ANTIBOT
from automation.status_portal import RECUSA_DO_CNPJ as _RECUSA_DO_CNPJ
from automation.status_portal import FalhaPermanente
from automation.status_portal import classificar_mensagem as _classificar_mensagem
from automation.status_portal import recusa_permanente as _erro_permanente
from automation.status_portal import status_da_recusa as _status_erro_permanente
from automation.status_portal import status_encerra_linha as _status_encerra_linha


# ── Helpers ───────────────────────────────────────────────────────────────────

_normalizar_cnpj = planilha.normalizar_cnpj


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


def _autenticar(cert_subject_cn: str, cert_serial: str, auto_select: bool):
    """TRANSITIONAL — ponte entre o laço legado e a fronteira de login.

    A chave do Gemini ainda é lida de `os.environ` AQUI, e só aqui: daqui para
    baixo ela viaja explicitamente até o solver. O `os.environ` sobrevive porque
    o modo desktop/executável legado continua populando-o (LEGACY_SECRET_LOADING).

    Condição de remoção: quando o runner construir `ConfigLogin` a partir do
    input da execução, esta função e a leitura do ambiente saem juntas.
    """
    config = login.ConfigLogin(
        diretorio_perfil=str(LOGIN_ECAC_DIR),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
    )
    certificado = login.Certificado(subject_cn=cert_subject_cn, serial=cert_serial)
    return login.autenticar(certificado, config, auto_select, fazer_login=fazer_login)


def _resolver_captcha(alvo, aguardar=None) -> str:
    """TRANSITIONAL — ponte entre o fluxo legado e a fronteira do captcha.

    Duas coisas moram aqui e não na integração, de propósito:

    1. A chave sai de `os.environ`. A fronteira a recebe por parâmetro e nunca
       lê o ambiente; é o adapter que sabe onde ela está hoje.
    2. `resolvedor_captcha.solve_hcaptcha` lê `os.environ["GEMINI_API_KEY"]` por
       conta própria — não há parâmetro. Enquanto o fork não mudar, a variável
       global CONTINUA sendo o único transporte do segredo até ele.
       Ver CAPTCHA_INTEGRATION_COUPLING e LEGACY_SECRET_LOADING.

    Condição de remoção: quando o fork aceitar a chave por parâmetro, esta função
    passa a construir `ConfigCaptcha` a partir do input da execução e some junto
    com o `os.environ` do passo 1.
    """
    config = captcha.ConfigCaptcha(api_key=os.environ.get("GEMINI_API_KEY", ""))
    return captcha.resolver(alvo, config, tentativas=2, aguardar=aguardar)


def _listar_certs_windows() -> list[dict]:
    """Adapter sobre a integração Windows: lista os certificados utilizáveis.

    A leitura em si vive em `automation/certificados_windows.py`. O que fica aqui
    é o que pertence ao adapter local — decidir que uma falha conhecida vira
    lista vazia, e o que aparece no console.
    """
    try:
        return certificados_windows.interpretar_saida(
            certificados_windows.executar_powershell(
                certificados_windows.COMANDO_POWERSHELL
            )
        )
    except certificados_windows.FalhaAoLerCertificados as erro:
        print(f"[cert] Falha ao ler o repositório de certificados: {erro}")
        return []


def carregar_certificados() -> dict[str, dict]:
    """Retorna {nome_normalizado: info_do_certificado} lendo do Windows.

    Substitui o antigo par C:\\Certificados + senhas.json: os certificados são os
    que o usuário já tem instalados na máquina, e a autenticação é feita pelo
    próprio Chrome via CAPI — sem arquivo .pfx e sem senha em lugar nenhum.

    A chave do dicionário é o nome normalizado (sem acento, minúsculo), gerada
    tanto a partir do FriendlyName quanto do CN, para que a planilha possa trazer
    qualquer um dos dois. Só entram certificados da ICP-Brasil.
    """
    certs = _listar_certs_windows()
    if not certs:
        return {}

    mapa, ignorados = certificados_windows.indexar(certs)
    if ignorados:
        print(f"[cert] {ignorados} certificado(s) de sistema ignorado(s) "
              f"(CN fora do padrão da ICP-Brasil).")
    return mapa


def _buscar_certificado(nome: str, certs: dict) -> str | None:
    """Adapter sobre a regra pura de `automation.domain`.

    Traduz o dicionário de certificados do Windows no mapa CHAVE -> IDENTIDADE que
    a regra consome, e é aqui — não no domínio — que as mensagens são impressas.
    """
    resultado = buscar_certificado(nome, certificados_windows.identidades(certs))

    if resultado.resolvida:
        print(f"    → Certificado '{nome}' resolvido para "
              f"'{resultado.chave}' ({resultado.criterio}).")
        return resultado.chave

    if resultado.ambigua:
        print(f"    [!] Nome de certificado ambíguo: '{nome}' ({resultado.criterio}) "
              f"corresponde a {len(resultado.ambiguidade)}: "
              f"{', '.join(resultado.ambiguidade)}.")
        print("         Escreva na planilha um nome que identifique só um deles.")
    return None


def atualizar_env_certificado(cert_subject_cn: str) -> None:
    """Grava CERT_SUBJECT_CN no .env e no ambiente do processo.

    O servicos_rf_login lê essa variável para montar a flag
    --auto-select-certificate-for-urls do Chrome. Substituiu CERT_PFX_PATH e
    CERT_PFX_PASSPHRASE: no modo Windows Store não existe arquivo nem senha.
    """
    env_path = LOGIN_ECAC_DIR / ".env"
    existentes: dict[str, str] = {}
    if env_path.exists():
        for linha in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.startswith("#"):
                chave, _, valor = linha.partition("=")
                existentes[chave.strip()] = valor.strip()
    if "GEMINI_API_KEY" not in existentes:
        existentes["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", _GEMINI_API_KEY_PADRAO)
    # Resíduo do modo antigo: se sobraram no .env, o login tentaria o .pfx
    existentes.pop("CERT_PFX_PATH", None)
    existentes.pop("CERT_PFX_PASSPHRASE", None)
    existentes["CERT_SUBJECT_CN"] = cert_subject_cn

    conteudo = "\n".join(f"{k}={v}" for k, v in existentes.items()) + "\n"
    env_path.write_text(conteudo, encoding="utf-8")
    os.environ["CERT_SUBJECT_CN"] = cert_subject_cn


# ── Planilha ──────────────────────────────────────────────────────────────────

def ler_e_ordenar(caminho: str) -> pd.DataFrame:
    """Lê a aba 'Empresas', remove duplicatas e ordena pela coluna C (certificado)."""
    df, removidas = planilha.ler_e_ordenar(caminho)
    if removidas:
        print(f"  ⚠ {removidas} linha(s) duplicada(s) removida(s) da planilha.")
    return df


# ── Sessão de planilha ────────────────────────────────────────────────────────
# O recurso e suas regras vivem em automation/planilha.py. O que fica aqui é o
# que pertence ao adapter local: quando gravar, quando fechar e o que imprimir.
#
# TRANSITIONAL: `_sessao_planilha` é o dicionário interno da sessão, exposto
# enquanto o legado o lê direto. Sai quando `main` falar só pelos métodos —
# provavelmente junto com a fatia de app/orquestração.
_SESSAO = planilha.SessaoPlanilha()
_sessao_planilha = _SESSAO.estado


def _wb_sessao(caminho_planilha: str):
    """Workbook da sessão, carregado do disco só na primeira chamada."""
    if _SESSAO.precisa_abrir(caminho_planilha):
        fechar_planilha()
        _SESSAO.abrir(caminho_planilha)
    return _SESSAO.wb


def salvar_planilha() -> bool:
    """Grava no disco se houver alteração pendente. Retorna True se gravou.

    Uma falha (arquivo aberto no Excel, por exemplo) mantém a sessão suja para
    que a próxima chamada tente de novo, em vez de descartar os dados.
    """
    if not _SESSAO.precisa_gravar():
        return False
    try:
        _SESSAO.gravar()
    except Exception as e:
        print(f"    [!] Falha ao salvar a planilha: {type(e).__name__}: {e}")
        # Sem o `: {e}`, ao contrário do print acima: a mensagem do openpyxl
        # carrega o caminho completo do arquivo, e isto aqui vai para um log em
        # disco. O tipo já é o que orienta a ação (PermissionError = feche o Excel).
        registrar_erro(f"Planilha: falha ao salvar. {type(e).__name__}")
        return False
    _SESSAO.marcar_gravado()
    return True


def fechar_planilha() -> None:
    """Salva o que estiver pendente e descarta o workbook da memória."""
    salvar_planilha()
    _SESSAO.descartar()


def mapa_status(caminho_planilha: str) -> dict[str, tuple[str, str]]:
    """Mapa {cnpj: (coluna_D, coluna_E)} da aba 'Empresas', montado uma só vez."""
    _wb_sessao(caminho_planilha)
    return _SESSAO.mapa_status(caminho_planilha)


def _escrever_status(caminho_planilha: str, cnpj: str, valor: str,
                     coluna: int, rotulo: str) -> None:
    """Escreve `valor` na coluna indicada da linha do CNPJ na aba 'Empresas'."""
    _wb_sessao(caminho_planilha)
    if _SESSAO.escrever_status(cnpj, valor, coluna):
        print(f"    [✓] Coluna {rotulo} → '{valor}'  (CNPJ {cnpj})")
        return

    print(f"    [!] CNPJ {cnpj} não encontrado na planilha para escrita em {rotulo}.")


def escrever_coluna_d(caminho_planilha: str, cnpj: str, valor: str) -> None:
    """Escreve o status do DCTFWeb na coluna D da aba 'Empresas'."""
    _escrever_status(caminho_planilha, cnpj, valor,
                     coluna=planilha.COL_STATUS_DCTFWEB, rotulo="D")


def escrever_coluna_e(caminho_planilha: str, cnpj: str, valor: str) -> None:
    """Escreve o status dos Processos Fiscais na coluna E da aba 'Empresas'."""
    _escrever_status(caminho_planilha, cnpj, valor,
                     coluna=planilha.COL_STATUS_PROCESSOS, rotulo="E")


def ler_status_cnpj(caminho_planilha: str, cnpj: str) -> tuple[str, str]:
    """Lê os valores das colunas D e E da aba 'Empresas' para o CNPJ dado."""
    return mapa_status(caminho_planilha).get(cnpj, ("", ""))


def filtrar_pendentes(df: pd.DataFrame, caminho_planilha: str) -> tuple[pd.DataFrame, int]:
    """Remove do DataFrame as linhas com as colunas D e E já preenchidas."""
    return planilha.linhas_pendentes(
        df, mapa_status(caminho_planilha), _status_encerra_linha
    )


def escrever_aba_debitos(caminho_planilha: str, dados: list[dict]) -> None:
    """Adiciona os dados extraídos da tabela DCTFWeb na aba 'Débitos' da planilha."""
    _wb_sessao(caminho_planilha)
    destinos = _SESSAO.anexar_debitos(dados)
    print(f"    [✓] Aba 'Débitos': {len(dados)} linha(s) gravada(s) em "
          f"{planilha.descrever_destinos(destinos)}.")


# ── DCTFWeb: extração da tabela ────────────────────────────────────────────────

def extrair_debitos_dctfweb(sessao, cnpj: str, caminho_planilha: str) -> None:
    """TRANSITIONAL_PERSISTENCE_ADAPTER — consulta pela fronteira, grava aqui.

    A navegação vive em automation/consulta_fiscal.py e devolve dados. A decisão
    de gravar continua neste adapter enquanto a orquestração não for migrada.

    Condição de remoção: quando o app receber `ExtracaoFiscal` e decidir a
    persistência, este adapter sai junto com `verificar_pendencias`.
    """
    print("    → Aguardando tabela DCTFWeb carregar...")
    extracao = consulta_fiscal.consultar_dctfweb(
        sessao, cnpj, aguardar_rede=_aguardar_networkidle
    )

    print(f"    [✓] Total: {len(extracao)} linha(s) de débito DCTFWeb "
          f"em {extracao.paginas} página(s).")
    escrever_aba_debitos(caminho_planilha, list(extracao.linhas))
    escrever_coluna_d(caminho_planilha, cnpj, "Concluído")


# ── Processo Fiscal: extração de cards ────────────────────────────────────────

def escrever_aba_processos_fiscais(caminho_planilha: str, dados: list[dict]) -> None:
    """Adiciona linhas na aba 'Processos Fiscais' (cria se não existir)."""
    _wb_sessao(caminho_planilha)
    destinos = _SESSAO.anexar_processos(dados)
    print(f"    [✓] Aba 'Processos Fiscais': {len(dados)} linha(s) gravada(s) em "
          f"{planilha.descrever_destinos(destinos)}.")


def extrair_processo_fiscal(sessao, cnpj: str, caminho_planilha: str) -> None:
    """TRANSITIONAL_PERSISTENCE_ADAPTER — mesma divisão do DCTFWeb."""
    print("    → Aguardando página de processos fiscais carregar...")
    extracao = consulta_fiscal.consultar_processos(
        sessao, cnpj, aguardar_rede=_aguardar_networkidle, navegar=_goto_seguro
    )

    print(f"    [✓] Total: {len(extracao)} linha(s) de processo fiscal "
          f"em {extracao.paginas} página(s).")
    escrever_aba_processos_fiscais(caminho_planilha, list(extracao.linhas))
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


def _recuperar_apos_recusa(page) -> bool:
    """Devolve o portal a um estado utilizável depois de uma recusa de representação.

    A recusa é do CNPJ, não da sessão: o certificado segue autenticado e o portal
    aberto. Fechar o navegador aqui obrigaria um login novo para o próximo CNPJ
    do mesmo certificado — justamente o custo que se quer evitar.

    Fecha o formulário de representação (que fica aberto exibindo o erro) e volta
    para o portal, confirmando que o avatar reaparece.

    Returns:
        True se a sessão continua utilizável para representar o próximo CNPJ.
    """
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass

    try:
        _goto_seguro(page, URL_SERVICOS_RF, label="pós-recusa", timeout=30_000)
        page.locator('xpath=//*[@id="avatar-dropdown-trigger"]').first.wait_for(
            state="visible", timeout=15_000
        )
        print("    [✓] Sessão mantida — seguindo para o próximo CNPJ deste certificado.")
        return True
    except Exception as e:
        print(f"    [!] Sessão não recuperada após a recusa "
              f"({type(e).__name__}: {e}). Fechando o navegador.")
        return False


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
                _classe = _classificar_mensagem(_err_msg)
                if _classe == _ANTIBOT:
                    print(f"    → [!] Erro anti-bot: '{_err_msg}'")
                    _erro_bloqueado = True
                    break
                if _classe == _RECUSA_DO_CNPJ:
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
            raise representacao.AntiBotEsgotado(
                f"Acesso bloqueado por anti-bot após {_MAX_TENTATIVAS_REPR} "
                "tentativa(s). Reprocessar manualmente mais tarde."
            )

        # ── Resolve captcha conforme tipo ─────────────────────────────────────
        _captcha_repr_ok = True   # False se 2 tentativas falharem → retry repr
        if captcha_tipo in ("popup", "inline"):
            _alvo = _popups[0] if captcha_tipo == "popup" else page
            if captcha_tipo == "popup":
                print("    → Popup de captcha detectada. Resolvendo...")

            _desfecho = _resolver_captcha(_alvo, aguardar=lambda: page.wait_for_timeout(2_000))
            if _desfecho == captcha.RESOLVIDO_OU_AUSENTE:
                print("    [✓] Captcha resolvido.")
            else:
                print(f"    → Captcha não resolvido em 2 tentativas ({_desfecho}).")
                _captcha_repr_ok = False

            if captcha_tipo == "popup":
                try:
                    _popups[0].wait_for_close(timeout=15_000)
                    print("    [✓] Popup do captcha fechada.")
                except PlaywrightError:
                    pass
        else:
            print("    → Nenhum captcha detectado. Aguardando confirmação...")

        # Captcha não resolvido em 2 tentativas → reinicia o fluxo de representação
        if not _captcha_repr_ok:
            print("    → Captcha não resolvido em 2 tentativas. Refazendo 'Representar'...")
            if _tentativa_repr < _MAX_TENTATIVAS_REPR:
                continue
            raise representacao.RepresentacaoNaoConfirmada(
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
            _classe_pos_captcha = _classificar_mensagem(_err_pos_captcha)
            if _classe_pos_captcha == _ANTIBOT:
                print(f"    → [!] Erro anti-bot após captcha: '{_err_pos_captcha}'")
                if _tentativa_repr < _MAX_TENTATIVAS_REPR:
                    continue
                raise representacao.AntiBotEsgotado(
                    f"Acesso bloqueado por anti-bot após captcha — "
                    f"{_MAX_TENTATIVAS_REPR} tentativa(s). Reprocessar manualmente."
                )
            if _classe_pos_captcha == _RECUSA_DO_CNPJ:
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

        raise representacao.RepresentacaoNaoConfirmada(
            f"Representação falhou para CNPJ {cnpj} após {_MAX_TENTATIVAS_REPR} "
            f"tentativa(s). Portal ainda exibe: '{_cnpj_pag_final}'."
        )

    print(f"    [✓] Perfil alterado para Procurador do CNPJ {cnpj}.")

    _goto_seguro(page, URL_PENDENCIAS, label="pendências")
    page.wait_for_timeout(500)


def verificar_pendencias(sessao, cnpj: str, caminho_planilha: str,
                          skip_dctfweb: bool = False,
                          skip_processo: bool = False) -> str:
    """TRANSITIONAL_PERSISTENCE_ADAPTER — decide o que gravar a partir do que a
    consulta leu.

    A leitura do portal vive em automation/consulta_fiscal.py. O que ficou aqui é
    o que ainda pertence à orquestração: quais colunas escrever, e quando.

    A ORDEM importa e está preservada: a coluna D é gravada assim que o DCTFWeb
    termina, ANTES de os Processos começarem. Se os Processos caem, o DCTFWeb não
    é refeito — é o RESUMABILITY_CONTRACT da fatia 4.

    Retorna:
        "sem_pendencia"   — sem débitos
        "nao_compensavel" — débitos não compensáveis
        "concluido"       — ao menos um fluxo foi processado nesta execução
        "desconhecido"    — status inesperado
    """
    print("    → Verificando status de pendências...")
    situacao = consulta_fiscal.ler_situacao(sessao)

    if not situacao.reconhecida:
        print("    [!] Status de pendências não reconhecido.")
        return "desconhecido"

    if not situacao.com_pendencia:
        if not skip_dctfweb:
            escrever_coluna_d(caminho_planilha, cnpj, "Sem débitos")
        if not skip_processo:
            escrever_coluna_e(caminho_planilha, cnpj, "Sem Processos")
        return "sem_pendencia"

    # Nenhum botão de ação encontrado
    if not situacao.tem_dctfweb and not situacao.tem_processo:
        if not skip_dctfweb:
            escrever_coluna_d(caminho_planilha, cnpj, "Débitos não compensáveis")
        if not skip_processo:
            escrever_coluna_e(caminho_planilha, cnpj, "Sem Processos")
        return "nao_compensavel"

    # ── Dívida DCTFWeb ────────────────────────────────────────────────────────
    if situacao.tem_dctfweb and not skip_dctfweb:
        print("    → Clicando em 'Dívida DCTFWeb'...")
        extrair_debitos_dctfweb(sessao, cnpj, caminho_planilha)
    elif skip_dctfweb:
        print("    → DCTFWeb já processado anteriormente. Pulando...")

    # ── Processo Fiscal ───────────────────────────────────────────────────────
    if situacao.tem_processo and not skip_processo:
        extrair_processo_fiscal(sessao, cnpj, caminho_planilha)
        if not situacao.tem_dctfweb and not skip_dctfweb:
            # Sem DCTFWeb e sem skip → marca D como concluído via Fiscal
            escrever_coluna_d(caminho_planilha, cnpj, "Concluído")
    elif situacao.tem_processo and skip_processo:
        print("    → Processos Fiscais já processados anteriormente. Pulando...")
    else:
        # Não existe botão de Processo Fiscal
        if not skip_processo:
            escrever_coluna_e(caminho_planilha, cnpj, "Sem Processos")

    return "concluido"


# ── Processamento por CNPJ ────────────────────────────────────────────────────

def processar_cnpj(sessao, cnpj: str, row: pd.Series,
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

    if _status_encerra_linha(val_d):
        print(f"    → Coluna D = '{val_d}'. Não há o que processar. Pulando.")
        return "ja_processado"

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
    # A sessão já está autenticada; ir direto para representação.
    try:
        resultado = representacao.representar(
            sessao, cnpj,
            executar=trocar_perfil_procurador,
            recusa_do_portal=FalhaPermanente,
        )

        if resultado.encerra_a_linha:
            # Registra o motivo na planilha antes de propagar, para que a recusa
            # fique visível em vez de a linha voltar em branco.
            if resultado.status_coluna_d:
                escrever_coluna_d(caminho_planilha, cnpj, resultado.status_coluna_d)
            # TRANSITIONAL — converte o resultado de volta na exception que o laço
            # legado espera. A mensagem é CONSTANTE: a antiga carregava o CNPJ e o
            # texto bruto do portal, e este ponto de `raise` é novo.
            # Condição de remoção: quando `processar` consumir o resultado.
            raise FalhaPermanente(
                "O portal recusou este CNPJ.",
                status_coluna_d=resultado.status_coluna_d,
            )

        if not resultado.representado:
            raise representacao.RepresentacaoNaoConfirmada(
                f"Representação não concluída ({resultado.situacao})."
            )

        status = verificar_pendencias(sessao, cnpj, caminho_planilha,
                                       skip_dctfweb=skip_dctfweb,
                                       skip_processo=skip_processo)
    finally:
        # Único toque no disco por CNPJ. No finally para que uma falha no meio
        # do fluxo não descarte o que já foi extraído.
        salvar_planilha()

    return status


# ── Processamento principal ───────────────────────────────────────────────────

def processar(df: pd.DataFrame, certs: dict[str, dict],
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
    sessao = None           # login.SessaoReceita | None
    policy_ok      = True   # definido de fato ao entrar no primeiro certificado
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
            if sessao:
                _fechar_navegador(sessao.playwright, sessao.contexto, sessao.pagina)
                sessao = None

            chave = _buscar_certificado(certificado, certs)
            if chave is None:
                print(f"  [!] Certificado '{certificado}' não está instalado nesta máquina.")
                print(f"       Instalados: {', '.join(sorted(certs.keys()))}")
                cert_atual = certificado
                i += 1
                continue

            cert_subject_cn = str(certs[chave].get("subject_cn") or "").strip()
            atualizar_env_certificado(cert_subject_cn)
            print(f"    [cert] {certs[chave].get('display', chave)}  (CN: {cert_subject_cn})")

            # Guardião elevado (1 UAC): escreve a policy de auto-seleção do Chrome
            # e a REMOVE quando esta automação terminar, por qualquer motivo. Sem
            # ela o Chrome abre a janela nativa de escolha de certificado — daí o
            # fallback por UI dentro do fazer_login.
            _policy = cert_windows.iniciar_guarda_detalhado(cert_subject_cn)
            policy_ok = _policy.confiavel
            if not policy_ok:
                print("    [!] Policy de auto-seleção não ficou ativa (UAC negado?). "
                      "A janela de certificado será resolvida por UI.")
            elif not _policy.sera_limpa:
                # POLICY_STALE_OWNERSHIP_GAP: a policy já estava escrita e nenhum
                # guardião foi lançado — ninguém desta execução vai removê-la.
                print("    [!] A policy do Chrome já existia e continuará na máquina "
                      "depois desta execução.")
            cert_atual = certificado

        chave = _buscar_certificado(cert_atual, certs)
        if chave is None:
            i += 1
            continue
        cert_subject_cn = str(certs[chave].get("subject_cn") or "").strip()
        cert_serial     = str(certs[chave].get("serial") or "").strip()

        print(f"\n  [{idx + 1}/{total}] CNPJ: {cnpj}")

        # ── Login no portal (apenas quando não há sessão aberta) ─────────────────
        if sessao is None:
            try:
                _resultado = _autenticar(cert_subject_cn, cert_serial, policy_ok)
                if not _resultado.autenticado:
                    raise Exception("login não autenticou.")
                sessao = _resultado.sessao
                page = sessao.pagina
                print("    [✓] Login no portal concluído.")
            except Exception as e:
                print(f"    [!] Erro ao fazer login ({type(e).__name__}: {e}). Pulando CNPJ {cnpj}.")
                i += 1
                continue

        else:
            page = sessao.pagina

        # ── Processa pendências deste CNPJ ────────────────────────────────────
        _avancar = True
        try:
            processar_cnpj(sessao, cnpj, row, caminho_planilha)

        except FalhaPermanente as e:
            # Procuração expirada/inválida ou CNPJ não autorizado — não retentar.
            # O portal recusou ESTE CNPJ, não a sessão: o certificado continua
            # autenticado. Mantém o navegador para o próximo CNPJ do mesmo grupo
            # e só fecha se a sessão não voltar a um estado utilizável.
            print(f"    [!] Falha permanente — CNPJ {cnpj} ignorado: {e}")
            if not _recuperar_apos_recusa(page):
                _fechar_navegador(sessao.playwright, sessao.contexto, sessao.pagina)
                sessao = None

        except Exception as e:
            _retentativas_cnpj[cnpj] = _retentativas_cnpj.get(cnpj, 0) + 1
            _n = _retentativas_cnpj[cnpj]
            print(
                f"    [!] Erro ao processar CNPJ {cnpj} "
                f"(tentativa {_n}/{_MAX_RETENT_CNPJ}): {e}"
            )
            _fechar_navegador(sessao.playwright, sessao.contexto, sessao.pagina)
            sessao = None
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
    if sessao:
        _fechar_navegador(sessao.playwright, sessao.contexto, sessao.pagina)


# ── Entrada ───────────────────────────────────────────────────────────────────

def _entrada_da_execucao(planilha: str) -> EntradaDebitosEmAberto:
    """TRANSITIONAL — ponte entre os entrypoints atuais e a fronteira tipada.

    Hoje `main()` DESCOBRE a planilha (argumento da CLI ou janela do Tkinter) e
    só depois pede à fronteira que a valide. O destino é o inverso: um runner
    constrói `EntradaDebitosEmAberto` e a entrega pronta.

    Condição de remoção: quando existir o runner/app, esta função e o argparse
    local saem juntos e `main()` passa a receber a entrada como parâmetro.
    Enquanto isso, ela é o único ponto do legado que conhece a fronteira.
    """
    return montar_entrada({"planilha": planilha})


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

    # A forma vem da fronteira; o recurso, da integração. Ambas antes de qualquer
    # navegador — descobrir que a planilha está aberta no Excel só na primeira
    # gravação custa um login e um CNPJ inteiros.
    try:
        entrada = _entrada_da_execucao(planilha)
        validar_recurso(entrada.planilha)
    except (EntradaInvalida, PlanilhaIndisponivel) as erro_de_entrada:
        print(f"  [!] {erro_de_entrada}")
        sys.exit(2)
    planilha = entrada.planilha

    print(f"\nPlanilha: {planilha}")

    # Carrega o .env local e resolve a GEMINI_API_KEY antes de qualquer chamada
    # ao captcha solver
    _env_path = LOGIN_ECAC_DIR / ".env"
    if _env_path.exists():
        load_dotenv(dotenv_path=_env_path, override=True)

    _chave, _origem = _resolver_gemini_key()
    os.environ["GEMINI_API_KEY"] = _chave
    if _chave:
        # Só a origem. O prefixo e o sufixo da chave saíam daqui para o console
        # e, com `--log`, para um arquivo — SENSITIVE_OUTPUT sem contrapartida.
        print(f"Chave Gemini:  configurada (origem: {_origem})")
    else:
        print("  [!] GEMINI_API_KEY não encontrada — o captcha não será resolvido.")

    # Passo 2: certificados instalados nesta máquina (Windows Certificate Store)
    print("\nLendo certificados instalados na máquina...")
    certs = carregar_certificados()
    if not certs:
        print("  [!] Nenhum certificado com chave privada, válido e não arquivado,")
        print("      foi encontrado em Cert:\\CurrentUser\\My.")
        print("      Instale o certificado no Windows antes de rodar a automação.")
        sys.exit(1)
    _instalados = sorted({c.get("display") or c.get("subject_cn", "?") for c in certs.values()})
    print(f"Certificados instalados: {len(_instalados)}")
    for _nome in _instalados:
        print(f"  • {_nome}")

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
    # Processo guardião: relançado ELEVADO por cert_windows.iniciar_guarda, escreve
    # a policy de auto-seleção do Chrome e a remove quando o PID da automação
    # morrer — por qualquer motivo. Precisa vir antes de main() porque no exe
    # congelado o guardião é o próprio executável, com estes argumentos.
    if len(sys.argv) >= 4 and sys.argv[1] == "--guard":
        try:
            cert_windows.guardiao(
                int(sys.argv[2]),
                base64.b64decode(sys.argv[3]).decode("utf-8"),
            )
        except Exception:
            cert_windows.limpar_autoselect()
        sys.exit(0)

    main()
