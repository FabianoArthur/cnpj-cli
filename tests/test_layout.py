from cnpj_etl import layout


def test_find_csvs_matches_prefix_case_insensitively(tmp_path):
    for name in ["K3241.K03200Y0.D60510.EMPRECSV", "k3241.k03200y1.d60510.emprecsv", "other.txt"]:
        (tmp_path / name).write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "F.K03200$W.SIMPLES.CSV.D60510").write_text("x")
    assert [p.name for p in layout.find_csvs(tmp_path, "EMPRECSV")] == [
        "K3241.K03200Y0.D60510.EMPRECSV",
        "k3241.k03200y1.d60510.emprecsv",
    ]
    assert len(layout.find_csvs(tmp_path, "SIMPLES")) == 1


def test_iter_rows_decodes_latin1_and_fixes_width(tmp_path):
    f = tmp_path / "x.EMPRECSV"
    f.write_bytes('"1";"CAFÉ"\n"2";"B";"extra";"more"\n"3"\n'.encode("latin1"))
    assert list(layout.iter_rows(f, 3)) == [
        ["1", "CAFÉ", ""],
        ["2", "B", "extra"],
        ["3", "", ""],
    ]


def test_table_selection():
    assert set(layout.select_tables(None)) == set(layout.ALL_TABLES)
    assert list(layout.select_tables(["socios"])) == ["socios"]
    assert layout.column_names("simples")[0] == "cnpj_basico"
