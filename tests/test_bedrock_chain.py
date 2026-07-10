"""Tests for bedrock_chain: LlmClient Protocol, success/failure propagation."""

from __future__ import annotations

from unittest.mock import MagicMock

from anki_deck_generator.llm.bedrock_chain import (
    _BedrockLlmClient,
    extract_vocabulary_from_chunk,
    translate_simplified_terms,
)
from anki_deck_generator.llm.fixture_player import FixtureLlmModel
from anki_deck_generator.llm.schemas import LlmVocabularyItem


class TestBedrockLlmClientSuccessPropagation:
    """Verify _BedrockLlmClient propagates actual success/failure flags (Item 1 + 5)."""

    def test_vocabulary_success_returns_true(self) -> None:
        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.content = '{"cards":[{"simplified":"你好","meaning":"hello"}]}'
        mock_model.invoke.return_value = mock_result
        client = _BedrockLlmClient(mock_model)
        cards, success = client.vocabulary_for_chunk("some text")
        assert success is True

    def test_vocabulary_invoke_failure_returns_false(self) -> None:
        mock_model = MagicMock()
        mock_model.invoke.side_effect = RuntimeError("network error")
        client = _BedrockLlmClient(mock_model)
        cards, success = client.vocabulary_for_chunk("some text")
        assert success is False
        assert cards == []

    def test_vocabulary_parse_failure_returns_false(self) -> None:
        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "not json at all {{{"
        mock_model.invoke.return_value = mock_result
        client = _BedrockLlmClient(mock_model)
        cards, success = client.vocabulary_for_chunk("some text")
        assert success is False
        assert cards == []

    def test_translate_success_returns_true(self) -> None:
        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.content = '{"translations":[{"simplified":"你好","english":"hello"}]}'
        mock_model.invoke.return_value = mock_result
        client = _BedrockLlmClient(mock_model)
        translations, success = client.translate_terms(["你好"])
        assert success is True

    def test_translate_invoke_failure_returns_false(self) -> None:
        mock_model = MagicMock()
        mock_model.invoke.side_effect = RuntimeError("network error")
        client = _BedrockLlmClient(mock_model)
        translations, success = client.translate_terms(["你好"])
        assert success is False
        assert translations == {}

    def test_translate_parse_failure_returns_false(self) -> None:
        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "garbage"
        mock_model.invoke.return_value = mock_result
        client = _BedrockLlmClient(mock_model)
        translations, success = client.translate_terms(["你好"])
        assert success is False
        assert translations == {}


class TestExtractVocabularyFromChunk:
    """Verify the public wrapper propagates success flags."""

    def test_wrapped_success_propagation(self) -> None:
        mock_client = MagicMock()
        mock_client.vocabulary_for_chunk.return_value = ([LlmVocabularyItem(simplified="a", meaning="b")], True)
        cards, success = extract_vocabulary_from_chunk(mock_client, "text")
        assert success is True

    def test_wrapped_failure_propagation(self) -> None:
        mock_client = MagicMock()
        mock_client.vocabulary_for_chunk.return_value = ([], False)
        cards, success = extract_vocabulary_from_chunk(mock_client, "text")
        assert success is False
        assert cards == []


class TestTranslateSimplifiedTerms:
    """Verify the public wrapper propagates success flags."""

    def test_wrapped_success_propagation(self) -> None:
        mock_client = MagicMock()
        mock_client.translate_terms.return_value = ({"a": "b"}, True)
        translations, success = translate_simplified_terms(mock_client, ["a"])
        assert success is True

    def test_wrapped_failure_propagation(self) -> None:
        mock_client = MagicMock()
        mock_client.translate_terms.return_value = ({}, False)
        translations, success = translate_simplified_terms(mock_client, ["a"])
        assert success is False
        assert translations == {}


class TestFixtureLlmModelFailureFlag:
    """Verify FixtureLlmModel returns success=True (deterministic fixtures)."""

    def test_fixture_always_success(self, tmp_path) -> None:
        import hashlib
        key1 = hashlib.sha256("chunk alpha".encode("utf-8")).hexdigest()
        key2 = hashlib.sha256("chunk beta".encode("utf-8")).hexdigest()
        fixture = tmp_path / "fixture.json"
        fixture.write_text(
            f'{{"chunks":{{"{key1}":[],"{key2}":[{{"simplified":"test","meaning":"test"}}]}},"translations":{{}}}}',
            encoding="utf-8",
        )
        model = FixtureLlmModel.from_path(fixture)
        cards, success = model.vocabulary_for_chunk("chunk alpha")
        assert success is True
        assert cards == []

        cards, success = model.vocabulary_for_chunk("chunk beta")
        assert success is True
        assert len(cards) == 1
