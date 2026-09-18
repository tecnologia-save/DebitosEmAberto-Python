"""Os certificados do cofre entram no Windows durante a execucao — e saem.

Motivo (D8.4): entregue como arquivo, o certificado poe o proxy do Playwright
entre o Chrome e o gov.br, e o portal desconfia e pede captcha. Instalado, o
proprio Chrome o apresenta, como no executavel desktop.

Nada aqui toca o Windows: inspecionar, gravar e remover sao falsos. A conftest
ainda bloqueia o PowerShell real para a suite inteira.
"""
import json

import pytest

from automation.login import Certificado
from certificados_instalados import CertificadosInstalados, Instalado

CN_A = "ALFA FICTICIA LTDA:11111111000191"
CN_B = "BETA FICTICIA LTDA:22222222000191"


class _Cofre:
    def __init__(self, itens):
        self._itens = itens
        self.carregado = False

    def carregar(self):
        self.carregado = True
        return len(self._itens)

    def itens(self):
        return dict(self._itens)

    def resolver(self, nome):
        return nome

    def certificado(self, chave):
        return self._itens[chave]


def _arquivo(n):
    return Certificado(subject_cn="", pfx_path=f"{n}.pfx", pfx_senha="senha-ficticia")


class _Windows:
    """O repositorio do usuario, em memoria."""

    def __init__(self, ja_instalados=(), falha_ao_inspecionar=(), falha_ao_remover=()):
        self.store = set(ja_instalados)
        self.falha_ao_inspecionar = set(falha_ao_inspecionar)
        self.falha_ao_remover = set(falha_ao_remover)
        self.pfx = {"0.pfx": ("IMPA", CN_A, "0A01"), "1.pfx": ("IMPB", CN_B, "0B02")}
        self.ordem = []

    def inspecionar(self, caminho, senha):
        if caminho in self.falha_ao_inspecionar:
            raise RuntimeError("PowerShell terminou com codigo 1")
        imp, cn, serial = self.pfx[caminho]
        return Instalado(imp, cn, serial, imp in self.store)

    def gravar(self, caminho, senha):
        self.ordem.append(("gravar", caminho))
        self.store.add(self.pfx[caminho][0])

    def remover(self, impressao):
        if impressao in self.falha_ao_remover:
            raise RuntimeError("PowerShell terminou com codigo 1")
        self.ordem.append(("remover", impressao))
        self.store.discard(impressao)


@pytest.fixture
def marcador(tmp_path):
    return tmp_path / "persistente" / "certificados-instalados.json"


def _provedor(windows, marcador, itens=None):
    itens = itens or {"alfa": _arquivo(0), "beta": _arquivo(1)}
    return CertificadosInstalados(_Cofre(itens), marcador, inspecionar=windows.inspecionar,
                                  gravar=windows.gravar, remover=windows.remover)


def test_instala_e_entrega_o_modo_windows_store(marcador):
    windows = _Windows()
    provedor = _provedor(windows, marcador)

    assert provedor.carregar() == 2

    assert provedor.certificado("alfa") == Certificado(subject_cn=CN_A, serial="0A01")
    assert provedor.certificado("beta").do_windows_store
    assert windows.store == {"IMPA", "IMPB"}


def test_encerrar_remove_o_que_instalou_e_limpa_o_marcador(marcador):
    windows = _Windows()
    provedor = _provedor(windows, marcador)
    provedor.carregar()

    provedor.encerrar()

    assert windows.store == set()
    assert not marcador.exists()


def test_o_que_ja_estava_instalado_e_usado_e_NUNCA_removido(marcador):
    windows = _Windows(ja_instalados={"IMPA"})
    provedor = _provedor(windows, marcador)
    provedor.carregar()
    provedor.encerrar()

    assert provedor.certificado("alfa").subject_cn == CN_A
    assert windows.store == {"IMPA"}, "o certificado do operador fica"
    assert ("gravar", "0.pfx") not in windows.ordem


def test_o_marcador_e_gravado_ANTES_da_instalacao(marcador):
    windows = _Windows()
    vistos = []

    def gravar(caminho, senha):
        vistos.append(json.loads(marcador.read_text(encoding="utf-8")))
        windows.gravar(caminho, senha)

    provedor = CertificadosInstalados(_Cofre({"alfa": _arquivo(0)}), marcador,
                                      inspecionar=windows.inspecionar, gravar=gravar,
                                      remover=windows.remover)
    provedor.carregar()

    assert vistos == [["IMPA"]]


def test_residuo_de_uma_execucao_que_morreu_sai_no_inicio_da_proxima(marcador):
    windows = _Windows()
    _provedor(windows, marcador).carregar()          # morreu sem encerrar
    assert windows.store == {"IMPA", "IMPB"}

    windows.ordem.clear()
    seguinte = _provedor(windows, marcador, {"alfa": _arquivo(0)})
    seguinte.carregar()

    assert windows.ordem[:2] == [("remover", "IMPA"), ("remover", "IMPB")]
    seguinte.encerrar()
    assert windows.store == set()


def test_falha_ao_instalar_mantem_aquele_alias_no_modo_arquivo(marcador):
    windows = _Windows(falha_ao_inspecionar={"1.pfx"})
    provedor = _provedor(windows, marcador)
    provedor.carregar()

    assert provedor.certificado("alfa").do_windows_store
    assert provedor.certificado("beta") == _arquivo(1)
    assert json.loads(marcador.read_text(encoding="utf-8")) == ["IMPA"]


def test_falha_ao_remover_fica_no_marcador_para_a_proxima(marcador, capsys):
    windows = _Windows(falha_ao_remover={"IMPB"})
    provedor = _provedor(windows, marcador)
    provedor.carregar()

    provedor.encerrar()

    assert json.loads(marcador.read_text(encoding="utf-8")) == ["IMPB"]
    assert "não puderam ser removidos" in capsys.readouterr().out


def test_nao_pede_policy_do_windows():
    assert CertificadosInstalados.pede_policy_do_windows is False


def test_nada_de_alias_cn_serial_ou_caminho_no_log(marcador, capsys):
    windows = _Windows()
    provedor = _provedor(windows, marcador)
    provedor.carregar()
    provedor.encerrar()

    saida = capsys.readouterr().out
    for sensivel in ("alfa", "beta", CN_A, "11111111000191", "0A01", ".pfx", "IMPA"):
        assert sensivel not in saida
    assert "2 certificado(s) do cofre instalado(s)" in saida


def test_a_senha_vai_pela_entrada_padrao_e_nao_pela_linha_de_comando(monkeypatch):
    import certificados_instalados as ci
    visto = {}

    def powershell(script, env_extra, entrada=""):
        visto.update(script=script, env=env_extra, entrada=entrada)
        return json.dumps({"impressao": "IMP", "cn": CN_A, "serial": "01",
                           "ja": False, "chave": True})
    monkeypatch.setattr(ci, "_powershell", powershell)

    ci.inspecionar_no_windows("C:/x/0.pfx", "senha-ficticia")

    assert visto["entrada"] == "senha-ficticia\n"
    assert "senha-ficticia" not in visto["script"]
    assert "senha-ficticia" not in json.dumps(visto["env"])
