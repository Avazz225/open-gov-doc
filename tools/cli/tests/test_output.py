import json

from dms_cli import output


def test_print_table_with_rows(capsys):
    output.print_table([{"id": 1, "name": "Alpha"}, {"id": 22, "name": "B"}])

    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ["id", "name"]
    assert "1" in out[2]
    assert "Alpha" in out[2]


def test_print_table_empty(capsys):
    output.print_table([])

    assert capsys.readouterr().out.strip() == "Keine Ergebnisse."


def test_print_table_serializes_nested_values(capsys):
    output.print_table([{"id": 1, "payload": {"a": 1}}])

    out = capsys.readouterr().out
    assert '{"a": 1}' in out


def test_print_json(capsys):
    output.print_json({"a": 1, "b": [1, 2]})

    parsed = json.loads(capsys.readouterr().out)
    assert parsed == {"a": 1, "b": [1, 2]}


def test_print_record(capsys):
    output.print_record({"id": 1, "name": "Alpha"})

    out = capsys.readouterr().out
    assert "id " in out or "id  " in out
    assert "Alpha" in out


def test_emit_json_format(capsys):
    output.emit([{"a": 1}], output_format="json")

    parsed = json.loads(capsys.readouterr().out)
    assert parsed == [{"a": 1}]


def test_emit_table_format_list(capsys):
    output.emit([{"a": 1}], output_format="table")

    assert "a" in capsys.readouterr().out


def test_emit_table_format_dict(capsys):
    output.emit({"a": 1}, output_format="table")

    assert "a " in capsys.readouterr().out
