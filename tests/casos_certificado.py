"""Corpus ficticio de certificados para caracterizar a regra de match.

NADA aqui e real: nem nome de pessoa, nem empresa, nem CNPJ, nem CN.

O formato imita o que `carregar_certificados` monta a partir do Windows: o MESMO
certificado e indexado sob varias chaves (FriendlyName, CN e CN sem o documento),
e o CN da ICP-Brasil tem a forma "NOME:documento".
"""


def _cert(display: str, nome_no_cn: str, documento: str, serial: str) -> dict:
    return {
        "display": display,
        "subject_cn": f"{nome_no_cn}:{documento}",
        "serial": serial,
    }


# Seis certificados ficticios. Documentos e seriais inventados.
ALVORADA = _cert("ALVORADA COMERCIO", "ALVORADA COMERCIO", "00000000000001", "AA01")
BERNARDO = _cert(
    "BERNARDO TEIXEIRA MONTENEGRO SOUSA",
    "BERNARDO TEIXEIRA MONTENEGRO SOUSA",
    "00000000001",
    "BB02",
)
XYZ = _cert("X Y Z CONSULTORIAS", "X Y Z CONSULTORIAS", "00000000000002", "CC03")
CARDOSO = _cert("EMPRESARIAL FICTICIA LTDA", "EMPRESARIAL FICTICIA LTDA", "00000000000003", "DD04")
DES = _cert("D&S ASSESSORIA", "D&S ASSESSORIA", "00000000000004", "EE05")
DSR = _cert("D.S.R. ASSESSORIA", "D.S.R. ASSESSORIA", "00000000000005", "FF06")


def _indexar(*certificados: dict) -> dict[str, dict]:
    """Reproduz a indexacao de `carregar_certificados`: display, CN e CN sem doc."""
    mapa: dict[str, dict] = {}
    for c in certificados:
        cn = c["subject_cn"]
        for nome in {c["display"], cn, cn.split(":")[0]}:
            chave = nome.strip().lower()
            if chave:
                mapa.setdefault(chave, c)
    return mapa


CERTS = _indexar(ALVORADA, BERNARDO, XYZ, CARDOSO, DES, DSR)

# Um unico certificado, indexado sob tres chaves — serve para provar que a
# deduplicacao por identidade nao transforma isso em empate.
CERTS_UM_SO = _indexar(BERNARDO)
