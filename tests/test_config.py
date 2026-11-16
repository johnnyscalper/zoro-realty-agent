"""Configuration boundary: no real keys, external requests or model inference."""

import json

import pytest

from app.config import Settings


SECRETS = {
    "MOLIT_TRADE_API_KEY": "offline-test-trade-private+/=",
    "MOLIT_RENT_API_KEY": "offline-test-rent-private+/=",
    "MOLIT_API_KEY": "offline-test-shared-private+/=",
    "ZORO_API_KEY": "offline-test-zoro-private+/=",
    "ZORO_BASE_URL": "https://private-zoro.invalid:9443/v1",
}


def test_settings_repr_and_public_status_do_not_disclose_credentials():
    settings = Settings.from_env(SECRETS)
    for output in (repr(settings), json.dumps(settings.public_status(), ensure_ascii=False)):
        for secret in SECRETS.values():
            assert secret not in output


def test_explicit_empty_mapping_does_not_inherit_process_keys(monkeypatch):
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    settings = Settings.from_env({})
    status = settings.public_status()
    assert status["molit"]["trade_key_configured"] is False
    assert status["molit"]["rent_key_configured"] is False
    assert status["zoro"]["configured"] is False


def test_shared_key_marks_both_services_configured_without_disclosing_it():
    settings = Settings.from_env({"MOLIT_API_KEY": SECRETS["MOLIT_API_KEY"]})
    status = settings.public_status()
    assert status["molit"]["trade_key_configured"] is True
    assert status["molit"]["rent_key_configured"] is True
    assert SECRETS["MOLIT_API_KEY"] not in json.dumps(status)


def test_blank_and_whitespace_keys_are_unconfigured():
    settings = Settings.from_env({
        "MOLIT_TRADE_API_KEY": "   ", "MOLIT_RENT_API_KEY": "", "ZORO_BASE_URL": "  ",
    })
    status = settings.public_status()
    assert status["molit"]["trade_key_configured"] is False
    assert status["molit"]["rent_key_configured"] is False
    assert status["zoro"]["configured"] is False


@pytest.mark.parametrize("value", ["0", "31", "NaN", "Infinity", "not-a-number"])
def test_timeout_configuration_rejects_unbounded_or_non_numeric_values(value):
    with pytest.raises(ValueError):
        Settings.from_env({"MOLIT_TIMEOUT_SECONDS": value})


@pytest.mark.parametrize("value", ["0", "65536", "not-a-port"])
def test_port_configuration_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        Settings.from_env({"APP_PORT": value})

