"""Config surface: payload shape, derived cap, key masking."""

from __future__ import annotations

import pytest
from tools.load.config import LoadConfig, build_parser, config_from_args


def test_payload_is_the_minimal_2captcha_body() -> None:
    cfg = LoadConfig(client_key="k" * 8, captcha_type="text_captcha")
    body = cfg.payload()
    assert body["clientKey"] == "k" * 8
    assert body["task"] == {
        "type": "text_captcha",
        "websiteKey": "load-test-key",
        "websiteURL": "https://example.com",
    }


def test_in_flight_cap_defaults_to_the_offered_rate() -> None:
    assert LoadConfig(rate=1000.0).in_flight_cap == 1000
    assert LoadConfig(rate=10.0).in_flight_cap == 64
    assert LoadConfig(rate=1000.0, in_flight_max=7).in_flight_cap == 7


def test_as_dict_never_echoes_the_client_key() -> None:
    cfg = LoadConfig(client_key="super-secret-key", rate=5.0)
    dumped = cfg.as_dict()
    assert dumped["client_key"] == "***"
    assert dumped["in_flight_cap"] == 64


def test_cli_requires_a_key_and_positive_numbers() -> None:
    parser = build_parser()
    args = parser.parse_args(["--rate", "10", "--duration", "1"])
    with pytest.raises(SystemExit):
        config_from_args(args, parser)

    args = parser.parse_args(["--key", "abcdefgh", "--rate", "0"])
    with pytest.raises(SystemExit):
        config_from_args(args, parser)


def test_cli_maps_flags_onto_the_dataclass() -> None:
    parser = build_parser()
    args = parser.parse_args(["--key", "abcdefgh", "--rate", "250", "--type", "text_captcha", "--json", "out.json"])
    cfg = config_from_args(args, parser)
    assert cfg.client_key == "abcdefgh"
    assert cfg.rate == 250.0
    assert cfg.report_json == "out.json"
