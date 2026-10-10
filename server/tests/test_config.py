import pytest

from assist_decider_server.config import (
    ConfigError,
    load_settings,
    remember_model,
    remembered_model,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("PORT", "CONFIG", "MODEL"):
        monkeypatch.delenv(f"ASSIST_DECIDER_{name}", raising=False)


def test_precedence(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text('port = 9000\nmodel = "english"\n')
    monkeypatch.setenv("ASSIST_DECIDER_PORT", "9100")
    s = load_settings(str(cfg))
    assert (s.port, s.model) == (9100, "english")
    assert load_settings(str(cfg), port=9200).port == 9200


@pytest.mark.parametrize("kw", [{"model": "x"}, {"device": "gpu"}, {"port": 0}, {"log_level": "x"}])
def test_invalid(kw):
    with pytest.raises(ConfigError):
        load_settings(**kw)


def test_unknown_key(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text("hots = 1\n")
    with pytest.raises(ConfigError):
        load_settings(str(cfg))


def test_remembered_model(tmp_path):
    state = tmp_path / "sub" / "state.json"
    settings = load_settings(model="multilingual", state_file=str(state))
    assert remembered_model(settings) == "multilingual"  # nothing chosen yet
    remember_model(settings, "d1-3b")
    assert remembered_model(settings) == "d1-3b"
    # Changing the setting overrides the choice made under the old one.
    assert remembered_model(load_settings(model="english", state_file=str(state))) == "english"
    state.write_text("{broken")
    assert remembered_model(settings) == "multilingual"
