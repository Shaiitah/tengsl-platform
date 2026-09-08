from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "tengsl-edge" / "app" / "main.py"


def load_command_codes():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "COMMAND_CODES":
                    return ast.literal_eval(node.value)
    raise AssertionError("COMMAND_CODES not found")


def test_command_codes_match_c2000_pp_manual():
    assert load_command_codes() == {
        "arm": 24,
        "disarm": 109,
        "enable": 111,
        "disable": 112,
    }


def test_zone_addresses_are_not_shifted_to_zero():
    source = MAIN.read_text(encoding="utf-8")
    assert "else 40000 + (zc.zone_number - 1)" in source
    assert "address -= 40000" not in source
