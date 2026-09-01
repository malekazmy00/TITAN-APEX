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
    assert config.extraction_mode == "parsed_html"
    assert config.warm_session_urls == []


def test_warm_session_urls_is_read_from_yaml(tmp_path: Path) -> None:
    """docs/REQUIREMENTS.md section 9 entry 21, Step 1: an ordered list,
    read as-is (no validation beyond "it's a list of strings" -- there's
    nothing structurally invalid about any particular URL list here, the
    same reasoning start_urls's own lack of extra validation already
    has)."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nwarm_session_urls:\n"
        '  - "https://quotes.toscrape.com/"\n'
        '  - "https://quotes.toscrape.com/category/1"\n',
        encoding="utf-8",
    )

    config = load_spider_config(str(config_file))

    assert config.warm_session_urls == [
        "https://quotes.toscrape.com/",
        "https://quotes.toscrape.com/category/1",
    ]


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


# --- extraction_mode: "live_dom" (docs/REQUIREMENTS.md section 9 entry
# 12 -- the real fix for entry 11's confirmed Shadow DOM gap) ------------


def test_live_dom_extraction_mode_is_read_from_yaml(tmp_path: Path) -> None:
    """Happy path: a valid live_dom config (html format, antibot_needed,
    a real-browser provider) loads with extraction_mode populated."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_needed: true\nantibot_provider: camoufox\n"
        "extraction_mode: live_dom\n",
        encoding="utf-8",
    )

    config = load_spider_config(str(config_file))

    assert config.extraction_mode == "live_dom"


def test_live_dom_extraction_mode_works_with_patchright_too(tmp_path: Path) -> None:
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_needed: true\nantibot_provider: patchright\n"
        "extraction_mode: live_dom\n",
        encoding="utf-8",
    )

    config = load_spider_config(str(config_file))

    assert config.extraction_mode == "live_dom"


def test_unknown_extraction_mode_raises_config_error(tmp_path: Path) -> None:
    """Failure case 9: a typo'd/unsupported extraction_mode value must be
    rejected at config-load time."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(VALID_YAML + "\nextraction_mode: raw_dom\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="failed schema validation"):
        load_spider_config(str(config_file))


def test_live_dom_extraction_mode_requires_antibot_needed(tmp_path: Path) -> None:
    """Failure case 10: no antibot_needed means no provider ever drives a
    live browser page at all -- live_dom would have nothing to extract from."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_provider: camoufox\nextraction_mode: live_dom\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="requires antibot_needed"):
        load_spider_config(str(config_file))


def test_live_dom_extraction_mode_requires_a_real_browser_provider(tmp_path: Path) -> None:
    """Failure case 11: byparr (the default antibot_provider) has no live
    browser page to query -- ByparrProvider.solve() only logs a warning and
    ignores extraction_selectors, so this must be rejected at config-load
    time instead of silently degrading to parsed_html at request time."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_needed: true\nextraction_mode: live_dom\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="requires antibot_provider"):
        load_spider_config(str(config_file))


def test_live_dom_extraction_mode_requires_html_response_format(tmp_path: Path) -> None:
    """Failure case 12: live_dom extraction reuses `selectors`, not
    `json_selectors` -- combining it with response_format: json is a real
    misconfiguration, not something to silently resolve."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        'name: x\nstart_urls: ["http://x/"]\nrate_limit: 1.0\n'
        "response_format: json\njson_selectors:\n  items_path: a\n  fields:\n    x: b\n"
        "antibot_needed: true\nantibot_provider: camoufox\nextraction_mode: live_dom\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="requires response_format"):
        load_spider_config(str(config_file))


# --- progressive_extraction (docs/REQUIREMENTS.md section 9 entry 14 --
# the real fix for entry 13's confirmed DOM Virtualization gap) ---------


def test_progressive_extraction_defaults_to_false(tmp_path: Path) -> None:
    config_file = tmp_path / "target.yaml"
    config_file.write_text(VALID_YAML, encoding="utf-8")

    config = load_spider_config(str(config_file))

    assert config.progressive_extraction is False


