import json
import unittest

from app.agent.ollama import rewrite_rationale


class OllamaRationaleTests(unittest.TestCase):
    def test_a_failed_model_keeps_the_rule_text(self):
        def fail(fallback, facts):
            raise TimeoutError("ollama is down")

        text, used = rewrite_rationale("Approved. Approved hours 0.", {"decision": "approve"}, generate=fail)
        self.assertEqual(text, "Approved. Approved hours 0.")
        self.assertFalse(used)

    def test_an_empty_model_reply_keeps_the_rule_text(self):
        text, used = rewrite_rationale("Approved.", {"decision": "approve"}, generate=lambda fallback, facts: "  ")
        self.assertEqual(text, "Approved.")
        self.assertFalse(used)

    def test_a_model_reply_replaces_the_wording_only(self):
        def generate(fallback, facts):
            self.assertEqual(facts["decision"], "approve")
            self.assertIn("Approved hours", fallback)
            return "Approved. The week stays within the limit. Approved hours 0, projected hours 8."

        text, used = rewrite_rationale(
            "Approved. Approved hours 0, projected hours 8.",
            {"decision": "approve"},
            generate=generate,
        )
        self.assertTrue(used)
        self.assertIn("within the limit", text)
        self.assertLessEqual(len(text), 500)

    def test_a_long_model_reply_is_cut_to_500_characters(self):
        text, used = rewrite_rationale("short", {"decision": "warning"}, generate=lambda fallback, facts: "word " * 200)
        self.assertTrue(used)
        self.assertEqual(len(text), 500)
        self.assertTrue(text.endswith("..."))


class OllamaCycleTests(unittest.TestCase):
    def test_the_cycle_stores_the_model_wording_and_the_rule_decision(self):
        from tests.test_agent_evaluation import AgentEvaluationTests

        case = AgentEvaluationTests()
        case.setUp()
        try:
            case._request(case.employee_id, "cashier")

            def narrate(fallback, facts):
                self.assertEqual(facts["decision"], "approve")
                return "Model wording. Approved hours 0.", True

            case._cycle(narrate=narrate)
            payload = case._payload(case._recommendation())
            self.assertEqual(payload["decision"], "approve")
            self.assertEqual(payload["rationale"], "Model wording. Approved hours 0.")
            ollama = next(item for item in payload["sources"] if item["name"] == "ollama")
            self.assertTrue(ollama["ok"])
            self.assertIsInstance(json.dumps(payload["sources"]), str)
        finally:
            case.tearDown()
