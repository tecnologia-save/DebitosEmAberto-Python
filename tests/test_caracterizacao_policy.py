"""Caracterizacao da policy de auto-selecao COMO ELA E HOJE.

Escrito ANTES de extrair. Nenhum teste escreve no registro real, pede UAC, lanca
processo elevado, altera a policy da maquina ou depende de Windows configurado.
As fronteiras substituidas sao `winreg`, `ShellExecuteExW` e a espera por PID.

Todos os CNs sao ficticios.
"""
import json

import pytest
from registro_falso import RegistroFalso

import cert_windows

CN_A = "ALFA FICTICIA LTDA:11111111000191"
CN_B = "BETA FICTICIA SA:22222222000172"
CAMINHO = cert_windows.REG_PATH


@pytest.fixture
def registro(monkeypatch):
    """Substitui o winreg inteiro, incluindo as constantes de colmeia."""
    falso = RegistroFalso()
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(
        cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM"))
    )
    return falso


def registro_com(monkeypatch, **kwargs):
    falso = RegistroFalso(**kwargs)
    monkeypatch.setattr(cert_windows, "winreg", falso)
    monkeypatch.setattr(cert_windows, "_COLMEIAS", (("HKCU", "HKCU"), ("HKLM", "HKLM")))
    return falso


# ── A · escrita da policy ─────────────────────────────────────────────────────

def test_a_escreve_nas_duas_colmeias(registro):
    cert_windows.definir_autoselect(CN_A)

    for colmeia in ("HKCU", "HKLM"):
        valores = registro.valores(colmeia, CAMINHO)
        assert len(valores) == len(cert_windows.CERT_URLS)
        assert set(valores) == {str(i) for i in range(1, len(cert_windows.CERT_URLS) + 1)}


def test_a_cada_valor_e_um_json_com_url_e_cn(registro):
    cert_windows.definir_autoselect(CN_A)

    primeiro = json.loads(registro.valores("HKCU", CAMINHO)["1"])
    assert primeiro["pattern"] == cert_windows.CERT_URLS[0]
    assert primeiro["filter"]["SUBJECT"]["CN"] == CN_A


def test_a_as_urls_cobrem_govbr_e_receita(registro):
    """A policy vale para os dominios onde o certificado e pedido."""
    assert any("acesso.gov.br" in u for u in cert_windows.CERT_URLS)
    assert any("receita.fazenda.gov.br" in u for u in cert_windows.CERT_URLS)


def test_a_reescrita_substitui_os_valores_contiguos(registro):
    """A limpeza apaga 1, 2, 3... ate o primeiro buraco — nao a chave inteira."""
    cert_windows.definir_autoselect(CN_A)

    cert_windows.definir_autoselect(CN_B)

    valores = registro.valores("HKCU", CAMINHO)
    assert len(valores) == len(cert_windows.CERT_URLS)
    assert json.loads(valores["1"])["filter"]["SUBJECT"]["CN"] == CN_B


def test_a_defeito_valor_fora_da_sequencia_sobrevive_a_reescrita(registro):
    """POLICY_POSSIBLE_DEFECT: a limpeza para no primeiro indice ausente, entao
    um valor com nome fora da sequencia — deixado por outra ferramenta ou por
    uma policy anterior mais longa — permanece apontando para o CN antigo."""
    cert_windows.definir_autoselect(CN_A)
    registro.dados["HKCU"][CAMINHO]["99"] = json.dumps(
        {"pattern": "https://[*.]gov.br", "filter": {"SUBJECT": {"CN": CN_A}}}
    )

    cert_windows.definir_autoselect(CN_B)

    assert "99" in registro.valores("HKCU", CAMINHO), "a sobra continua la"


def test_a_uma_colmeia_basta(monkeypatch, capsys):
    """HKLM sem elevacao e o caso comum, e nao interrompe."""
    falso = registro_com(monkeypatch, protegidas=("HKLM",))

    cert_windows.definir_autoselect(CN_A)

    assert falso.tem("HKCU", CAMINHO)
    assert not falso.tem("HKLM", CAMINHO)
    assert "HKLM indisponivel" in capsys.readouterr().out


