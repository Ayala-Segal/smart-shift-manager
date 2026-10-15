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

    def test_a_copied_reply_keeps_the_rule_text(self):
        fallback = _fallback()
        text, used = rewrite_rationale(fallback, _hour_facts(), generate=lambda fallback, facts: fallback)
        self.assertEqual(text, fallback)
        self.assertFalse(used)

    def test_a_reply_that_invents_an_hours_breach_is_rejected(self):
        fallback = _fallback()
        reply = (
            "Approved. Projected hours exceed the weekly limit. "
            "Approved hours 10, projected hours 18, shift hours 8, weekly limit 42."
        )
        text, used = rewrite_rationale(fallback, _hour_facts(), generate=lambda fallback, facts: reply)
        self.assertEqual(text, fallback)
        self.assertFalse(used)

    def test_a_reply_missing_hour_numbers_is_rejected(self):
        fallback = _fallback()
        reply = "Approved. The week stays within the limit."
        text, used = rewrite_rationale(fallback, _hour_facts(), generate=lambda fallback, facts: reply)
        self.assertEqual(text, fallback)
        self.assertFalse(used)

    def test_a_reply_within_the_limit_is_accepted(self):
        fallback = _fallback()
        reply = (
            "Approved. The week stays within the limit and does not exceed it. "
            "Approved hours 10, projected hours 18, shift hours 8, weekly limit 42."
        )
        text, used = rewrite_rationale(fallback, _hour_facts(), generate=lambda fallback, facts: reply)
        self.assertTrue(used)
        self.assertEqual(text, reply)
        self.assertLessEqual(len(text), 500)

    def test_a_second_attempt_is_used_when_the_first_fails(self):
        fallback = _fallback()
        reply = (
            "Approved. The week stays within the limit. "
            "Approved hours 10, projected hours 18, shift hours 8, weekly limit 42."
        )
        calls = []

        def generate(fallback, facts):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError("ollama is slow")
            return reply

        text, used = rewrite_rationale(fallback, _hour_facts(), generate=generate)
        self.assertEqual(calls, [1, 1])
        self.assertTrue(used)
        self.assertEqual(text, reply)

    def test_a_rejected_reply_is_retried_once(self):
        fallback = _fallback()
        reply = (
            "Approved. The week stays within the limit. "
            "Approved hours 10, projected hours 18, shift hours 8, weekly limit 42."
        )
        calls = []

        def generate(fallback, facts):
            calls.append(1)
            if len(calls) == 1:
                return fallback
            return reply

        text, used = rewrite_rationale(fallback, _hour_facts(), generate=generate)
        self.assertEqual(calls, [1, 1])
        self.assertTrue(used)
        self.assertEqual(text, reply)

    def test_two_failed_attempts_keep_the_rule_text(self):
        fallback = _fallback()
        calls = []

        def generate(fallback, facts):
            calls.append(1)
            raise TimeoutError("ollama is down")

        text, used = rewrite_rationale(fallback, _hour_facts(), generate=generate)
        self.assertEqual(calls, [1, 1])
        self.assertEqual(text, fallback)
        self.assertFalse(used)


def _fallback():
    return "Approved. Approved hours 10, projected hours 18, shift hours 8, weekly limit 42."


def _hour_facts():
    return {
        "decision": "approve",
        "approved_hours": 10,
        "projected_hours": 18,
        "shift_hours": 8,
        "weekly_limit": 42,
        "exceeds_limit": False,
    }


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
