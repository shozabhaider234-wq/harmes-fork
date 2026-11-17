import sys
import unittest
from math import ceil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients" / "python"))
from context_builder import ContextBuilder
from hermes_bridge import _resolve_budget


class ContextBuilderTests(unittest.TestCase):
    def test_detail_budget_ceiling_and_fallback(self):
        self.assertEqual([_resolve_budget(d, {"safe_available_tokens": 5000, "token_budget": 5000})[0]
                          for d in ("brief", "normal", "deep", "exhaustive")], [300, 1000, 2000, 4000])
        self.assertEqual(_resolve_budget("exhaustive", {"safe_available_tokens": 700, "token_budget": 5000})[0], 700)
        self.assertEqual(_resolve_budget("deep", {"token_budget": 5000}, token_budget=600)[0], 600)
        self.assertEqual(_resolve_budget("brief", {"safe_available_tokens": 0})[0], 0)
        self.assertEqual(_resolve_budget("normal", {})[1], "fallback_without_prompt_occupancy")

    def test_live_capacity_releases_fallback_ceiling_and_respects_all_reserves(self):
        capacity = {"context_window": 12000, "prompt_tokens": 2000,
                    "assistant_output_tokens": 300, "reserved_output_tokens": 2000,
                    "memory_call_count": 1, "other_tool_call_count": 1}
        budget, reason = _resolve_budget("deep", {"safe_available_tokens": 1000,
            "hard_token_cap": 3500, "safety_margin_tokens": 500,
            "tool_result_wrapper_tokens": 100, "other_tool_result_reserve_tokens": 200},
            context_capacity=capacity)
        self.assertEqual(budget, 2000)
        self.assertEqual(reason, "usage_anchored_live_estimate")
        zero, _ = _resolve_budget("deep", {"hard_token_cap": 5000}, context_capacity={
            "context_window": 3000, "prompt_tokens": 2000, "assistant_output_tokens": 300,
            "reserved_output_tokens": 1000})
        self.assertEqual(zero, 0)

    def test_historical_retrieval_marks_old_record(self):
        rows = [{"id": "old", "summary": "Prior server location",
                 "entry": {"superseded_by": "new"}}]
        current = ContextBuilder(token_counter=len).build(rows, 300)
        historical = ContextBuilder(token_counter=len).build(rows, 300, include_superseded=True)
        self.assertEqual(current.selected_memory_ids, ())
        self.assertEqual(historical.selected_memory_ids, ("old",))
        self.assertIn('"superseded_by":"new"', historical.text)

    def test_rank_scores_are_not_treated_as_probabilities(self):
        rows = [{"id": "first", "summary": "Concise relevant fact", "score": 1 / 61,
                 "entry": {"importance": None, "confidence": None}},
                {"id": "second", "summary": "Another relevant fact", "score": 1 / 62}]
        package = ContextBuilder(token_counter=len, minimum_utility_per_token=0.001).build(rows, 300)
        self.assertEqual(package.selected_memory_ids, ("first", "second"))

    def test_default_counter_reuses_hermes_estimator_for_text_and_formatted_package(self):
        from agent.model_metadata import estimate_tokens_rough

        builder = ContextBuilder()
        samples = ["Unicode: 你好 café العربية", "def f(x): return {'x': x}",
                   '{"json": [1, true, null]}', "long " * 5000]
        for text in samples:
            self.assertEqual(builder._count(text), ceil(estimate_tokens_rough(text) * 1.25))
        package = builder.build([{"id": "memory-1", "summary": samples[1],
                                  "source_ref": "hermes://profile/sessions/a"}], 300)
        self.assertEqual(package.token_count, builder._count(package.text))
        self.assertLessEqual(package.token_count, package.token_budget)
        self.assertEqual(package.token_count_method, "hermes_rough_estimate_padded")

    def test_threshold_can_leave_budget_unused_and_records_reason(self):
        rows = [{"id": "useful", "summary": "Short", "utility": 1.0},
                {"id": "weak", "summary": "Weak candidate", "utility": 0.001}]
        package = ContextBuilder(token_counter=len, minimum_utility_per_token=0.01).build(rows, 500)
        self.assertEqual(package.selected_memory_ids, ("useful",))
        self.assertTrue(package.omission_reasons)
        self.assertLess(package.token_count, package.token_budget)

    def test_duplicates_unicode_and_budget(self):
        rows = [{"id": "a", "summary": "你好 🌱"}, {"id": "b", "summary": "  你好   🌱 "},
                {"id": "c", "summary": "x" * 8000}]
        package = ContextBuilder(token_counter=lambda text: len(text.encode("utf-8"))).build(rows, 180)
        self.assertLessEqual(package.token_count, 180)
        self.assertEqual(len(set(package.selected_memory_ids)), len(package.selected_memory_ids))
        self.assertEqual(package.selected_memory_ids[0], "a")

    def test_explicit_supersession_drops_only_marked_old_record(self):
        rows = [{"id": "old", "summary": "Old address", "entry": {"superseded_by": "new"}},
                {"id": "new", "summary": "Current address"},
                {"id": "historic", "summary": "Historical event from 1999"}]
        package = ContextBuilder(token_counter=len).build(rows, 500)
        self.assertNotIn("old", package.selected_memory_ids)
        self.assertIn("old:superseded_by:new", package.omission_reasons)
        self.assertIn("new", package.selected_memory_ids)
        self.assertIn("historic", package.selected_memory_ids)

    def test_unresolved_conflict_keeps_both_provenance_records(self):
        rows = [{"id": "fact-a", "summary": "The server runs in region east", "source_ref": "source-a"},
                {"id": "fact-b", "summary": "The server runs in region west", "source_ref": "source-b"}]
        package = ContextBuilder(token_counter=len).build(rows, 500)
        self.assertEqual(set(package.selected_memory_ids), {"fact-a", "fact-b"})
        self.assertEqual(set(package.provenance), {"fact-a@source-a", "fact-b@source-b"})

    def test_zero_budget_has_empty_result(self):
        package = ContextBuilder.empty_package(3)
        self.assertEqual((package.text, package.token_count, package.token_budget), ("", 0, 0))
        self.assertEqual(package.omitted_count, 3)


if __name__ == "__main__":
    unittest.main()
