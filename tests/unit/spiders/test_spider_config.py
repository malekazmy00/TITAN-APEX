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
    assert config.render_wait_ms is None
    assert config.click_selector is None
    assert config.antibot_provider == "byparr"


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


def test_render_wait_ms_and_click_selector_are_read_from_yaml(tmp_path: Path) -> None:
    """render_wait_ms/click_selector (docs/REQUIREMENTS.md section 7, entries 3-4)
    are config-only knobs -- no spider code changes needed to set them."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nrender_wait_ms: 2500\nclick_selector: \"button.load-more\"\n",
        encoding="utf-8",
    )

    config = load_spider_config(str(config_file))

    assert config.render_wait_ms == 2500
    assert config.click_selector == "button.load-more"


def test_non_positive_render_wait_ms_raises_config_error(tmp_path: Path) -> None:
    """Failure case 6: a zero or negative render_wait_ms is meaningless (there is
    nothing to wait for) and must be rejected, not silently treated as 'no wait'."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(VALID_YAML + "\nrender_wait_ms: 0\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="failed schema validation"):
        load_spider_config(str(config_file))


def test_antibot_provider_is_read_from_yaml(tmp_path: Path) -> None:
    """antibot_provider (docs/REQUIREMENTS.md section 9 entry 4 / round 3)
    is a config-only knob -- a target picks camoufox with no spider code
    changes."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(VALID_YAML + "\nantibot_provider: camoufox\n", encoding="utf-8")

    config = load_spider_config(str(config_file))

    assert config.antibot_provider == "camoufox"


def test_patchright_antibot_provider_is_read_from_yaml(tmp_path: Path) -> None:
    """Same as the camoufox case above, for the third selectable provider
    (this phase's revision) -- lighter than Camoufox, still config-only."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(VALID_YAML + "\nantibot_provider: patchright\n", encoding="utf-8")

    config = load_spider_config(str(config_file))

    assert config.antibot_provider == "patchright"


def test_unknown_antibot_provider_raises_config_error(tmp_path: Path) -> None:
    """Failure case 7: a typo'd/unsupported provider name must be rejected
    at config-load time, not silently accepted and fall back at request time."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(VALID_YAML + "\nantibot_provider: playwright\n", encoding="utf-8")

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


JSON_YAML = """
name: mock_target_feed
start_urls:
  - "http://localhost:8080/api/feed"
allowed_domains:
  - "localhost"
rate_limit: 1.0
response_format: json
json_selectors:
  items_path: "edges"
  fields:
    post_id: "post.id"
    author: "post.author"
    text: "post.text"
    likes: "post.likes"
  next_cursor_path: "page_info.end_cursor"
  has_next_page_path: "page_info.has_next_page"
"""


def test_json_response_format_is_read_from_yaml(tmp_path: Path) -> None:
    """Happy path (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's JSON/API
    round): a json-format config loads with json_selectors populated and
    selectors left unset."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(JSON_YAML, encoding="utf-8")

    config = load_spider_config(str(config_file))

    assert config.response_format == "json"
    assert config.selectors is None
    assert config.json_selectors is not None
    assert config.json_selectors.items_path == "edges"
    assert config.json_selectors.fields["author"] == "post.author"
    assert config.json_selectors.next_cursor_path == "page_info.end_cursor"
    assert config.json_selectors.has_next_page_path == "page_info.has_next_page"


def test_html_format_without_selectors_raises_config_error(tmp_path: Path) -> None:
    """Failure case 5: response_format defaults to "html", which requires
    `selectors` -- omitting it must be rejected at load time, not produce
    a spider that crashes on the first real request."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        'name: x\nstart_urls: ["http://x/"]\nrate_limit: 1.0\n', encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="failed schema validation"):
        load_spider_config(str(config_file))


def test_json_format_without_json_selectors_raises_config_error(tmp_path: Path) -> None:
    """Failure case 6: response_format: json requires json_selectors."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        'name: x\nstart_urls: ["http://x/"]\nrate_limit: 1.0\nresponse_format: json\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="failed schema validation"):
        load_spider_config(str(config_file))


def test_html_format_with_json_selectors_also_set_raises_config_error(tmp_path: Path) -> None:
    """Failure case 7: setting both selector blocks for an "html" config is
    a real misconfiguration (which one actually applies?), not silently
    resolved by picking one."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\njson_selectors:\n  items_path: a\n  fields:\n    x: b\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="failed schema validation"):
        load_spider_config(str(config_file))


def test_json_format_with_selectors_also_set_raises_config_error(tmp_path: Path) -> None:
    """Failure case 8: the mirror image of the above for a "json" config."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        JSON_YAML + '\nselectors:\n  item: "div"\n  fields:\n    x: "span::text"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="failed schema validation"):
        load_spider_config(str(config_file))
