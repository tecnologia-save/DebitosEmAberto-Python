"""A guarda que impede a suite de pedir UAC — e a prova de que ela morde.

Escrito depois de a fatia 13A.4 ter deixado tres dubles na funcao errada e a
suite ter aberto cinco caixas de consentimento do Windows. Ver o cabecalho de
`conftest.py`.

Este arquivo NAO eleva nada: ele confirma que a tentativa vira falha de teste.
"""
import pytest
from conftest import ElevacaoRealNaSuite

import cert_windows


def test_a_elevacao_real_vira_falha_de_teste():
    """Se algum dia um duble ficar no lugar errado, e isto que acontece: uma
    excecao com nome proprio, e nao uma janela esperando resposta."""
    with pytest.raises(ElevacaoRealNaSuite):
        cert_windows._elevar(["--guard", "0", "sentinela"])


def test_a_e_a_guarda_esta_em_TODOS_os_testes():
    """Autouse, e sem precisar ser pedida: quem escrever um teste novo nao tem
    de lembrar dela."""
    from pathlib import Path

    fonte = (Path(__file__).resolve().parent / "conftest.py").read_text(
        encoding="utf-8")

    assert "@pytest.fixture(autouse=True)" in fonte
    assert "ShellExecuteExW" in fonte


def test_b_o_duble_no_lugar_certo_continua_passando(monkeypatch):
    """A guarda nao atrapalha quem substitui a elevacao corretamente."""
    monkeypatch.setattr(cert_windows, "_elevar", lambda args: (True, 0xB0B0))

    assert cert_windows._elevar(["x"]) == (True, 0xB0B0)


def test_b_e_um_duble_em_RUNAS_nao_engana_mais_ninguem(monkeypatch):
    """O erro exato da 13A.4: substituir `_runas` e achar que a elevacao ficou
    interceptada. `_runas` nao e mais o caminho do guardiao."""
    monkeypatch.setattr(cert_windows, "_runas", lambda *a, **k: 0)

    with pytest.raises(ElevacaoRealNaSuite):
        cert_windows._lancar_guardiao("CN-SENTINELA-FICTICIO")
