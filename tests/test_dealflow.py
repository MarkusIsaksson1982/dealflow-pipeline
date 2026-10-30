import json, sqlite3, tempfile, unittest
from pathlib import Path
from dealflow.core import Deal, RulesExtractor, dedupe, digest, redact, run, score, validate

class TestDealflow(unittest.TestCase):
    def setUp(self):
        self.d = Deal("A", "climate", "Seed", "Germany", "€1m", "a@example.com")

    def test_rules_extracts_labels(self):
        x = RulesExtractor().extract("Company: A\nSector: climate\nStage: Seed\nCountry: Germany\nAsk: €1m\nContact: a@example.com")
        self.assertEqual(x["name"], "A"); self.assertEqual(x["contact_email"], "a@example.com")

    def test_extractor_missing_is_blank(self): self.assertEqual(RulesExtractor().extract("hello")["name"], "")
    def test_validation_accepts_complete(self):
        x = self.d.__dict__.copy(); x["confidence"] = 1.0
        self.assertTrue(validate(x)[0])
    def test_validation_reports_missing(self):
        ok, why = validate({"name":"A"}); self.assertFalse(ok); self.assertIn("sector", why)
    def test_validation_rejects_bad_email(self):
        x = self.d.__dict__.copy(); x["contact_email"] = "not-an-email"; self.assertFalse(validate(x)[0])
    def test_dedupe_by_name_and_domain(self):
        other = Deal("A", "health", "Seed", "France", "x", "b@example.com")
        self.assertEqual(len(dedupe([self.d, other])), 1)
    def test_dedupe_keeps_different_domains(self):
        self.assertEqual(len(dedupe([self.d, Deal("A", "x", "Seed", "France", "x", "b@other.example")])), 2)
    def test_score_full_match(self): self.assertEqual(score(self.d)[0], 100)
    def test_score_explains_mismatch(self):
        p, why = score(Deal("X", "retail", "Series B", "USA", "x", "x@x.example"))
        self.assertEqual(p, 0); self.assertTrue(any("outside thesis" in x["reason"] for x in why)); self.assertTrue(any("outside Europe" in x["reason"] for x in why))
    def test_redaction_email_phone(self):
        self.assertEqual(redact("a@x.example +49 171 1234567"), "[redacted email] [redacted phone]")
    def test_run_queues_invalid_and_dedupes(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.json"; src.write_text(json.dumps([{"body":"Company: A\nSector: climate\nStage: Seed\nCountry: Germany\nAsk: x\nContact: a@x.example"}, {"body":"Company: A\nSector: climate\nStage: Seed\nCountry: Germany\nAsk: x\nContact: b@x.example"}, {"body":"Company: Bad\nSector: climate"}]))
            db, md = Path(td)/"x.db", Path(td)/"x.md"
            n, r = run(td, str(db), str(md)); self.assertEqual((n, r), (1, 1)); self.assertTrue(md.exists())
            c = sqlite3.connect(db); self.assertEqual(c.execute("select count(*) from deals").fetchone()[0], 1)
            self.assertEqual(c.execute("select count(*) from reviews").fetchone()[0], 1)
            self.assertNotIn("@x.example", md.read_text())
            run(td, str(db), str(md)); self.assertEqual(c.execute("select count(*) from reviews").fetchone()[0], 1)

    def test_low_confidence_is_reviewed(self):
        class Low:
            def extract(self, text):
                return {f: "x" for f in ("name", "sector", "stage", "country", "ask")} | {"contact_email": "x@example.com", "confidence": .2}
        self.assertEqual(validate(Low().extract("")), (False, "low confidence extraction"))
    def test_sqlite_digest_has_no_email(self):
        c = sqlite3.connect(":memory:"); c.execute("CREATE TABLE deals(name,sector,stage,country,ask,score,reasons)")
        c.execute("INSERT INTO deals VALUES(?,?,?,?,?,?,?)", ("A", "climate", "Seed", "Germany", "contact a@x.example", 1, "ok")); c.execute("CREATE TABLE reviews(source,reason,raw)")
        c.execute("INSERT INTO reviews VALUES(?,?,?)", ("x", "bad", "phone +46 701 123456")); c.commit()
        self.assertNotIn("@x.example", digest(c)); self.assertIn("Human review queue: 1", digest(c))
    def test_custom_thesis_changes_score(self):
        self.assertEqual(score(self.d, {"impact_areas": ["health"], "stages": [], "region": "Europe"})[0], 30)

class TestReviewFixes(unittest.TestCase):
    def test_confidence_must_be_finite_in_range(self):
        base = {f: "x" for f in ("name", "sector", "stage", "country", "ask")} | {"contact_email": "a@x.example"}
        for c in (float("nan"), 1.5, True, -1): self.assertFalse(validate(base | {"confidence": c})[0])
        self.assertTrue(validate(base | {"confidence": .9})[0])
    def test_contact_field_wins_over_other_emails(self):
        x = RulesExtractor().extract("Forwarded by intern@fund.example\nCompany: A\nSector: climate\nStage: Seed\nCountry: Germany\nAsk: x\nContact: a@a.example")
        self.assertEqual(x["contact_email"], "a@a.example")
    def test_amounts_survive_and_phones_redacted(self):
        self.assertEqual(redact("€2 500 000"), "€2 500 000"); self.assertEqual(redact("089/12345678"), "[redacted phone]")
    def test_impact_is_word_match(self):
        self.assertEqual(score(Deal("A", "retail", "Seed", "Germany", "x", "a@a.example"), {"impact_areas": ["ai"], "stages": [], "region": "Europe"})[0], 30)
    def test_bad_file_and_rerun_update(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "bad.json").write_text("{"); (Path(td) / "big.json").write_text(json.dumps([{"body": "x" * 20000}, {"body": None}]))
            good = Path(td) / "in.json"; good.write_text(json.dumps([{"body": "Company: A\nSector: retail\nStage: Seed\nCountry: Germany\nAsk: x\nContact: a@x.example"}]))
            db, md = Path(td) / "o.db", Path(td) / "o.md"
            self.assertEqual(run(td, db, md), (1, 3))
            good.write_text(good.read_text().replace("retail", "climate")); run(td, db, md)
            c = sqlite3.connect(db); self.assertEqual(c.execute("SELECT count(*), max(score) FROM deals").fetchone(), (1, 100)); c.close()

    def test_round3_validate_non_dict_and_freemail_dedupe(self):
        self.assertEqual(validate(None), (False, "extractor returned no record"))
        a = Deal("Same", "climate", "Seed", "Germany", "€1m", "x@gmail.com"); b = Deal("Same", "health", "Seed", "France", "€2m", "y@gmail.com")
        c = Deal("Same", "climate", "Seed", "Germany", "€1m", "z@same.example"); d = Deal("Same", "climate", "Seed", "Germany", "€1m", "w@same.example")
        self.assertEqual(len(dedupe([a, b])), 2); self.assertEqual(len(dedupe([c, d])), 1)
    def test_round3_scoring_europe_and_multiword_areas(self):
        pts, why = score(Deal("P", "Digital health-tech", "Series A", "Portugal", "€3m", "p@p.example")); self.assertEqual(pts, 100)
        self.assertEqual(score(Deal("I", "ocean biodiversity", "seed", "Ireland", "€1m", "i@i.example"))[0], 100)
        t = {"impact_areas": ["circular economy"], "stages": ["pre-seed"], "region": "Europe"}
        pts, why = score(Deal("C", "Circular economy logistics", "Pre-Seed", "Poland", "€1m", "c@c.example"), t); self.assertEqual(pts, 100); self.assertIn("pre-seed", why[1]["reason"])
    def test_round3_phone_variants_redacted(self):
        for v in ["+ 49 151 2345678", "(0)30 1234567", "+46 70 123 45 67"]: self.assertEqual(redact("call " + v), "call [redacted phone]")
        self.assertEqual(redact("Ask: €2 500 000"), "Ask: €2 500 000")
    def test_round3_bad_txt_and_bad_record_go_to_review_and_digest_is_weekly(self):
        class Boom(RulesExtractor):
            def extract(self, text):
                if "boom" in text: raise ValueError("x")
                return super().extract(text)
        with tempfile.TemporaryDirectory() as td:
            Path(td, "bad.txt").write_bytes(b"\xff\xfe\x00bad"); Path(td, "boom.txt").write_text("boom")
            Path(td, "ok.txt").write_text("Company: A\nSector: climate\nStage: Seed\nCountry: Germany\nAsk: €1m\nContact: a@a.example")
            db = str(Path(td, "d.db")); md = str(Path(td, "d.md"))
            self.assertEqual(run(td, db, md), (1, 2))
            c = sqlite3.connect(db); c.execute("UPDATE deals SET week='2000-W01'"); c.execute("INSERT INTO deals(name,domain,score,reasons,week) VALUES('Old','old.example',10,'[]','2000-W01')"); c.commit()
            self.assertNotIn("Old", digest(c, "2026-W01")); self.assertIn("Old", digest(c, "2000-W01")); c.close()

if __name__ == "__main__": unittest.main()
