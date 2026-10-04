import pytest

from assist_decider_server.config import ConfigError, load_settings, require_token


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("TOKEN", "TOKEN_FILE", "PORT", "CONFIG", "MODEL"):
        monkeypatch.delenv(f"ASSIST_DECIDER_{name}", raising=False)


def test_precedence(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text('port = 9000\nmodel = "english"\n')
    monkeypatch.setenv("ASSIST_DECIDER_PORT", "9100")
    s = load_settings(str(cfg))
    assert (s.port, s.model) == (9100, "english")
    assert load_settings(str(cfg), port=9200).port == 9200


def test_token_and_token_file_conflict(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'token = "{"a" * 40}"\n')
    monkeypatch.setenv("ASSIST_DECIDER_TOKEN_FILE", str(tmp_path / "t"))
    with pytest.raises(ConfigError):
        load_settings(str(cfg))


def test_token_file(tmp_path, monkeypatch):
    (tmp_path / "t").write_text("b" * 40 + "\n")
    monkeypatch.setenv("ASSIST_DECIDER_TOKEN_FILE", str(tmp_path / "t"))
    assert require_token(load_settings()) == "b" * 40


@pytest.mark.parametrize("kw", [{"model": "x"}, {"device": "gpu"}, {"port": 0}, {"log_level": "x"}])
def test_invalid(kw):
    with pytest.raises(ConfigError):
        load_settings(**kw)


def test_short_token_rejected(monkeypatch):
    monkeypatch.setenv("ASSIST_DECIDER_TOKEN", "short")
    with pytest.raises(ConfigError):
        require_token(load_settings())


def test_unknown_key(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text("hots = 1\n")
    with pytest.raises(ConfigError):
        load_settings(str(cfg))


def test_repr_hides_token():
    assert "secret" not in repr(load_settings(token="secret" * 6))
