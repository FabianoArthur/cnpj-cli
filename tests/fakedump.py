"""A tiny, entirely fictional CNPJ dump with the same shape as the real one.

The real monthly archive is a tar.gz (sometimes a zip) holding one zip per
table chunk; each zip holds a latin-1, ``;``-separated CSV without header whose
file name contains the table prefix (``EMPRECSV``, ``ESTABELE``...).
Every company, person and address below is made up.
"""

from __future__ import annotations

import io
import random
import tarfile
import zipfile
from pathlib import Path

EMPRESAS = [
    ["11222333", "PADARIA EXEMPLO LTDA", "2062", "49", "50000,00", "01", ""],
    ["44555666", "CAFÉ FICTÍCIO S.A.", "2054", "10", "1250000,00", "05", ""],
    ["77888999", "OFICINA MODELO ME", "2135", "50", "1000,00"],  # short row: padded
]
ESTABELECIMENTOS = [
    [
        "11222333",
        "0001",
        "81",
        "1",
        "PADARIA DO EXEMPLO",
        "02",
        "20150302",
        "00",
        "",
        "",
        "20150302",
        "1091102",
        "4721102,5611203",
        "RUA",
        "DAS FLORES",
        "100",
        "",
        "CENTRO",
        "01001000",
        "SP",
        "7107",
        "11",
        "40000000",
        "",
        "",
        "",
        "",
        "contato@example.com",
        "",
        "",
    ],
    [
        "11222333",
        "0002",
        "62",
        "2",
        "PADARIA DO EXEMPLO FILIAL",
        "02",
        "20180101",
        "00",
        "",
        "",
        "20180101",
        "1091102",
        "",
        "AVENIDA",
        "BRASIL",
        "2000",
        "LOJA 3",
        "JARDIM",
        "01002000",
        "SP",
        "7107",
        "11",
        "40000001",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ],
    [
        "44555666",
        "0001",
        "81",
        "1",
        "CAFÉ FICTÍCIO",
        "08",
        "20240115",
        "01",
        "",
        "",
        "20001010",
        "5611203",
        "",
        "RUA",
        "SÃO JOÃO",
        "5",
        "",
        "CENTRO",
        "30110000",
        "MG",
        "4123",
        "31",
        "30000000",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ],
]
SOCIOS = [
    [
        "11222333",
        "2",
        "MARIA EXEMPLO",
        "***123456**",
        "49",
        "20150302",
        "",
        "***000000**",
        "",
        "00",
        "4",
    ],
    [
        "11222333",
        "2",
        "JOÃO MODELO",
        "***654321**",
        "22",
        "20150302",
        "",
        "***000000**",
        "",
        "00",
        "5",
    ],
]
SIMPLES = [["11222333", "S", "20150302", "", "N", "", ""]]
CNAES = [
    ["1091102", "Fabricação de produtos de padaria e confeitaria"],
    ["5611203", "Lanchonetes e similares"],
]
MUNICIPIOS = [["7107", "SAO PAULO"], ["4123", "BELO HORIZONTE"]]
NATUREZAS = [["2062", "Sociedade Empresária Limitada"], ["2054", "Sociedade Anônima Fechada"]]
PAISES = [["105", "BRASIL"]]
QUALIFICACOES = [["49", "Sócio-Administrador"], ["22", "Sócio"]]
MOTIVOS = [["00", "SEM MOTIVO"], ["01", "EXTINCAO POR ENCERRAMENTO LIQUIDACAO VOLUNTARIA"]]

# zip member name -> rows. Two chunks for estabelecimentos, like the real dump.
TABLES = {
    "Empresas0": ("K3241.K03200Y0.D60510.EMPRECSV", EMPRESAS),
    "Estabelecimentos0": ("K3241.K03200Y0.D60510.ESTABELE", ESTABELECIMENTOS[:2]),
    "Estabelecimentos1": ("K3241.K03200Y1.D60510.ESTABELE", ESTABELECIMENTOS[2:]),
    "Socios0": ("K3241.K03200Y0.D60510.SOCIOCSV", SOCIOS),
    "Simples": ("F.K03200$W.SIMPLES.CSV.D60510", SIMPLES),
    "Cnaes": ("F.K03200$Z.D60510.CNAECSV", CNAES),
    "Municipios": ("F.K03200$Z.D60510.MUNICCSV", MUNICIPIOS),
    "Naturezas": ("F.K03200$Z.D60510.NATJUCSV", NATUREZAS),
    "Paises": ("F.K03200$Z.D60510.PAISCSV", PAISES),
    "Qualificacoes": ("F.K03200$Z.D60510.QUALSCSV", QUALIFICACOES),
    "Motivos": ("F.K03200$Z.D60510.MOTICSV", MOTIVOS),
}


def _csv(rows: list[list[str]]) -> bytes:
    lines = [";".join(f'"{v}"' for v in row) for row in rows]
    return ("\n".join(lines) + "\n").encode("latin1")


def _inner_zip(member: str, rows: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, _csv(rows))
    return buf.getvalue()


def build_tar_gz(padding: int = 0) -> bytes:
    """The monthly archive as bytes. ``padding`` adds a filler file to make it bigger."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, (member, rows) in TABLES.items():
            data = _inner_zip(member, rows)
            info = tarfile.TarInfo(f"{name}.zip")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if padding:
            filler = random.Random(padding).randbytes(padding)  # incompressible, deterministic
            info = tarfile.TarInfo("README.txt")
            info.size = len(filler)
            tar.addfile(info, io.BytesIO(filler))
    return buf.getvalue()


def build_zip() -> bytes:
    """Same content, packed as a zip (the share sometimes serves zips)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, (member, rows) in TABLES.items():
            z.writestr(f"{name}.zip", _inner_zip(member, rows))
    return buf.getvalue()


def write_month(base: Path, month: str) -> Path:
    """Write ``<base>/<month>/dados.tar.gz`` as if ``cnpj download`` had run."""
    folder = base / month
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / "dados.tar.gz"
    archive.write_bytes(build_tar_gz())
    return archive
