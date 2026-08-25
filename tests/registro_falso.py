"""Um registro do Windows de mentira, com a superficie que cert_windows.py usa.

Nenhum teste pode escrever no registro real, pedir UAC ou lancar processo
elevado. Este modulo substitui `winreg` no menor nivel possivel: as proprias
funcoes, mantendo a semantica que importa — inclusive as duas colmeias
independentes e os erros que cada uma pode levantar.
"""


class ColmeiaProtegida(Exception):
    """Marcador interno; nunca sai daqui — vira OSError de verdade."""


class Chave:
    def __init__(self, colmeia, caminho):
        self.colmeia = colmeia
        self.caminho = caminho
        self.fechada = False


class RegistroFalso:
    """Duas colmeias, cada uma podendo falhar de forma independente.

    `protegidas` recebe os rotulos das colmeias que recusam ESCRITA — e como
    HKLM se comporta sem elevacao. A leitura continua funcionando nas duas.
    """

    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_ALL_ACCESS = 0xF003F
    KEY_READ = 0x20019
    REG_SZ = 1

    def __init__(self, protegidas=()):
        # {colmeia: {caminho: {nome_valor: valor}}}
        self.dados: dict[str, dict[str, dict[str, str]]] = {"HKCU": {}, "HKLM": {}}
        self.protegidas = set(protegidas)
        self.operacoes: list[tuple] = []

    # ── superficie do winreg ──────────────────────────────────────────────────

    def CreateKeyEx(self, colmeia, caminho, reservado, acesso):
        self.operacoes.append(("CreateKeyEx", colmeia, caminho))
        if colmeia in self.protegidas:
            raise PermissionError(f"acesso negado a {colmeia}")
        self.dados[colmeia].setdefault(caminho, {})
        return Chave(colmeia, caminho)

    def OpenKeyEx(self, colmeia, caminho, reservado, acesso):
        """LEITURA nunca e barrada: sem elevacao o HKLM continua legivel — o que
        falha e escrever nele. E essa assimetria que produz colmeias divergentes."""
        self.operacoes.append(("OpenKeyEx", colmeia, caminho))
        if caminho not in self.dados[colmeia]:
            raise FileNotFoundError(caminho)
        return Chave(colmeia, caminho)

    def SetValueEx(self, chave, nome, reservado, tipo, valor):
        self.operacoes.append(("SetValueEx", chave.colmeia, nome))
        self.dados[chave.colmeia][chave.caminho][nome] = valor

    def QueryValueEx(self, chave, nome):
        valores = self.dados[chave.colmeia][chave.caminho]
        if nome not in valores:
            raise FileNotFoundError(nome)
        return valores[nome], self.REG_SZ

    def DeleteValue(self, chave, nome):
        valores = self.dados[chave.colmeia][chave.caminho]
        if nome not in valores:
            raise FileNotFoundError(nome)
        del valores[nome]

    def DeleteKey(self, colmeia, caminho):
        self.operacoes.append(("DeleteKey", colmeia, caminho))
        if colmeia in self.protegidas:
            raise PermissionError(f"acesso negado a {colmeia}")
        if caminho not in self.dados[colmeia]:
            raise FileNotFoundError(caminho)
        del self.dados[colmeia][caminho]

    def CloseKey(self, chave):
        chave.fechada = True

    # ── inspecao dos testes ───────────────────────────────────────────────────

    def valores(self, colmeia, caminho):
        return dict(self.dados[colmeia].get(caminho, {}))

    def tem(self, colmeia, caminho):
        return bool(self.dados[colmeia].get(caminho))
