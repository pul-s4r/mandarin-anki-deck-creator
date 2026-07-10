"""Tests for bedrock_chain: LlmClient Protocol, success/failure propagation, retry logic."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError
from langchain_core.messages import HumanMessage

from anki_deck_generator.llm.bedrock_chain import (
    _BedrockLlmClient,
    _is_transient_error,
    _retry_invoke,
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


_TEST_URL = "https://bedrock-runtime.us-east-1.amazonaws.com"


class TestRetryLogic:
    """Item 2: retry wrapper distinguishes transient from permanent errors."""

    def test_is_transient_timeout(self) -> None:
        assert _is_transient_error(ConnectTimeoutError(endpoint_url=_TEST_URL)) is True

    def test_is_transient_read_timeout(self) -> None:
        assert _is_transient_error(ReadTimeoutError(endpoint_url=_TEST_URL)) is True

    def test_is_transient_endpoint_connection(self) -> None:
        assert _is_transient_error(EndpointConnectionError(endpoint_url=_TEST_URL)) is True

    def test_is_transient_throttle(self) -> None:
        exc = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "throttled"}},
            "InvokeModel",
        )
        assert _is_transient_error(exc) is True

    def test_is_transient_server_error(self) -> None:
        exc = ClientError(
            {"Error": {"Code": "InternalServerException", "Message": "oops"}},
            "InvokeModel",
        )
        assert _is_transient_error(exc) is True

    def test_is_not_transient_runtime_error(self) -> None:
        assert _is_transient_error(RuntimeError("bad json")) is False

    def test_is_not_transient_validation_error(self) -> None:
        assert _is_transient_error(ValueError("parse fail")) is False

    def test_is_not_transient_non_retryable_client_error(self) -> None:
        exc = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "invalid"}},
            "InvokeModel",
        )
        assert _is_transient_error(exc) is False

    def test_retry_succeeds_on_second_attempt(self) -> None:
        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.content = '{"cards":[{"simplified":"你好","meaning":"hello"}]}'
        mock_model.invoke.side_effect = [
            ConnectTimeoutError(endpoint_url=_TEST_URL),
            mock_result,
        ]
        client = _BedrockLlmClient(mock_model)
        cards, success = client.vocabulary_for_chunk("test text")
        assert success is True
        assert len(cards) == 1
        assert mock_model.invoke.call_count == 2

    def test_retry_exhausted_raises_from_retry_invoke(self) -> None:
        mock_model = MagicMock()
        mock_model.invoke.side_effect = [
            ConnectTimeoutError(endpoint_url=_TEST_URL),
            ConnectTimeoutError(endpoint_url=_TEST_URL),
            ConnectTimeoutError(endpoint_url=_TEST_URL),
        ]
        with pytest.raises(ConnectTimeoutError):
            _retry_invoke(mock_model, [HumanMessage(content="x")], max_attempts=3, delay=0.01)
        assert mock_model.invoke.call_count == 3

    def test_retry_exhausted_in_vocabulary_returns_failure(self) -> None:
        mock_model = MagicMock()
        mock_model.invoke.side_effect = [
            ConnectTimeoutError(endpoint_url=_TEST_URL),
            ConnectTimeoutError(endpoint_url=_TEST_URL),
            ConnectTimeoutError(endpoint_url=_TEST_URL),
        ]
        client = _BedrockLlmClient(mock_model)
        cards, success = client.vocabulary_for_chunk("test text")
        assert success is False
        assert cards == []
        assert mock_model.invoke.call_count == 3

    def test_no_retry_on_non_transient_error(self) -> None:
        mock_model = MagicMock()
        mock_model.invoke.side_effect = ValueError("bad response")
        client = _BedrockLlmClient(mock_model)
        cards, success = client.vocabulary_for_chunk("test text")
        assert success is False
        assert cards == []
        assert mock_model.invoke.call_count == 1

    def test_retry_with_custom_settings(self) -> None:
        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.content = '{"cards":[{"simplified":"test","meaning":"test"}]}'
        mock_model.invoke.side_effect = [
            ConnectTimeoutError(endpoint_url=_TEST_URL),
            ReadTimeoutError(endpoint_url=_TEST_URL),
            mock_result,
        ]
        client = _BedrockLlmClient(mock_model, retry_max_attempts=3, retry_delay=0.01)
        cards, success = client.vocabulary_for_chunk("text")
        assert success is True
        assert mock_model.invoke.call_count == 3

    def test_translate_retries_on_transient_error(self) -> None:
        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.content = '{"translations":[{"simplified":"你好","english":"hello"}]}'
        mock_model.invoke.side_effect = [
            ConnectTimeoutError(endpoint_url=_TEST_URL),
            mock_result,
        ]
        client = _BedrockLlmClient(mock_model)
        translations, success = client.translate_terms(["你好"])
        assert success is True
        assert mock_model.invoke.call_count == 2
