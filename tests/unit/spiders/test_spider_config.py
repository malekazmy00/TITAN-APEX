"""Unit tests for src/spiders/spider_config.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.exceptions import ConfigError
from src.spiders.spider_config import load_spider_config

VALID_YAML = """
name: quotes_toscrape
start_urls:
  - "https://quotes.toscrape.com/"
allowed_domains:
  - "quotes.toscrape.com"
rate_limit: 1.0
selectors:
  item: "div.quote"
  fields:
    text: "span.text::text"
    author: "small.author::text"
next_page: "li.next a::attr(href)"
"""


def test_load_valid_config(tmp_path: Path) -> None:
    """Happy path: a well-formed YAML file loads into a validated SpiderConfig."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(VALID_YAML, encoding="utf-8")

    config = load_spider_config(str(config_file))

    assert config.name == "quotes_toscrape"
    assert config.start_urls == ["https://quotes.toscrape.com/"]
    assert config.selectors.item == "div.quote"
    assert config.selectors.fields["author"] == "small.author::text"
    assert config.next_page == "li.next a::attr(href)"
    assert config.render_js is False
    assert config.max_concurrency == 2
    assert config.antibot_needed is False


def test_render_js_and_max_concurrency_are_read_from_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "target.yaml"
    config_file.write_text(VALID_YAML + "\nrender_js: true\nmax_concurrency: 4\n", encoding="utf-8")

    config = load_spider_config(str(config_file))

    assert config.render_js is True
    assert config.max_concurrency == 4


def test_antibot_needed_is_read_from_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "target.yaml"
    config_file.write_text(VALID_YAML + "\nantibot_needed: true\n", encoding="utf-8")

    config = load_spider_config(str(config_file))

    assert config.antibot_needed is True


def test_non_positive_max_concurrency_raises_config_error(tmp_path: Path) -> None:
    """Failure case 5: max_concurrency must be a positive integer."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(VALID_YAML + "\nmax_concurrency: 0\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="failed schema validation"):
        load_spider_config(str(config_file))


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    """Failure case 1: a non-existent path raises ConfigError, not a raw OSError."""
    missing_path = tmp_path / "does_not_exist.yaml"

    with pytest.raises(ConfigError, match="not found"):
        load_spider_config(str(missing_path))


def test_malformed_yaml_raises_config_error(tmp_path: Path) -> None:
    """Failure case 2: syntactically broken YAML raises ConfigError."""
    config_file = tmp_path / "broken.yaml"
    config_file.write_text("name: [unterminated", encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid YAML"):
        load_spider_config(str(config_file))


def test_schema_violation_raises_config_error(tmp_path: Path) -> None:
    """Failure case 3: valid YAML that violates the schema also raises ConfigError."""
    config_file = tmp_path / "invalid_schema.yaml"
    config_file.write_text("name: missing_everything_else\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="failed schema validation"):
        load_spider_config(str(config_file))


def test_non_mapping_yaml_raises_config_error(tmp_path: Path) -> None:
    """Failure case 4: a YAML file that is a list, not a mapping, is rejected."""
    config_file = tmp_path / "list.yaml"
    config_file.write_text("- a\n- b\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="mapping"):
        load_spider_config(str(config_file))
