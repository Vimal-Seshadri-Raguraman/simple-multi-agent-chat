import pytest

from analyst_agent.config import ConfigError, load_config

BASE = {"ANTHROPIC_API_KEY": "sk-ant-test", "SMAC_AGENT_CODE": "abc123"}


def test_defaults_applied():
    cfg = load_config(BASE)
    assert cfg.smac_url == "http://127.0.0.1:8000"
    assert cfg.agent_name == "Analyst"
    assert cfg.model == "claude-sonnet-5"
    assert cfg.max_replies_per_min == 6 and cfg.max_hops == 3


def test_missing_anthropic_key_is_actionable():
    with pytest.raises(ConfigError) as e:
        load_config({"SMAC_AGENT_CODE": "abc123"})
    assert "ANTHROPIC_API_KEY" in str(e.value) and ".env" in str(e.value)


def test_trailing_slash_stripped():
    assert (
        load_config({**BASE, "SMAC_URL": "http://x:8001/"}).smac_url == "http://x:8001"
    )


def test_code_may_be_absent_when_credentials_exist():
    # the loop decides; load_config only rejects a config that can NEVER work
    cfg = load_config({"ANTHROPIC_API_KEY": "sk-ant-test"})
    assert cfg.agent_code is None


def test_numeric_overrides_must_be_positive_ints():
    with pytest.raises(ConfigError):
        load_config({**BASE, "MAX_HOPS": "zero"})


def test_numeric_override_zero_is_rejected():
    # "positive" means > 0, not merely parseable
    with pytest.raises(ConfigError):
        load_config({**BASE, "MAX_REPLIES_PER_MIN": "0"})


def test_error_names_the_offending_variable():
    with pytest.raises(ConfigError) as e:
        load_config({**BASE, "MAX_HOPS": "zero"})
    assert "MAX_HOPS" in str(e.value)
