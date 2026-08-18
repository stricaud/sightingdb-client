"""Where settings come from, and which source wins."""

from __future__ import annotations

import pytest

import sightingdb
from sightingdb.config import Config


def write_config(tmp_path, text):
    path = tmp_path / "client.toml"
    path.write_text(text)
    return path


def test_defaults_point_at_a_local_server():
    config = Config.resolve(use_env=False, use_file=False)
    assert config.url == "https://localhost:9999"
    assert config.apikey is None
    assert config.verify is True


def test_arguments_beat_the_environment_which_beats_the_file(tmp_path, monkeypatch):
    path = write_config(
        tmp_path,
        """
        [client]
        url = "https://from-file:1111"
        apikey = "file-key"
        timeout = 33.0
        """,
    )
    monkeypatch.setenv("SIGHTINGDB_URL", "https://from-env:2222")
    monkeypatch.setenv("SIGHTINGDB_APIKEY", "env-key")

    config = Config.resolve(apikey="argument-key", config_file=path)
    assert config.apikey == "argument-key"  # argument
    assert config.url == "https://from-env:2222"  # environment
    assert config.timeout == 33.0  # file


def test_sources_combine_field_by_field(tmp_path, monkeypatch):
    path = write_config(tmp_path, '[client]\nurl = "https://db.example:9999"\n')
    monkeypatch.setenv("SIGHTINGDB_APIKEY", "env-key")
    config = Config.resolve(config_file=path)
    assert (config.url, config.apikey) == ("https://db.example:9999", "env-key")


def test_host_and_port_build_a_url():
    assert Config.resolve(
        host="db.example", port=9999, use_env=False, use_file=False
    ).url == ("https://db.example:9999")
    assert Config.resolve(
        host="db.example", port=8080, ssl=False, use_env=False, use_file=False
    ).url == ("http://db.example:8080")


def test_a_url_without_a_scheme_is_assumed_to_be_https():
    assert Config.resolve(url="db.example:9999", use_env=False, use_file=False).url == (
        "https://db.example:9999"
    )


def test_port_alone_adjusts_whatever_url_the_other_sources_gave(monkeypatch):
    monkeypatch.setenv("SIGHTINGDB_URL", "https://db.example:9999")
    assert Config.resolve(port=8443, use_file=False).url == "https://db.example:8443"


def test_url_and_host_together_are_a_mistake():
    with pytest.raises(sightingdb.ConfigurationError):
        Config.resolve(url="https://a", host="b", use_env=False, use_file=False)


def test_the_environment_can_build_a_url_from_parts(monkeypatch):
    monkeypatch.setenv("SIGHTINGDB_HOST", "db.example")
    monkeypatch.setenv("SIGHTINGDB_PORT", "1234")
    monkeypatch.setenv("SIGHTINGDB_SSL", "false")
    assert Config.resolve(use_file=False).url == "http://db.example:1234"


@pytest.mark.parametrize(
    "text, expected",
    [("false", False), ("0", False), ("no", False), ("true", True), ("1", True)],
)
def test_verify_reads_as_a_boolean(monkeypatch, text, expected):
    monkeypatch.setenv("SIGHTINGDB_VERIFY", text)
    assert Config.resolve(use_file=False).verify is expected


def test_verify_can_be_a_path_to_a_ca_bundle(monkeypatch):
    monkeypatch.setenv("SIGHTINGDB_VERIFY", "/etc/ssl/sightingdb.pem")
    assert Config.resolve(use_file=False).verify == "/etc/ssl/sightingdb.pem"


def test_a_flat_config_file_works_too(tmp_path):
    path = write_config(tmp_path, 'url = "https://flat.example:9999"\napikey = "k"\n')
    config = Config.resolve(config_file=path, use_env=False)
    assert (config.url, config.apikey) == ("https://flat.example:9999", "k")


def test_a_config_file_can_use_host_and_port(tmp_path):
    path = write_config(tmp_path, '[client]\nhost = "db.example"\nport = 8443\nssl = false\n')
    assert Config.resolve(config_file=path, use_env=False).url == "http://db.example:8443"


def test_a_missing_default_config_file_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sightingdb.config.DEFAULT_CONFIG_PATH", tmp_path / "absent.toml"
    )
    assert Config.resolve(use_env=False).url == "https://localhost:9999"


def test_a_missing_named_config_file_is_an_error(tmp_path):
    with pytest.raises(sightingdb.ConfigurationError, match="No such configuration"):
        Config.resolve(config_file=tmp_path / "absent.toml", use_env=False)


def test_an_unreadable_config_file_says_so(tmp_path):
    path = write_config(tmp_path, "this is not = = toml")
    with pytest.raises(sightingdb.ConfigurationError, match="Cannot read"):
        Config.resolve(config_file=path, use_env=False)


def test_sightingdb_config_names_the_file(tmp_path, monkeypatch):
    path = write_config(tmp_path, '[client]\napikey = "from-config-env"\n')
    monkeypatch.setenv("SIGHTINGDB_CONFIG", str(path))
    assert Config.resolve().apikey == "from-config-env"


def test_nonsense_settings_are_refused():
    with pytest.raises(sightingdb.ConfigurationError):
        Config.resolve(timeout=0, use_env=False, use_file=False)
    with pytest.raises(sightingdb.ConfigurationError):
        Config.resolve(max_retries=-1, use_env=False, use_file=False)
    with pytest.raises(sightingdb.ConfigurationError):
        Config.resolve(use_env=False, use_file=False).replace(timeout=-5.0)


def test_the_api_key_is_not_in_the_repr():
    config = Config.resolve(apikey="hunter2", use_env=False, use_file=False)
    assert "hunter2" not in repr(config)


def test_a_client_can_be_isolated_from_the_environment(monkeypatch):
    monkeypatch.setenv("SIGHTINGDB_URL", "https://not-this-one:9999")
    db = sightingdb.SightingDB("https://chosen:9999", use_env=False, use_file=False)
    with db:
        assert db.config.url == "https://chosen:9999"
