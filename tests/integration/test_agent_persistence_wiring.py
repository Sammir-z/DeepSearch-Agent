import unittest
from types import SimpleNamespace
from unittest.mock import patch


class AgentPersistenceWiringTests(unittest.TestCase):
    def test_build_main_agent_passes_shared_redis_components(self):
        from app.agent import main_agent as main_agent_module

        checkpointer = object()
        store = object()
        persistence = SimpleNamespace(checkpointer=checkpointer, store=store)

        with patch.object(main_agent_module, "create_deep_agent", return_value="graph") as build:
            result = main_agent_module.build_main_agent(persistence)

        self.assertEqual(result, "graph")
        self.assertIs(build.call_args.kwargs["checkpointer"], checkpointer)
        self.assertIs(build.call_args.kwargs["store"], store)
        self.assertEqual(build.call_args.kwargs["context_schema"].__name__, "AgentRuntimeContext")

    def test_main_agent_exposes_explicit_memory_tools(self):
        from app.agent import main_agent as main_agent_module

        persistence = SimpleNamespace(checkpointer=object(), store=object())
        with patch.object(main_agent_module, "create_deep_agent", return_value="graph") as build:
            main_agent_module.build_main_agent(persistence)

        tool_names = {tool.name for tool in build.call_args.kwargs["tools"]}
        self.assertTrue(
            {
                "remember_user_memory",
                "forget_user_memory",
                "search_user_memories",
            }.issubset(tool_names)
        )


if __name__ == "__main__":
    unittest.main()
