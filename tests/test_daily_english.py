import importlib.util
import random
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "daily_english.py"
SPEC = importlib.util.spec_from_file_location("daily_english", SCRIPT)
daily = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = daily
SPEC.loader.exec_module(daily)


class DailyEnglishTests(unittest.TestCase):
    def test_parse_words_deduplicates_and_splits_slashes(self):
        words = daily.parse_words()
        terms = [item["word"] for item in words]
        self.assertGreaterEqual(len(terms), 50)
        self.assertEqual(len(terms), len(set(terms)))
        self.assertIn("furthermore", terms)
        self.assertIn("moreover", terms)
        self.assertEqual(terms.count("potential"), 1)

    def test_story_selection_has_no_overlap_until_exhaustion(self):
        entries = [{"word": f"word{i}", "translation": "释义"} for i in range(30)]
        state = {"word_epoch": 1, "used_story_words": [], "last_story_words": []}
        first = daily.select_story_words(entries, state, random.Random(1))
        second = daily.select_story_words(entries, state, random.Random(2))
        self.assertFalse({x["word"] for x in first} & {x["word"] for x in second})

    def test_new_epoch_still_excludes_previous_story(self):
        entries = [{"word": f"word{i}", "translation": "释义"} for i in range(25)]
        state = {"word_epoch": 1, "used_story_words": [], "last_story_words": []}
        daily.select_story_words(entries, state, random.Random(1))
        second = daily.select_story_words(entries, state, random.Random(2))
        third = daily.select_story_words(entries, state, random.Random(3))
        self.assertFalse({x["word"] for x in second} & {x["word"] for x in third})
        self.assertEqual(state["word_epoch"], 2)

    def test_question_duplicate_detection(self):
        words = [{"word": f"word{i}", "translation": "释义"} for i in range(10)]
        questions = [
            {
                "word": f"word{i}",
                "question": f"Question {i}",
                "options": ["one", "two", "three", "four"],
                "answer": "A",
                "explanation": "说明",
            }
            for i in range(10)
        ]
        fingerprints = daily.validate_questions(questions, words, set())
        with self.assertRaisesRegex(ValueError, "相邻"):
            daily.validate_questions(questions, words, set(fingerprints))

    def test_sunday_skips_without_api_key(self):
        state = {"version": 1, "runs": {}}
        self.assertIsNone(daily.generate_for_date(daily.date(2026, 6, 21), state))


if __name__ == "__main__":
    unittest.main()