def test_progressive_extraction_is_read_from_yaml_with_parsed_html(tmp_path: Path) -> None:
    """Happy path: progressive_extraction works with the default
    extraction_mode ("parsed_html") -- independent of it, not requiring
    live_dom."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_needed: true\nantibot_provider: camoufox\n"
        "progressive_extraction: true\n",
        encoding="utf-8",
    )

    config = load_spider_config(str(config_file))

    assert config.progressive_extraction is True
    assert config.extraction_mode == "parsed_html"


def test_progressive_extraction_works_with_live_dom_too(tmp_path: Path) -> None:
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_needed: true\nantibot_provider: camoufox\n"
        "extraction_mode: live_dom\nprogressive_extraction: true\n",
        encoding="utf-8",
    )

    config = load_spider_config(str(config_file))

    assert config.progressive_extraction is True
    assert config.extraction_mode == "live_dom"


def test_progressive_extraction_requires_antibot_needed(tmp_path: Path) -> None:
    """Failure case 13: no antibot_needed means no provider ever drives a
    live browser page to scroll at all."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_provider: camoufox\nprogressive_extraction: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="requires antibot_needed"):
        load_spider_config(str(config_file))


def test_progressive_extraction_requires_a_real_browser_provider(tmp_path: Path) -> None:
    """Failure case 14: byparr (the default antibot_provider) has no live
    browser page to scroll and re-read step by step."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_needed: true\nprogressive_extraction: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="requires antibot_provider"):
        load_spider_config(str(config_file))


def test_progressive_extraction_requires_html_response_format(tmp_path: Path) -> None:
    """Failure case 15: progressive collection needs `selectors`
    (post-id-keyed dedup), not `json_selectors`."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        'name: x\nstart_urls: ["http://x/"]\nrate_limit: 1.0\n'
        "response_format: json\njson_selectors:\n  items_path: a\n  fields:\n    x: b\n"
        "antibot_needed: true\nantibot_provider: camoufox\nprogressive_extraction: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="requires response_format"):
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


LOGIN_YAML_BLOCK = (
    "\nlogin:\n"
    "  login_url: http://localhost:8080/login\n"
    "  username: titan_test_user\n"
    "  password: titan_test_pass\n"
    "  username_field: '#username'\n"
    "  password_field: '#password'\n"
    "  submit_selector: '#login-submit'\n"
)


def test_login_defaults_to_none(tmp_path: Path) -> None:
    config_file = tmp_path / "target.yaml"
    config_file.write_text(VALID_YAML, encoding="utf-8")

    config = load_spider_config(str(config_file))

    assert config.login is None


def test_login_is_read_from_yaml(tmp_path: Path) -> None:
    """Happy path: a full login block, with a real browser provider."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_needed: true\nantibot_provider: camoufox\n" + LOGIN_YAML_BLOCK,
        encoding="utf-8",
    )

    config = load_spider_config(str(config_file))

    assert config.login is not None
    assert config.login.login_url == "http://localhost:8080/login"
    assert config.login.username == "titan_test_user"
    assert config.login.password == "titan_test_pass"
    assert config.login.username_field == "#username"
    assert config.login.password_field == "#password"
    assert config.login.submit_selector == "#login-submit"
    assert config.login.session_expiry_probe_url is None


def test_login_session_expiry_probe_url_is_read_from_yaml(tmp_path: Path) -> None:
    """The test-only knob (docs/REQUIREMENTS.md section 9 entry 15) also
    round-trips through YAML."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML
        + "\nantibot_needed: true\nantibot_provider: camoufox\n"
        + LOGIN_YAML_BLOCK
        + "  session_expiry_probe_url: http://localhost:8080/test-expire-session\n",
        encoding="utf-8",
    )

    config = load_spider_config(str(config_file))

    assert config.login is not None
    assert config.login.session_expiry_probe_url == "http://localhost:8080/test-expire-session"


def test_login_requires_antibot_needed(tmp_path: Path) -> None:
    """Failure case: no antibot_needed means no provider ever drives a
    live browser page to fill/submit a real login form at all."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_provider: camoufox\n" + LOGIN_YAML_BLOCK,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="requires antibot_needed"):
        load_spider_config(str(config_file))


def test_login_requires_a_real_browser_provider(tmp_path: Path) -> None:
    """Failure case: byparr (the default antibot_provider) has no live
    browser page to fill/submit a real login form."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_needed: true\n" + LOGIN_YAML_BLOCK,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="requires antibot_provider"):
        load_spider_config(str(config_file))


