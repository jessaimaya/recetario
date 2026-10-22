from recetario_ingest import cli, ingest


def test_the_load_command_loads_the_finished_books(tmp_path, monkeypatch, capsys):
    config_file = tmp_path / "recetario.toml"
    config_file.write_text("")
    monkeypatch.setattr(ingest, "load_all", lambda config: [])

    assert cli.main(["load", "--config", str(config_file)]) == 0
    assert "no finished books to load" in capsys.readouterr().out
