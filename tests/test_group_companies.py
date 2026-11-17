import json
import unittest

from group_companies import consolidate


def detail(code, full, ban="01234567", name="公司", grade="L"):
    return {"ccc": code, "ccc_full": full, "ban": ban, "cname": name,
            "name": "品項", "direction": "I", "year": "115", "period": "115 01-08",
            "status": "ok", "import_grade": grade, "queried_at": "2026-11-17T10:00:00+08:00",
            "source_url": "https://example.com", "error": ""}


class CompanyTests(unittest.TestCase):
    def test_aggregates_by_ban_and_keeps_exact_source_rows(self):
        rows = [detail("01062020", "01062020111"), detail("01062010", "01062010114")]
        result, skipped = consolidate(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(skipped, 0)
        self.assertEqual(result[0]["company_display"], "公司（01062010、01062020）")
        self.assertEqual(result[0]["import_grade"], "L")
        self.assertEqual(json.loads(result[0]["source_records"]), rows)
        self.assertEqual(len(json.loads(result[0]["ccc_details"])), 2)

    def test_same_name_different_ban_stays_separate_and_sorts_by_ccc(self):
        rows = [detail("01069000", "01069000905", "12345678"),
                detail("01062010", "01062010114", "87654321"),
                detail("01062010", "01062010115", "01234567")]
        result, _ = consolidate(rows)
        self.assertEqual([r["ban"] for r in result], ["01234567", "87654321", "12345678"])

    def test_missing_company_stays_out_and_grade_conflicts_are_preserved(self):
        rows = [detail("01062010", "01062010114"),
                detail("01062020", "01062020111", grade="K"), {"ban": "", "status": "no_firms"}]
        result, skipped = consolidate(rows)
        self.assertEqual(skipped, 1)
        self.assertEqual(result[0]["status"], "needs_review")
        self.assertEqual(json.loads(result[0]["field_variants"])["import_grade"], ["K", "L"])

    def test_rejects_other_year_or_export(self):
        for change in ({"year": "114"}, {"direction": "E"}):
            with self.assertRaises(ValueError):
                consolidate([{**detail("01062010", "01062010114"), **change}])
