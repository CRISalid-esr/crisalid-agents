"""Tests for the SorboBot OpenWebUI pipeline adapter."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from openwebui_pipelines.sorbobot_pipeline import to_langchain_messages


def test_to_langchain_messages_converts_known_roles():
    messages = [
        {"role": "system", "content": "Tu es SorboBot."},
        {"role": "user", "content": "Quels sont les experts en NLP ?"},
        {"role": "assistant", "content": "Voici les experts..."},
    ]

    result = to_langchain_messages(messages)

    assert [type(m) for m in result] == [SystemMessage, HumanMessage, AIMessage]
    assert result[1].content == "Quels sont les experts en NLP ?"


def test_to_langchain_messages_skips_unknown_role():
    messages = [
        {"role": "tool", "content": "ignored"},
        {"role": "user", "content": "kept"},
    ]

    result = to_langchain_messages(messages)

    assert len(result) == 1
    assert isinstance(result[0], HumanMessage)


def test_to_langchain_messages_defaults_missing_content_to_empty_string():
    result = to_langchain_messages([{"role": "user"}])

    assert result[0].content == ""


def test_to_langchain_messages_empty_input_returns_empty_list():
    assert to_langchain_messages([]) == []