def test_a_nenhuma_colmeia_disponivel_apenas_avisa(monkeypatch, capsys):
    """Nao levanta: o chamador descobre pela leitura, nao por exception."""
    registro_com(monkeypatch, protegidas=("HKCU", "HKLM"))

    assert cert_windows.definir_autoselect(CN_A) is None
    assert "FALHA" in capsys.readouterr().out


# ── B · leitura ───────────────────────────────────────────────────────────────

def test_b_le_o_cn_da_policy(registro):
    cert_windows.definir_autoselect(CN_A)

    assert cert_windows.policy_cn() == CN_A
    assert cert_windows.policy_existe() is True


def test_b_sem_policy(registro):
    assert cert_windows.policy_cn() == ""
    assert cert_windows.policy_existe() is False


def test_b_valor_corrompido_e_tratado_como_ausente(registro):
    cert_windows.definir_autoselect(CN_A)
    registro.dados["HKCU"][CAMINHO]["1"] = "isto nao e json"
    registro.dados["HKLM"][CAMINHO]["1"] = "isto tambem nao"

    assert cert_windows.policy_cn() == ""


def test_b_diagnostico_mostra_as_duas_colmeias(monkeypatch):
    falso = registro_com(monkeypatch, protegidas=("HKLM",))
    cert_windows.definir_autoselect(CN_A)

    texto = cert_windows.diagnostico()

    assert "HKCU=" in texto and "HKLM=" in texto
    assert "(vazio)" in texto
    assert falso.tem("HKCU", CAMINHO)


# ── P · PARTIAL_POLICY_STATE ──────────────────────────────────────────────────

def test_p_as_colmeias_podem_discordar(registro):
    """PARTIAL_POLICY_STATE: nada garante que HKCU e HKLM concordem.

    Basta a limpeza falhar numa delas e a escrita seguinte falhar na outra.
    """
    cert_windows.definir_autoselect(CN_A)
    registro.protegidas = {"HKLM"}          # HKLM congela em CN_A
    cert_windows.definir_autoselect(CN_B)   # HKCU passa a CN_B

    assert json.loads(registro.valores("HKCU", CAMINHO)["1"])["filter"]["SUBJECT"]["CN"] == CN_B
    assert json.loads(registro.valores("HKLM", CAMINHO)["1"])["filter"]["SUBJECT"]["CN"] == CN_A


def test_p_policy_cn_reporta_apenas_a_primeira_colmeia_nao_vazia(registro):
    """E a consequencia perigosa do teste acima: com as colmeias discordando,
    `policy_cn()` responde HKCU e nao diz que HKLM tem OUTRO CN."""
    cert_windows.definir_autoselect(CN_A)
    registro.protegidas = {"HKLM"}
    cert_windows.definir_autoselect(CN_B)

    assert cert_windows.policy_cn() == CN_B, "so HKCU"
    assert "HKLM=" + CN_A in cert_windows.diagnostico(), "o diagnostico ve, o policy_cn nao"


def test_p_limpeza_parcial_deixa_a_outra_colmeia(monkeypatch):
    falso = registro_com(monkeypatch)
    cert_windows.definir_autoselect(CN_A)
    falso.protegidas = {"HKLM"}

    cert_windows.limpar_autoselect()

    assert not falso.tem("HKCU", CAMINHO)
    assert falso.tem("HKLM", CAMINHO), "sobra machine-wide"
    assert cert_windows.policy_existe() is True


# ── J · limpeza ───────────────────────────────────────────────────────────────

def test_j_limpeza_remove_das_duas(registro, capsys):
    cert_windows.definir_autoselect(CN_A)

    cert_windows.limpar_autoselect()

    assert cert_windows.policy_existe() is False
    assert "removida de HKCU+HKLM" in capsys.readouterr().out


def test_j_limpeza_sem_policy_e_silenciosa(registro, capsys):
    cert_windows.limpar_autoselect()

    assert capsys.readouterr().out == ""


def test_o_limpeza_nao_verifica_de_quem_e_a_policy(registro):
    """A pergunta O da entrega: o cleanup remove policy de outro dono?

    SIM. `limpar_autoselect` faz DeleteKey incondicional — nao le o CN, nao
    compara com o que escreveu, nao tem marcador de dono.
    """
    cert_windows.definir_autoselect(CN_B)   # outra execucao escreveu CN_B

    cert_windows.limpar_autoselect()        # nossa limpeza, sem olhar o CN

    assert cert_windows.policy_existe() is False


