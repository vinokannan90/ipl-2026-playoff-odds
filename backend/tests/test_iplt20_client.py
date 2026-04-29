"""Tests for the JSONP stripper."""

from iplodds.data.iplt20_client import _strip_jsonp


def test_strip_jsonp_basic():
    assert _strip_jsonp('cb({"a":1})') == '{"a":1}'


def test_strip_jsonp_with_semicolon_and_whitespace():
    assert _strip_jsonp('  cb(  {"a":1}  );  ') == '{"a":1}'


def test_strip_jsonp_passthrough_raw_json():
    assert _strip_jsonp('{"a":1}') == '{"a":1}'
