import unittest
from pathlib import Path

from search_shared.index_reader import read_snapshot
from search_shared.tokenizer import tokenize


class SharedFoundationTests(unittest.TestCase):
    def test_normalizes_case_width_and_combining_marks(self):
        self.assertEqual(tokenize("Ｈousing, Straße! ಕನ್ನಡ हिंदी"), ["housing", "strasse", "ಕನ್ನಡ", "हिंदी"])

    def test_fixture_postings_agree_with_tokenizer(self):
        root = Path(__file__).resolve().parents[1] / "fixtures" / "v1" / "index"
        manifest, segments = read_snapshot(root)
        self.assertEqual(manifest["passage_count"], 1)
        segment = segments[0]
        passage = segment["passages"][0]
        tokens = tokenize(passage["text"])
        self.assertEqual(passage["length"], len(tokens))
        for position, term in enumerate(tokens):
            self.assertEqual(segment["postings"][term], [{"passage_id": 1, "positions": [position]}])
