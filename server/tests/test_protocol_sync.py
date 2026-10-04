from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_protocol_copies_are_identical():
    server = ROOT / "server" / "assist_decider_server" / "protocol.py"
    ha = ROOT / "custom_components" / "assist_decider" / "protocol.py"
    assert server.read_bytes() == ha.read_bytes(), "Copy server protocol.py to the integration"
