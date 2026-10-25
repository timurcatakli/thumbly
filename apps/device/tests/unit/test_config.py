import pytest

from thumbly import config as config_module
from thumbly.config import Config, load_config, public_origin


def test_public_url_normalized_and_optional():
    assert Config(backend="simulated").public_url is None
    config = Config(backend="simulated", public_url="https://My-Mac.Tailnet.ts.net/")
    assert config.public_url == "https://my-mac.tailnet.ts.net"
    assert config.mcp_port == 8090
    assert public_origin("https://mac.example.com:8443") == "https://mac.example.com:8443"
    assert public_origin("https://mac.example.com:443") == "https://mac.example.com"


@pytest.mark.parametrize(
    "value",
    [
        "http://my-mac.tailnet.ts.net",
        "https://my-mac.tailnet.ts.net/mcp",
        "https://my-mac.tailnet.ts.net?x=1",
        "https://my-mac.tailnet.ts.net#frag",
        "https://user:pw@my-mac.tailnet.ts.net",
        "my-mac.tailnet.ts.net",
        "",
    ],
)
def test_public_url_rejects_non_origins(value):
    with pytest.raises(ValueError):
        Config(backend="simulated", public_url=value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dashboard_port": 8771},  # Same as the local MCP entry.
        {"public_url": "https://my-mac.tailnet.ts.net", "mcp_port": 8770},
        {"public_url": "https://my-mac.tailnet.ts.net", "local_mcp_port": 8090},
    ],
)
def test_ports_must_not_collide(kwargs):
    with pytest.raises(ValueError):
        Config(backend="simulated", **kwargs)


def test_unused_remote_port_may_repeat_and_zero_disables():
    config = Config(backend="simulated", dashboard_port=8090, local_mcp_port=0)
    assert config.mcp_port == config.dashboard_port and config.local_mcp_port == 0


def test_unreadable_binding_reads_as_none(tmp_path):
    from thumbly.adapters.storage.pairing import PairingStore

    (tmp_path / "binding.json").write_text(
        '{"usb_identity":"old-phone","owner_confirmed":true,"portrait_locked":true}'
    )
    assert PairingStore(tmp_path).read() is None


def test_defaults_need_no_settings_file_and_keep_state_in_library(tmp_path):
    config = load_config(tmp_path / "missing.toml")
    assert config.state_dir == config_module.HOME and config.runtime_dir == config_module.HOME / "run"
    assert config_module.HOME.parts[-3:] == ("Library", "Application Support", "Thumbly")
    assert (config.dashboard_port, config.local_mcp_port) == (8770, 8771)
    (tmp_path / "config.toml").write_text("dashboard_port = 9000\nauto_pair = false\n")
    loaded = load_config(tmp_path / "config.toml")
    assert loaded.dashboard_port == 9000 and loaded.auto_pair is False


def test_settings_file_follows_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("THUMBLY_CONFIG", str(tmp_path / "custom.toml"))
    assert config_module.default_config_path() == tmp_path / "custom.toml"
    monkeypatch.delenv("THUMBLY_CONFIG")
    assert config_module.default_config_path() == config_module.HOME / "config.toml"


def test_unknown_settings_are_rejected(tmp_path):
    (tmp_path / "config.toml").write_text('wifi_interface = "wlan0"\n')
    with pytest.raises(ValueError):
        load_config(tmp_path / "config.toml")