# ── N · concorrencia: nao ha mecanismo nenhum ─────────────────────────────────

def test_n_a_policy_nao_carrega_dono(registro):
    """GLOBAL_CERT_POLICY_CONCURRENCY_RISK: nada no payload identifica quem
    escreveu — sem PID, sem sessao, sem marcador."""
    cert_windows.definir_autoselect(CN_A)

    payload = registro.valores("HKCU", CAMINHO)["1"]

    assert "pid" not in payload.lower()
    assert set(json.loads(payload)) == {"pattern", "filter"}


def test_n_nao_ha_lock_nem_compare_and_swap():
    fonte = (cert_windows.Path(cert_windows.__file__)).read_text(encoding="utf-8")

    for mecanismo in ("Mutex", "mutex", "lock", "LockFile", "flock", "compare"):
        assert mecanismo not in fonte, f"nao ha {mecanismo}"


def test_n_a_chave_e_a_mesma_para_qualquer_execucao():
    """Namespace unico: duas automacoes na mesma maquina disputam a MESMA chave."""
    assert cert_windows.REG_PATH == (
        r"Software\Policies\Google\Chrome\AutoSelectCertificateForUrls"
    )


# ── D · E · elevacao (UAC) ────────────────────────────────────────────────────

class Shell32Falso:
    """ShellExecuteExW de mentira: registra a chamada e devolve o que mandarem."""

    def __init__(self, sucesso=True):
        self.sucesso = sucesso
        self.chamadas = []

    def ShellExecuteExW(self, ponteiro):
        sei = ponteiro._obj
        self.chamadas.append(
            {"verb": sei.lpVerb, "file": sei.lpFile, "params": sei.lpParameters,
             "show": sei.nShow}
        )
        return 1 if self.sucesso else 0


@pytest.fixture
def uac(monkeypatch):
    falso = Shell32Falso()
    monkeypatch.setattr(cert_windows, "_shell32", falso)
    return falso


def test_d_o_verbo_e_runas_e_a_janela_e_escondida(uac):
    assert cert_windows._runas(["--guard", "123", "Q049QQ=="]) == 0

    chamada = uac.chamadas[0]
    assert chamada["verb"] == "runas", "é isto que dispara o UAC"
    assert chamada["show"] == 0, "SW_HIDE"
    assert chamada["file"] == cert_windows.sys.executable


def test_d_uac_negado_devolve_menos_um(monkeypatch):
    monkeypatch.setattr(cert_windows, "_shell32", Shell32Falso(sucesso=False))

    assert cert_windows._runas(["--guard", "1", "x"]) == -1


def test_d_sem_espera_o_retorno_e_zero_mesmo_sem_saber_o_que_aconteceu(uac):
    """POLICY_POSSIBLE_DEFECT: com wait_ms=None o retorno 0 significa apenas
    "o Windows aceitou lançar" — não que o guardião fez algo. Quem confirma é o
    polling seguinte, não este código."""
    assert cert_windows._runas(["--guard", "1", "x"], wait_ms=None) == 0


def test_d_o_cn_vai_em_base64_na_linha_de_comando(uac):
    """Base64 aqui é QUOTING, não proteção: elimina espaços e acentos do argv.
    O CN continua legível para qualquer um que veja a linha de comando."""
    import base64

    cn_b64 = base64.b64encode(CN_A.encode("utf-8")).decode("ascii")
    cert_windows._runas(["--guard", "999", cn_b64])

    params = uac.chamadas[0]["params"]
    assert cn_b64 in params
    assert " " not in cn_b64
    assert base64.b64decode(cn_b64).decode("utf-8") == CN_A, "reversível, não secreto"


def test_e_guard_args_em_script_e_em_exe_congelado(monkeypatch):
    monkeypatch.setattr(cert_windows.sys, "frozen", False, raising=False)
    args = cert_windows._guard_args(["--guard", "1", "x"])
    assert args[0].startswith('"') and args[0].endswith('"'), "path entre aspas"
    assert args[1:] == ["--guard", "1", "x"]

    monkeypatch.setattr(cert_windows.sys, "frozen", True, raising=False)
    assert cert_windows._guard_args(["--guard", "1", "x"]) == ["--guard", "1", "x"]


# ── G · K · L · iniciar_guarda ────────────────────────────────────────────────

