import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.memory.history import (
    ChatHistoryTurn,
    HistoryStorageUnavailableError,
    extract_chat_history,
    load_thread_history,
)


class ChatHistoryExtractionTests(unittest.IsolatedAsyncioTestCase):
    def test_extracts_user_prompt_and_latest_assistant_text(self):
        state = SimpleNamespace(
            values={
                "messages": [
                    HumanMessage(content="第一问\n【工作环境指令】\n内部路径"),
                    AIMessage(content="中间工具调用", tool_calls=[{"name": "task", "args": {}, "id": "1", "type": "tool_call"}]),
                    ToolMessage(content="工具结果", tool_call_id="1"),
                    AIMessage(content="第一问的最终回答", id="assistant-1"),
                ]
            }
        )

        turns = extract_chat_history(state, fallback_timestamp="2026-09-06T10:00:00")

        self.assertEqual(
            turns,
            [
                ChatHistoryTurn(
                    id="turn-0",
                    user_content="第一问",
                    assistant_content="第一问的最终回答",
                    timestamp="2026-09-06T10:00:00",
                )
            ],
        )

    def test_preserves_multiple_turns_and_incomplete_user_turn(self):
        state = {
            "messages": [
                {"type": "human", "content": "问题一", "id": "human-1"},
                {"type": "ai", "content": [{"type": "text", "text": "回答一"}]},
                {"role": "user", "content": "问题二"},
                {"role": "assistant", "content": "回答二"},
                {"type": "human", "content": "尚未回答的问题"},
            ]
        }

        turns = extract_chat_history(state)

        self.assertEqual([turn.user_content for turn in turns], ["问题一", "问题二", "尚未回答的问题"])
        self.assertEqual([turn.assistant_content for turn in turns], ["回答一", "回答二", ""])

    def test_ignores_empty_or_non_text_assistant_messages(self):
        state = {
            "messages": [
                {"type": "human", "content": "问题"},
                {"type": "ai", "content": [{"type": "tool_use", "name": "task"}]},
                {"type": "tool", "content": "内部工具结果"},
            ]
        }

        turns = extract_chat_history(state)

        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].assistant_content, "")

    def test_empty_checkpoint_returns_no_turns(self):
        self.assertEqual(extract_chat_history({"values": {"messages": []}}), [])

    def test_accepts_tuple_messages_from_a_checkpoint_snapshot(self):
        state = {
            "values": {
                "messages": (
                    {"type": "human", "content": "问题"},
                    {"type": "ai", "content": "回答"},
                )
            }
        }

        self.assertEqual(extract_chat_history(state)[0].assistant_content, "回答")

    async def test_checkpoint_read_failure_is_wrapped(self):
        agent = SimpleNamespace(
            aget_state=AsyncMock(side_effect=ConnectionError("redis down"))
        )

        with self.assertRaises(HistoryStorageUnavailableError):
            await load_thread_history(agent, "thread-1")

    async def test_checkpoint_read_uses_async_agent_state(self):
        agent = SimpleNamespace(
            aget_state=AsyncMock(
                return_value={
                    "messages": [
                        {"type": "human", "content": "问题"},
                        {"type": "ai", "content": "回答"},
                    ]
                }
            )
        )

        turns = await load_thread_history(agent, "thread-1")

        agent.aget_state.assert_awaited_once_with(
            {"configurable": {"thread_id": "thread-1"}}
        )
        self.assertEqual(turns[0].assistant_content, "回答")


if __name__ == "__main__":
    unittest.main()
