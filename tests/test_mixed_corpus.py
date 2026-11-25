"""Mixed corpus coverage without requiring the local dataset."""

from hashlib import sha256

from db.e3.mixed import describe_article, discover
from engines.e1.reference import file_source, parse_source
from engines.e1.source_cleaning import clean_article, clean_snapshot_page


def test_article_dedup_preserves_regions_and_uses_original_name(tmp_path):
    raw = tmp_path / "raw"
    articles = raw / "scheme_articles"
    for scope in ("assam", "karnataka"):
        (articles / scope / "nested").mkdir(parents=True)
    (raw / "scheme_pdfs").mkdir()
    payload = "Education Scholarship\n\nApply online for assistance.\nSAVE AS PDF".encode()
    (articles / "assam" / "nested" / "source copy.txt").write_bytes(payload)
    original = articles / "karnataka" / "nested" / "source.txt"
    original.write_bytes(payload)
    (raw / "scheme_pdfs" / "test").touch()

    (item,), skipped = discover(raw.resolve())
    assert item["paths"][0].endswith("source.txt")
    assert item["regions"] == ["Assam", "Karnataka"]
    assert item["sha256"] == sha256(payload).hexdigest()
    assert skipped == [{"path": "scheme_pdfs/test", "reason": "UNSUPPORTED_FILE"}]
    assert discover(raw.resolve())[0][0]["source_id"] == item["source_id"]

    stored = tmp_path / "article.txt"
    stored.write_bytes(describe_article(item, payload))
    source = file_source(dict(doc_id=item["source_id"], job_id=item["source_id"],
                              source_type="SCHEME_ARTICLE", file_path=stored.name), tmp_path)
    records = []
    report = parse_source(source, 32, records.append)
    assert report["status"] == "INDEXED"
    assert [record[:2] for record in records] == [(None, "Education Scholarship"), (None, "Apply online for assistance.")]


def test_cleanup_preserves_instructions_and_repairs_encoding():
    article = "\ufeffSAVE AS PDF\n\n(adsbygoogle=window.adsbygoogle||[]).push({});\nApply for \u00e2\u201a\u00b91000. \u0c95\u0ca8\u0ccd\u0ca8\u0ca1"
    assert clean_article(article) == "Apply for \u20b91000. \u0c95\u0ca8\u0ccd\u0ca8\u0ca1"
    pdf = ("GrantAre you sure you want to sign out?CancelSign OutFeedbackSomething went wrong. "
           "Please try again later.OkCancelApply NowCheck EligibilityAssamGrant\n"
           "BenefitsApply online. Submit the signed form.")
    cleaned = clean_snapshot_page(pdf)
    assert "sign out" not in cleaned
    assert "Something went wrong" not in cleaned
    assert "Apply online. Submit the signed form." in cleaned