@pytest.fixture
def sem_dormir(monkeypatch):
    dormidas = []
    monkeypatch.setattr(cert_windows.time, "sleep", lambda s: dormidas.append(s))
    return dormidas


def test_g_lanca_o_guardiao_e_espera_a_policy_do_cn_pedido(registro, sem_dormir, monkeypatch):
    chamadas = []

    def lancar(args, wait_ms=None):
        chamadas.append(args)
        cert_windows.definir_autoselect(CN_A)   # o guardião escreve
        return 0

    monkeypatch.setattr(cert_windows, "_runas", lancar)

    assert cert_windows.iniciar_guarda(CN_A) is True
    assert len(chamadas) == 1
    assert "--guard" in chamadas[0]


def test_g_uac_negado_nao_e_fatal_apenas_devolve_false(registro, monkeypatch, capsys):
    """H da entrega: UAC negado NÃO levanta. O chamador segue com policy_ok=False
    e o login usa o fallback de UI."""
    monkeypatch.setattr(cert_windows, "_runas", lambda *a, **k: -1)

    assert cert_windows.iniciar_guarda(CN_A) is False
    assert "UAC negado" in capsys.readouterr().out


def test_i_o_polling_desiste_depois_de_60_tentativas(registro, sem_dormir, monkeypatch):
    """POLICY_POSSIBLE_DEFECT: 60 vezes 0,5 s = 30 s mágicos, sem condição associada
    além de "a policy ainda não apareceu"."""
    monkeypatch.setattr(cert_windows, "_runas", lambda *a, **k: 0)   # não escreve nada

    assert cert_windows.iniciar_guarda(CN_A) is False
    assert len(sem_dormir) == 60
    assert set(sem_dormir) == {0.5}


def test_l_policy_ja_correta_devolve_true_sem_lancar_guardiao(registro, monkeypatch, capsys):
    """POLICY_STALE_OWNERSHIP_GAP — o achado central da fatia.

    Se a policy já está com o CN pedido, `iniciar_guarda` devolve True na hora e
    NÃO lança guardião. Esta execução passa a depender de uma policy que ela não
    escreveu e que ninguém associou a ela: se quem a escreveu morreu sem limpar,
    ela fica na máquina indefinidamente, apontando para o certificado de um
    cliente, e nenhum processo desta execução vai removê-la.
    """
    cert_windows.definir_autoselect(CN_A)       # sobra de outra execução
    lancamentos = []
    monkeypatch.setattr(cert_windows, "_runas", lambda *a, **k: lancamentos.append(a) or 0)

    assert cert_windows.iniciar_guarda(CN_A) is True
    assert lancamentos == [], "nenhum guardião associado a esta execução"
    assert "Policy ja ativa" in capsys.readouterr().out


def test_m_policy_de_outro_cn_lanca_guardiao_novo(registro, sem_dormir, monkeypatch):
    """M da entrega: a policy de CN_B não impede o guardião de CN_A. Ele sobe,
    sobrescreve, e agora há DOIS donos potenciais para a mesma chave."""
    cert_windows.definir_autoselect(CN_B)
    lancamentos = []

    def lancar(args, wait_ms=None):
        lancamentos.append(args)
        cert_windows.definir_autoselect(CN_A)
        return 0

    monkeypatch.setattr(cert_windows, "_runas", lancar)

    assert cert_windows.iniciar_guarda(CN_A) is True
    assert len(lancamentos) == 1
    assert cert_windows.policy_cn() == CN_A, "sobrescreveu"


def test_m_esperar_pelo_cn_e_nao_pela_existencia_torna_a_troca_segura(
    registro, sem_dormir, monkeypatch
):
    """Se o guardião subir mas escrever outro CN, o polling estoura em vez de
    devolver True — senão o Chrome abriria com o certificado errado."""
    cert_windows.definir_autoselect(CN_B)
    monkeypatch.setattr(cert_windows, "_runas", lambda *a, **k: 0)

    assert cert_windows.iniciar_guarda(CN_A) is False


# ── J · O · o guardião e a limpeza cega ───────────────────────────────────────

