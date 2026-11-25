import unittest
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from search_shared.index_reader import encode_passage_id, passage_id_value, read_snapshot
from search_shared.tokenizer import tokenize, tokenize_with_spans

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class SharedFoundationTests(unittest.TestCase):
    def test_normalizes_case_width_and_combining_marks(self):
        self.assertEqual(tokenize("Ｈousing, Straße! ಕನ್ನಡ हिंदी"), ["housing", "strasse", "ಕನ್ನಡ", "हिंदी"])

    def test_fixture_postings_agree_with_tokenizer(self):
        root = FIXTURES / "v2" / "index"
        manifest, segments = read_snapshot(root)
        self.assertEqual(manifest["stats"], {"N": 1, "total_tokens": 3, "avgdl": 3})
        segment = segments[0]
        passage = segment["passages"][0]
        tokens = tokenize(passage["text"])
        self.assertEqual(passage["length"], len(tokens))
        for position, term in enumerate(tokens):
            self.assertEqual(segment["postings"][term], [{"passage_id": 1, "positions": [position]}])
        self.assertEqual(
            tokenize_with_spans(passage["text"]),
            list(zip(tokens, passage["token_spans"], strict=True)),
        )

    def test_source_spans_survive_normalization_and_astral_characters(self):
        sample = "🏠 Cafe\u0301 Straße \u1100\u1161"
        self.assertEqual(
            tokenize_with_spans(sample),
            [
                ("café", {"start": 3, "end": 8}),
                ("strasse", {"start": 9, "end": 15}),
                ("가", {"start": 16, "end": 18}),
            ],
        )

    def test_unsupported_version_and_bad_checksum_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "schema version"):
            read_snapshot(FIXTURES / "v1" / "index")
        with TemporaryDirectory() as location:
            root = Path(location) / "index"
            shutil.copytree(FIXTURES / "v2" / "index", root)
            (root / "segment-0001.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                read_snapshot(root)

    def test_passage_id_must_have_canonical_encoding(self):
        self.assertEqual(encode_passage_id(2**53), "9007199254740992")
        self.assertEqual(encode_passage_id(2**53 - 1), 2**53 - 1)
        self.assertEqual(passage_id_value("9007199254740992"), 2**53)
        self.assertEqual(passage_id_value(2**53 - 1), 2**53 - 1)
        for invalid in (True, 0, 2**53, "42", "09007199254740992"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                passage_id_value(invalid)
