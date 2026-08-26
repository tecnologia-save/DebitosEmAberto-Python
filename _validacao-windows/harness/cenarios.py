"""Os cenarios da fatia 12E, cada um chamando as primitivas reais do produto.

Regra do §1: nada de implementacao paralela. Onde o harness "faz" alguma coisa,
ele chama `exclusividade_host`, `cert_windows`, `policy_certificado` ou
`maquina` — os mesmos modulos que a automacao usa. O que e proprio do harness e
so a montagem do cenario: subir um segundo processo, matar um processo, semear
um valor no registro.

Regra do §37: nenhum cenario altera o produto para passar. Se um invariante
falhar, ele e registrado e a rodada para.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import winreg
from pathlib import Path

from comum import (
    CN_A,
    CN_B,
    FAIL_SAFE,
    FAIL_UNSAFE,
    NOT_TESTED,
    PASS,
    Caderno,
    Evidencia,
)

PRODUTO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PRODUTO))

import cert_windows  # noqa: E402
from automation import exclusividade_host, maquina, policy_certificado  # noqa: E402

ESTE = Path(__file__).resolve().parent / "executar.py"

# Codigos de saida dos papeis filhos. Numeros, e nao texto: classificar por
# mensagem e proibido desde a fatia 6A.
SAIDA_ADQUIRIU = 0
SAIDA_RECUSADO = 3
SAIDA_FALHA_AO_VERIFICAR = 4


def _filho(papel: str, **extra) -> subprocess.Popen:
    """Sobe outra instancia deste harness num papel especifico."""
    argumentos = [sys.executable, str(ESTE), "--papel", papel]
    for chave, valor in extra.items():
        argumentos += [f"--{chave}", str(valor)]
    return subprocess.Popen(  # noqa: S603
        argumentos, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def _rodar(papel: str, timeout: int = 60, **extra) -> tuple[int, str]:
    processo = _filho(papel, **extra)
    saida, erro = processo.communicate(timeout=timeout)
    return processo.returncode, (saida or "") + (erro or "")


# ── Grupo 1 · lease de host (§5, §6, §30) ─────────────────────────────────────

def grupo_lease(caderno: Caderno, seco: bool) -> None:
    if seco:
        for item, cenario in (
            ("C", "criacao do Global named Event"),
            ("D", "segunda instancia recusada"),
            ("D2", "segunda instancia nao prolonga o lease"),
            ("AF30", "segunda instancia nao chega a abrir Chrome"),
        ):
            caderno.anotar(Evidencia(item=item, cenario=cenario, secao="§5 §6 §30",
                                     observado="execucao seca — nada foi tocado"))
        return

    # C — criacao
    controle = None
    try:
        controle = exclusividade_host.adquirir()
        classe, observado = PASS, "handle obtido; objeto criado no namespace Global"
    except exclusividade_host.FalhaAoVerificarExclusividade as erro:
        classe, observado = FAIL_SAFE, f"recusou criar: {type(erro).__name__}"
    except exclusividade_host.ExecucaoJaAtivaNoHost:
        classe, observado = FAIL_SAFE, "ja existia — havia outra execucao no host"
    caderno.anotar(Evidencia(
        item="C", secao="§5",
        cenario="processo comum cria Global\\DebitosEmAberto-host-v1",
        precondicao="host sem execucao ativa; processo NAO elevado",
        acao="exclusividade_host.adquirir()",
        esperado="cria o objeto e devolve controle",
        observado=observado, classificacao=classe,
        finding="HOST_LEASE_WINDOWS_SECURITY_VALIDATION_REQUIRED",
    ))
    if controle is None:
        return

    try:
        # D — segunda instancia
        codigo, texto = _rodar("segunda-instancia")
        classe = PASS if codigo == SAIDA_RECUSADO else FAIL_UNSAFE
        caderno.anotar(Evidencia(
            item="D", secao="§6",
            cenario="segundo processo real tenta adquirir com o lease tomado",
            precondicao="processo A segura o lease",
            acao="segunda instancia chama adquirir()",
            esperado=f"ExecucaoJaAtivaNoHost; saida {SAIDA_RECUSADO}",
            observado=f"saida {codigo}; {texto.strip()[:200]}",
            classificacao=classe,
            finding="SINGLE_HOST_CONCURRENCY_CONTRACT",
        ))

        # §30 — e sem tocar em nada global
        marcas = _marcas_do_filho(texto)
        tocou = marcas.get("tocou_registro") or marcas.get("abriu_chrome")
        caderno.anotar(Evidencia(
            item="AF30", secao="§30",
            cenario="a recusa acontece ANTES de qualquer efeito global",
            precondicao="processo A segura o lease",
            acao="segunda instancia reporta o que chegou a fazer",
            esperado="registro intocado, Chrome nao iniciado, CERT_SUBJECT_CN nao alterado",
            observado=json.dumps(marcas, ensure_ascii=False),
            classificacao=FAIL_UNSAFE if tocou else PASS,
            finding="SINGLE_HOST_CONCURRENCY_CONTRACT",
        ))
    finally:
        exclusividade_host.liberar(controle)

    # D2 — depois da liberacao, uma terceira entra
    codigo, texto = _rodar("segunda-instancia")
    caderno.anotar(Evidencia(
        item="D2", secao="§5 §6",
        cenario="apos A liberar, C adquire — o handle recusado nao prolongou o lease",
        precondicao="A liberou; nenhum guardiao vivo",
        acao="terceira instancia chama adquirir()",
        esperado=f"adquire; saida {SAIDA_ADQUIRIU}",
        observado=f"saida {codigo}; {texto.strip()[:200]}",
        classificacao=PASS if codigo == SAIDA_ADQUIRIU else FAIL_SAFE,
        finding="HOST_LOCK_RELEASE_BOUNDARY_UNDEFINED",
    ))


def _marcas_do_filho(texto: str) -> dict:
    for linha in texto.splitlines():
        if linha.startswith("MARCAS="):
            try:
                return json.loads(linha[len("MARCAS="):])
            except ValueError:
                return {"erro": "marcas ilegiveis"}
    return {"erro": "o filho nao reportou marcas"}


# ── Grupo 2 · policy real e guardiao elevado (§9 §11 §12 §13 §14) ─────────────

def grupo_policy(caderno: Caderno, seco: bool) -> None:
    itens = (
        ("G", "guardiao elevado anexa ao lease antes de escrever", "§9 §11"),
        ("I", "escrita e remocao reais em HKCU", "§12"),
        ("J", "escrita e remocao reais em HKLM", "§12"),
        ("K", "limpeza normal confirmada com guardiao elevado", "§13"),
        ("L", "processo comum le HKLM escrito pelo elevado", "§14"),
    )
    if seco:
        for item, cenario, secao in itens:
            caderno.anotar(Evidencia(item=item, cenario=cenario, secao=secao,
                                     observado="execucao seca — nada foi tocado"))
        return

    antes = cert_windows.inventario_da_policy()
    if any(c.existe for c in antes):
        for item, cenario, secao in itens:
            caderno.anotar(Evidencia(
                item=item, cenario=cenario, secao=secao,
                esperado="host sem policy antes de comecar",
                observado="ABORTADO: ja havia policy no host; sanear a VM antes",
                classificacao=NOT_TESTED,
            ))
        return

    controle_host = exclusividade_host.adquirir()
    try:
        resultado = maquina.garantir_policy_do_windows(CN_A)
        colmeias = {c.rotulo: c for c in cert_windows.inventario_da_policy()}

        caderno.anotar(Evidencia(
            item="G", secao="§9 §11",
            cenario="guardiao elevado sobe, anexa ao lease e so entao escreve",
            precondicao="parent NAO elevado com o lease; UAC aceito pelo operador",
            acao="maquina.garantir_policy_do_windows(CN ficticio)",
            esperado="ATIVADA com guardiao desta execucao",
            observado=f"situacao={resultado.situacao!r} guardiao={resultado.tem_guardiao}",
            classificacao=PASS if resultado.tem_guardiao else FAIL_SAFE,
            finding="HOST_LEASE_WINDOWS_SECURITY_VALIDATION_REQUIRED",
        ))

        for item, rotulo in (("I", "HKCU"), ("J", "HKLM")):
            colmeia = colmeias.get(rotulo)
            escreveu = bool(colmeia and colmeia.existe and colmeia.regras)
            caderno.anotar(Evidencia(
                item=item, secao="§12",
                cenario=f"escrita real observavel em {rotulo}",
                precondicao="guardiao elevado acabou de escrever",
                acao="cert_windows.inventario_da_policy()",
                esperado=f"{rotulo} com as regras da policy",
                observado=(
                    f"existe={colmeia.existe} legivel={colmeia.legivel} "
                    f"regras={len(colmeia.regras)}" if colmeia else "colmeia ausente"
                ),
                classificacao=PASS if escreveu else FAIL_SAFE,
                finding="NORMAL_POLICY_CLEANUP_PRIVILEGE_GAP",
            ))

        # L — a pergunta que decide o UNREADABLE_HIVE_BLOCKS_HOST_RELEASE
        hklm = colmeias.get("HKLM")
        legivel = bool(hklm and hklm.legivel)
        caderno.anotar(Evidencia(
            item="L", secao="§14",
            cenario="processo comum consegue inventariar HKLM escrito pelo elevado",
            precondicao="HKLM escrito por processo elevado; leitor NAO elevado",
            acao="cert_windows.inventario_da_policy() no processo comum",
            esperado="legivel=True — senao a limpeza nunca confirma",
            observado=f"legivel={hklm.legivel if hklm else 'n/d'}",
            classificacao=PASS if legivel else FAIL_SAFE,
            finding="UNREADABLE_HIVE_BLOCKS_HOST_RELEASE",
        ))

        # K — limpeza normal, pedida pelo processo comum ao guardiao elevado
        confirmou = maquina.liberar_policy_do_windows(resultado.controle)
        depois = cert_windows.inventario_da_policy()
        sobrou = [c.rotulo for c in depois if c.existe or not c.legivel]
        caderno.anotar(Evidencia(
            item="K", secao="§13",
            cenario="parent vivo pede limpeza; guardiao elevado remove as duas colmeias",
            precondicao="policy escrita em HKCU e HKLM pelo guardiao elevado",
            acao="maquina.liberar_policy_do_windows(controle)",
            esperado="confirmado=True; nenhuma colmeia com estado",
            observado=f"confirmado={confirmou} sobrou={sobrou or 'nada'}",
            classificacao=PASS if (confirmou and not sobrou) else FAIL_SAFE,
            finding="NORMAL_POLICY_CLEANUP_PRIVILEGE_GAP",
        ))
    finally:
        exclusividade_host.liberar(controle_host)


# ── Grupo 3 · matriz de estado residual no registro real (§16) ────────────────

_RESIDUOS = (
    ("N-A", "somente o valor '2'", {"2": json.dumps(
        {"pattern": "https://[*.]gov.br", "filter": {"SUBJECT": {"CN": CN_A}}})}),
    ("N-B", "payload malformado no '1'", {"1": "{isto nao fecha"}),
    ("N-C", "valor de forma desconhecida", {"1": json.dumps({"regra": "outra"})}),
    ("N-D", "nome de valor inesperado", {"RegraDaEmpresa": "qualquer coisa"}),
)


def grupo_residuo(caderno: Caderno, seco: bool) -> None:
    if seco:
        for item, descricao, _ in (*_RESIDUOS, ("N-E", "as duas ausentes", {})):
            caderno.anotar(Evidencia(item=item, cenario=descricao, secao="§16",
                                     observado="execucao seca — nada foi tocado"))
        return

    for item, descricao, valores in _RESIDUOS:
        _semear_hkcu(valores)
        visto = cert_windows.policy_existe()
        _apagar_hkcu()
        caderno.anotar(Evidencia(
            item=item, secao="§16",
            cenario=f"policy_existe com residuo real: {descricao}",
            precondicao="HKCU com estado que nao produz CN interpretavel",
            acao="cert_windows.policy_existe()",
            esperado="True — estado ilegivel ainda e estado",
            observado=f"policy_existe()={visto}",
            classificacao=PASS if visto else FAIL_UNSAFE,
            finding="CLEANUP_CONFIRMATION_FALSE_NEGATIVE",
        ))

    _apagar_hkcu()
    visto = cert_windows.policy_existe()
    caderno.anotar(Evidencia(
        item="N-E", secao="§16",
        cenario="policy_existe com as duas colmeias realmente ausentes",
        precondicao="nenhuma chave de policy no host",
        acao="cert_windows.policy_existe()",
        esperado="False",
        observado=f"policy_existe()={visto}",
        classificacao=PASS if not visto else FAIL_SAFE,
        finding="CLEANUP_CONFIRMATION_FALSE_NEGATIVE",
    ))


def _semear_hkcu(valores: dict) -> None:
    """Monta o cenario no registro real. Setup, e nao algoritmo do produto."""
    chave = winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, cert_windows.REG_PATH, 0, winreg.KEY_ALL_ACCESS
    )
    try:
        for nome, valor in valores.items():
            winreg.SetValueEx(chave, nome, 0, winreg.REG_SZ, valor)
    finally:
        winreg.CloseKey(chave)


def _apagar_hkcu() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, cert_windows.REG_PATH)
    except OSError:
        pass


# ── Grupo 4 · decisao de startup sobre o registro real (§22 a §26) ────────────

def grupo_startup(caderno: Caderno, seco: bool) -> None:
    casos = (
        ("T", "§22", "host vazio", {}, policy_certificado.CRIAR, ""),
        ("U", "§23", "policy exatamente compativel com o CN pedido",
         _policy_completa(CN_A), policy_certificado.EMPRESTAR, ""),
        ("V", "§24", "policy preexistente de OUTRO certificado",
         _policy_completa(CN_B), policy_certificado.RECUSAR,
         policy_certificado.OUTRO_CERTIFICADO),
        ("W-mal", "§25", "payload malformado", {"1": "{nao fecha"},
         policy_certificado.RECUSAR, policy_certificado.CONTEUDO_NAO_RECONHECIDO),
        ("W-extra", "§25", "regra a mais alem das nossas",
         {**_policy_completa(CN_A), "99": "qualquer coisa"},
         policy_certificado.RECUSAR, policy_certificado.REGRAS_ADICIONAIS),
    )
    if seco:
        for item, secao, descricao, _, esperado, _motivo in casos:
            caderno.anotar(Evidencia(item=item, cenario=descricao, secao=secao,
                                     esperado=esperado,
                                     observado="execucao seca — nada foi tocado"))
        return

    for item, secao, descricao, valores, esperado, motivo in casos:
        _apagar_hkcu()
        if valores:
            _semear_hkcu(valores)
        antes = _valores_hkcu()

        decisao = policy_certificado.avaliar_estado_inicial(
            cert_windows.inventario_da_policy(), CN_A, tuple(cert_windows.CERT_URLS)
        )
        depois = _valores_hkcu()
        _apagar_hkcu()

        certo = decisao.decisao == esperado and (not motivo or decisao.motivo == motivo)
        intacto = antes == depois
        caderno.anotar(Evidencia(
            item=item, secao=secao,
            cenario=f"decisao de startup sobre registro real: {descricao}",
            precondicao=f"HKCU com {len(valores)} valor(es) ficticio(s)",
            acao="policy_certificado.avaliar_estado_inicial(inventario_da_policy())",
            esperado=f"{esperado}{' / ' + motivo if motivo else ''}, sem mutacao",
            observado=(
                f"decisao={decisao.decisao!r} motivo={decisao.motivo!r} "
                f"registro_intacto={intacto}"
            ),
            classificacao=(
                PASS if certo and intacto
                else FAIL_UNSAFE if not intacto
                else FAIL_SAFE
            ),
            finding="PREEXISTING_POLICY_DESTRUCTIVE_REPLACEMENT",
        ))


def _policy_completa(cn: str) -> dict:
    return {
        str(i): json.dumps({"pattern": url, "filter": {"SUBJECT": {"CN": cn}}})
        for i, url in enumerate(cert_windows.CERT_URLS, 1)
    }


def _valores_hkcu() -> dict:
    colmeia = cert_windows.inventario_da_policy()[0]
    return {r.nome: (r.padrao, r.cn, r.reconhecida) for r in colmeia.regras}


# ── Grupo 5 · crash handoff (§17 a §21) ──────────────────────────────────────

def grupo_crash(caderno: Caderno, seco: bool) -> None:
    itens = (
        ("O", "§17", "guardiao segura o lease apos a morte abrupta do parent"),
        ("P", "§17", "segunda execucao continua recusada durante a limpeza"),
        ("O2", "§17", "apos a limpeza confirmada, o host volta a ser adquirivel"),
    )
    if seco:
        for item, secao, cenario in itens:
            caderno.anotar(Evidencia(item=item, cenario=cenario, secao=secao,
                                     observado="execucao seca — nada foi tocado"))
        return

    filho = _filho("parent-que-morre")
    try:
        pronto = _esperar_marca(filho, "PRONTO", limite=120)
        if not pronto:
            caderno.anotar(Evidencia(
                item="O", secao="§17", cenario=itens[0][2],
                observado="o parent filho nao chegou a sinalizar PRONTO",
                classificacao=NOT_TESTED))
            return

        # §18: TerminateProcess, e nao Ctrl+C — nenhum `finally` do Python roda.
        subprocess.run(  # noqa: S603
            ["taskkill", "/F", "/PID", str(filho.pid)],  # noqa: S607
            capture_output=True, check=False,
        )
        time.sleep(1)

        codigo, _ = _rodar("segunda-instancia")
        caderno.anotar(Evidencia(
            item="P", secao="§17 §18",
            cenario="segunda execucao durante o intervalo entre crash e limpeza",
            precondicao="parent morto por TerminateProcess; guardiao ainda limpando",
            acao="nova instancia chama adquirir()",
            esperado=f"recusada; saida {SAIDA_RECUSADO}",
            observado=f"saida {codigo}",
            classificacao=PASS if codigo == SAIDA_RECUSADO else FAIL_UNSAFE,
            finding="HOST_LOCK_CRASH_HANDOFF_RACE",
        ))

        limpou = _esperar(lambda: not cert_windows.policy_existe(), limite=180)
        caderno.anotar(Evidencia(
            item="O", secao="§17",
            cenario="o guardiao detecta a morte e remove a policy",
            precondicao="parent morto sem cleanup normal",
            acao="observar inventario_da_policy ate esvaziar",
            esperado="policy removida pelo guardiao elevado",
            observado=f"limpou={limpou}",
            classificacao=PASS if limpou else FAIL_SAFE,
            finding="HOST_LOCK_CRASH_HANDOFF_RACE",
        ))

        livre = _esperar(
            lambda: _rodar("segunda-instancia")[0] == SAIDA_ADQUIRIU, limite=120
        )
        caderno.anotar(Evidencia(
            item="O2", secao="§17",
            cenario="so depois da limpeza o host volta a ser adquirivel",
            precondicao="guardiao terminou e fechou o handle do lease",
            acao="nova instancia chama adquirir()",
            esperado=f"adquire; saida {SAIDA_ADQUIRIU}",
            observado=f"adquiriu={livre}",
            classificacao=PASS if livre else FAIL_SAFE,
            finding="HOST_LOCK_CRASH_HANDOFF_RACE",
        ))
    finally:
        if filho.poll() is None:
            filho.kill()


def _esperar(condicao, limite: int) -> bool:
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(1)
    return False


def _esperar_marca(processo: subprocess.Popen, marca: str, limite: int) -> bool:
    fim = time.monotonic() + limite
    while time.monotonic() < fim and processo.poll() is None:
        linha = processo.stdout.readline() if processo.stdout else ""
        if linha.strip() == marca:
            return True
        if not linha:
            time.sleep(0.2)
    return False


# ── Papeis dos processos filhos ───────────────────────────────────────────────

def papel_segunda_instancia() -> int:
    """Tenta adquirir e REPORTA o que chegou a tocar antes de ser recusada."""
    marcas = {
        "tocou_registro": False,
        "abriu_chrome": False,
        "cert_subject_cn": os.environ.get("CERT_SUBJECT_CN", ""),
    }
    antes = cert_windows.inventario_da_policy()
    try:
        controle = exclusividade_host.adquirir()
    except exclusividade_host.ExecucaoJaAtivaNoHost:
        codigo = SAIDA_RECUSADO
    except exclusividade_host.FalhaAoVerificarExclusividade:
        codigo = SAIDA_FALHA_AO_VERIFICAR
    else:
        exclusividade_host.liberar(controle)
        codigo = SAIDA_ADQUIRIU

    depois = cert_windows.inventario_da_policy()
    marcas["tocou_registro"] = antes != depois
    marcas["abriu_chrome"] = _ha_chrome_deste_processo()
    marcas["cert_subject_cn_mudou"] = (
        marcas["cert_subject_cn"] != os.environ.get("CERT_SUBJECT_CN", "")
    )
    print(f"MARCAS={json.dumps(marcas, ensure_ascii=False)}")
    return codigo


def _ha_chrome_deste_processo() -> bool:
    """Chrome iniciado POR ESTE processo. Nao conta o Chrome do operador."""
    saida = subprocess.run(  # noqa: S603
        ["wmic", "process", "where",  # noqa: S607
         f"ParentProcessId={os.getpid()}", "get", "Name"],
        capture_output=True, text=True, check=False,
    )
    return "chrome" in (saida.stdout or "").lower()


def papel_parent_que_morre() -> int:
    """Adquire o lease, sobe o guardiao real, avisa e espera ser morto."""
    controle = exclusividade_host.adquirir()  # noqa: F841 — morre com o processo
    resultado = maquina.garantir_policy_do_windows(CN_A)
    if not resultado.tem_guardiao:
        print("SEM_GUARDIAO", flush=True)
        return 1
    print("PRONTO", flush=True)
    while True:
        time.sleep(5)


GRUPOS = {
    "lease": grupo_lease,
    "policy": grupo_policy,
    "residuo": grupo_residuo,
    "startup": grupo_startup,
    "crash": grupo_crash,
}

PAPEIS = {
    "segunda-instancia": papel_segunda_instancia,
    "parent-que-morre": papel_parent_que_morre,
}