class Kernel32Falso:
    def __init__(self, handle=1):
        self.handle = handle
        self.esperas = []
        self.fechados = []

    def OpenProcess(self, acesso, herdar, pid):
        return self.handle

    def OpenEventW(self, acesso, herdar, nome):
        return 0        # sem canal de limpeza nestes testes

    def WaitForSingleObject(self, h, ms):
        self.esperas.append(ms)
        return 0

    def CloseHandle(self, h):
        self.fechados.append(h)
        return True


@pytest.fixture
def guardiao_isolado(monkeypatch, tmp_path, sem_dormir):
    """O guardião roda elevado e grava log ao lado do módulo — nos testes, em tmp.

    Desde a fatia 12C ele também ANEXA ao lease do host antes de escrever a
    policy. Aqui o lease é um handle fictício: o que se caracteriza é a policy,
    e o lease tem os seus próprios testes.
    """
    monkeypatch.setattr(cert_windows, "_k32", Kernel32Falso())
    monkeypatch.setattr(cert_windows, "Path", lambda *a: tmp_path / "cert_windows.py")
    monkeypatch.setattr(cert_windows.exclusividade_host, "anexar", lambda: 99)
    return tmp_path


def test_j_o_guardiao_escreve_espera_o_pid_e_limpa(registro, guardiao_isolado, monkeypatch):
    k32 = Kernel32Falso()
    monkeypatch.setattr(cert_windows, "_k32", k32)

    cert_windows.guardiao(4242, CN_A)

    assert k32.esperas == [0xFFFFFFFF], "espera indefinidamente pelo PID"
    assert cert_windows.policy_existe() is False, "limpou ao fim"


def test_o_o_guardiao_limpa_sem_olhar_de_quem_e_a_policy(
    registro, guardiao_isolado, monkeypatch
):
    """O da entrega: SIM, o cleanup remove policy de outro dono.

    O guardião de CN_A escreve, espera o PID, e no `finally` chama
    `limpar_autoselect()` — que apaga a chave inteira. Se outra execução tiver
    sobrescrito com CN_B nesse meio-tempo, é CN_B que desaparece.
    """
    def esperar_e_deixar_outro_escrever(h, ms):
        cert_windows.definir_autoselect(CN_B)   # execução B toma a chave
        return 0

    k32 = Kernel32Falso()
    k32.WaitForSingleObject = esperar_e_deixar_outro_escrever
    monkeypatch.setattr(cert_windows, "_k32", k32)

    cert_windows.guardiao(4242, CN_A)

    assert cert_windows.policy_cn() == "", "a policy de B foi removida pelo guardião de A"


def test_j_a_limpeza_insiste_ate_dez_vezes(registro, guardiao_isolado, monkeypatch):
    """E insistir é o que transforma uma corrida em disputa: enquanto a chave
    reaparecer, o guardião continua apagando — por até ~5 s.

    Desde a fatia 12C, se as dez não bastarem ele NÃO desiste e vai embora: são
    mais 20 rodadas espaçadas, e depois disso o processo PARA — bloqueado no
    próprio lease do host, que nunca é sinalizado. Sem CPU, sem laço, e o host
    continua ocupado. HOST_EXCLUSIVITY_FAIL_CLOSED.
    """
    monkeypatch.setattr(cert_windows, "limpar_autoselect", lambda: None)
    monkeypatch.setattr(cert_windows, "policy_existe", lambda: True)
    dormidas = []
    monkeypatch.setattr(cert_windows.time, "sleep", lambda s: dormidas.append(s))

    cert_windows.guardiao(4242, CN_A)

    curtas = [d for d in dormidas if d == 0.5]
    longas = [d for d in dormidas if d == cert_windows.INTERVALO_REPETICAO_S]
    assert len(curtas) == 10 * (1 + cert_windows.REPETICOES_APOS_A_MORTE)
    assert len(longas) == cert_windows.REPETICOES_APOS_A_MORTE, "espaçadas, não em laço"


def test_v_o_log_do_guardiao_grava_o_cn_em_disco(registro, guardiao_isolado, monkeypatch):
    """SENSITIVE_OUTPUT: `_guard_log.txt` fica ao lado do módulo, contém o CN —
    ou seja, o nome do cliente — e nunca é removido."""
    monkeypatch.setattr(cert_windows, "_k32", Kernel32Falso())

    cert_windows.guardiao(4242, CN_A)

    log = guardiao_isolado / "_guard_log.txt"
    assert log.exists()
    assert CN_A in log.read_text(encoding="utf-8")