# --- use_accumulated_profile (docs/REQUIREMENTS.md section 9 entry 21,
# Step 2) --------------------------------------------------------------


def test_use_accumulated_profile_defaults_to_false(tmp_path: Path) -> None:
    config_file = tmp_path / "target.yaml"
    config_file.write_text(VALID_YAML, encoding="utf-8")

    config = load_spider_config(str(config_file))

    assert config.use_accumulated_profile is False


def test_use_accumulated_profile_is_read_from_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_needed: true\nantibot_provider: camoufox\n"
        "use_accumulated_profile: true\n",
        encoding="utf-8",
    )

    config = load_spider_config(str(config_file))

    assert config.use_accumulated_profile is True


def test_use_accumulated_profile_requires_antibot_needed(tmp_path: Path) -> None:
    """Failure case: no antibot_needed means no provider ever drives a
    real browser context to load/save a profile into at all."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_provider: camoufox\nuse_accumulated_profile: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="requires antibot_needed"):
        load_spider_config(str(config_file))


def test_use_accumulated_profile_requires_a_real_browser_provider(tmp_path: Path) -> None:
    """Failure case: byparr (the default antibot_provider) has no
    browser context to load/save a profile into."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        VALID_YAML + "\nantibot_needed: true\nuse_accumulated_profile: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="requires antibot_provider"):
        load_spider_config(str(config_file))


# --- selectors.item_group_size (docs/REQUIREMENTS.md section 9 entry 23,
# Known Limitation #5's real fix) --------------------------------------


def test_item_group_size_is_read_from_yaml(tmp_path: Path) -> None:
    """Happy path: a config opting into positional extraction."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        'name: x\nstart_urls: ["http://x/"]\nrate_limit: 1.0\n'
        "selectors:\n  item: '#grid > *'\n  item_group_size: 3\n"
        "  fields:\n    title: '1::text'\n",
        encoding="utf-8",
    )

    config = load_spider_config(str(config_file))

    assert config.selectors.item_group_size == 3


def test_item_group_size_defaults_to_none(tmp_path: Path) -> None:
    """Happy path: every existing config's exact prior behavior, unset."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(VALID_YAML, encoding="utf-8")

    config = load_spider_config(str(config_file))

    assert config.selectors.item_group_size is None


def test_item_group_size_rejects_a_non_positive_value(tmp_path: Path) -> None:
    """Failure case: a zero/negative group size is meaningless."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        'name: x\nstart_urls: ["http://x/"]\nrate_limit: 1.0\n'
        "selectors:\n  item: '#grid > *'\n  item_group_size: 0\n"
        "  fields:\n    title: '0::text'\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="failed schema validation"):
        load_spider_config(str(config_file))


def test_item_group_size_rejects_live_dom_extraction_mode(tmp_path: Path) -> None:
    """Failure case: positional extraction is only implemented for the
    default 'parsed_html' path so far -- combining it with live_dom must
    fail loudly at config-load time, not silently misread `item`/`fields`
    the wrong way at request time."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        'name: x\nstart_urls: ["http://x/"]\nrate_limit: 1.0\n'
        "antibot_needed: true\nantibot_provider: camoufox\nextraction_mode: live_dom\n"
        "selectors:\n  item: '#grid > *'\n  item_group_size: 3\n"
        "  fields:\n    title: '1::text'\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="not yet supported with extraction_mode"):
        load_spider_config(str(config_file))


def test_item_group_size_rejects_progressive_extraction(tmp_path: Path) -> None:
    """Failure case: same reasoning, for progressive_extraction: true."""
    config_file = tmp_path / "target.yaml"
    config_file.write_text(
        'name: x\nstart_urls: ["http://x/"]\nrate_limit: 1.0\n'
        "antibot_needed: true\nantibot_provider: camoufox\nprogressive_extraction: true\n"
        "selectors:\n  item: '#grid > *'\n  item_group_size: 3\n"
        "  fields:\n    title: '1::text'\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="not yet supported with progressive_extraction"):
        load_spider_config(str(config_file))
