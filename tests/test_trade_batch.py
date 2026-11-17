import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from trade_batch import (is_complete, job_key, read_input, read_previous,
                         records_for_code, run_batch, write_output)


def job(full="01012100003", name="馬，純種繁殖用"):
    return {"ccc": full[:8], "ccc_full": full, "name": name}


def empty_record():
    return {"direction": "I", "query_total": 0, "status": "no_firms", "error": ""}


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        patcher = patch("trade_batch.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("builtins.print")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_two_columns_preserve_eleven_digit_code_and_chinese(self):
        source = self.root / "input.csv"
        source.write_text("ccc,name\n01029090009,其他牛\n", encoding="utf-8-sig")
        self.assertEqual(read_input(source), [job("01029090009", "其他牛")])

    def test_rejects_extra_columns_and_lost_leading_zero(self):
        source = self.root / "input.csv"
        for content in ("ccc,name,ccc_full\n01029090009,牛,01029090009\n",
                        "ccc,name\n1029090009,牛\n", "ccc,name\n01029090,牛\n"):
            source.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                read_input(source)

    def test_quoted_comma_in_name(self):
        source = self.root / "input.csv"
        source.write_text('ccc,name\n01012100003,"馬,繁殖用"\n', encoding="utf-8")
        self.assertEqual(read_input(source)[0]["name"], "馬,繁殖用")

    def test_query_error_is_saved_and_next_code_still_runs(self):
        output = self.root / "output.csv"
        jobs = [job(), job("01012900005", "其他馬")]
        with patch("trade_batch.records_for_code", side_effect=[RuntimeError("驗證失敗"), [empty_record()]]):
            self.assertEqual(run_batch(jobs, output, Mock(), Mock()), 1)
        groups = read_previous(output)
        self.assertEqual(groups[job_key(jobs[0])][0]["status"], "query_error")
        self.assertEqual(groups[job_key(jobs[0])][0]["error"], "驗證失敗")
        self.assertTrue(is_complete(groups[job_key(jobs[1])]))
        self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_resume_only_requeries_failed_items(self):
        output = self.root / "output.csv"
        jobs = [job(), job("01012900005", "其他馬")]
        write_output(output, [{**jobs[0], **empty_record()},
                              {**jobs[1], "direction": "I", "status": "query_error", "error": "失敗"}])
        with patch("trade_batch.records_for_code", return_value=[empty_record()]) as query:
            self.assertEqual(run_batch(jobs, output, Mock(), Mock(), resume=True), 0)
        self.assertEqual(query.call_count, 1)
        self.assertEqual(query.call_args.args[1], "01012900")
        with output.open(encoding="utf-8-sig", newline="") as source:
            self.assertEqual(len(list(csv.DictReader(source))), 2)

    def test_same_eight_digit_code_queries_once_but_preserves_full_codes(self):
        output = self.root / "output.csv"
        jobs = [job(), job("01012100004", "同前綴測試")]
        with patch("trade_batch.records_for_code", return_value=[empty_record()]) as query:
            self.assertEqual(run_batch(jobs, output, Mock(), Mock()), 0)
        self.assertEqual(query.call_count, 1)
        self.assertEqual(set(read_previous(output)), {job_key(item) for item in jobs})

    def test_checkpoint_survives_interruption(self):
        output = self.root / "output.csv"
        jobs = [job(), job("01012900005", "其他馬")]
        with patch("trade_batch.records_for_code", side_effect=[[empty_record()], KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):
                run_batch(jobs, output, Mock(), Mock())
        self.assertEqual(set(read_previous(output)), {job_key(jobs[0])})

    def test_company_details_cached_across_codes_and_partial_year_kept(self):
        firm = {"ban": "01234567", "cname": "公司", "ename": "Company", "boss": "代表人"}
        basic = [[None] * 11]
        basic[0][6], basic[0][8] = "地址", "02-12345678"
        grades = [["01234567", "115 01-08", "公司", "Company", "I", "M", "115"],
                  ["01234567", "114 01-12", "公司", "Company", "J", "M", "114"]]
        cache = {}
        with patch("trade_batch.query_firms", return_value=(1, 1, [firm], "token")), \
                patch("trade_batch.pop", side_effect=[basic, grades]) as details:
            first = records_for_code(Mock(), "01012100", Mock(), cache, 5)
            second = records_for_code(Mock(), "01012900", Mock(), cache, 5)
        self.assertEqual(details.call_count, 2)
        self.assertEqual((len(first), len(second)), (1, 1))
        self.assertEqual(first[0]["period"], "115 01-08")
        self.assertEqual(second[0]["import_grade"], "I")
        self.assertEqual(first[0]["fax"], "")
        self.assertTrue(is_complete(first))

    def test_missing_115_grade_keeps_company_without_substituting_another_year(self):
        firm = {"ban": "01234567", "cname": "公司", "ename": "Company", "boss": "代表人"}
        older = [["01234567", "114 01-12", "公司", "Company", "J", "M", "114"]]
        with patch("trade_batch.query_firms", return_value=(1, 1, [firm], "token")), \
                patch("trade_batch.pop", side_effect=[[], older]):
            records = records_for_code(Mock(), "01012100", Mock(), {}, 5)
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]["ban"], records[0]["year"], records[0]["status"]),
                         ("01234567", "115", "no_grades"))
        self.assertNotIn("import_grade", records[0])
        self.assertTrue(is_complete(records))

    def test_older_year_output_cannot_be_skipped_on_resume(self):
        row = {"direction": "I", "status": "ok", "query_total": 1,
               "ban": "01234567", "year": "114", "error": ""}
        self.assertFalse(is_complete([row]))
        self.assertFalse(is_complete([row, {**row, "year": "115"}]))
        self.assertTrue(is_complete([{**row, "year": "115"}]))

    def test_incomplete_company_count_cannot_be_skipped_on_resume(self):
        self.assertFalse(is_complete([{"direction": "I", "status": "ok", "query_total": 2,
                                       "ban": "01234567", "error": ""}]))


if __name__ == "__main__":
    unittest.main()
