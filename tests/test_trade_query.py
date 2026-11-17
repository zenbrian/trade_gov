import io
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import requests

from captcha_ocr import CaptchaOCR
from trade_query import captcha_ok, display, form_fields, parse_result, query_firms


def result_page(bans, total, pages=1, state="queryAllSuccess"):
    rows = "".join(
        '<tr><td>1</td><td><a onclick="kdbase_showPopBasic(\'%s\')">公司</a></td>'
        '<td>Company</td><td>代表人</td></tr>' % ban for ban in bans
    )
    return (
        "<input name='state' value='%s'><input name='verifySHidden' value='token'>"
        "共 %s 筆 共 %s 頁 <table>%s</table>" % (state, total, pages, rows)
    )


def response(text="", content=b"image"):
    result = Mock()
    result.text = text
    result.content = content
    return result


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch("trade_query.time.sleep").start()
        self.addCleanup(patch.stopall)
        patch("builtins.print").start()
        self.ocr = Mock()
        self.ocr.candidates.return_value = [("1234", 0.9)]
        self.session = Mock()
        self.session.get.side_effect = [
            response("<input name='verifySHidden' value='token'>"), response()
        ]

    def test_full_pagination_preserves_query_and_checks_unique_count(self):
        self.session.post.side_effect = [response(""),
            response(result_page(["12345678"], 2, 2)),
            response(result_page(["87654321"], 2, 2))]
        total, pages, firms, token = query_firms(self.session, "0101", self.ocr)
        self.assertEqual((total, pages, len(firms), token), (2, 2, 2, "token"))
        self.assertEqual(self.session.post.call_args_list[1].kwargs["data"]["q_ieType"], "I")
        posted = self.session.post.call_args.kwargs["data"]
        self.assertEqual((posted["state"], posted["currentPage"], posted["q_ccc"],
                          posted["q_ieType"]), ("queryPage", "2", "0101", "I"))

    def test_repeated_page_is_an_error(self):
        self.session.post.side_effect = [response(""),
            response(result_page(["12345678"], 2, 2)),
            response(result_page(["12345678"], 2, 2))]
        with self.assertRaisesRegex(RuntimeError, "重複統編"):
            query_firms(self.session, "0101", self.ocr)

    def test_missing_firms_is_not_success(self):
        self.session.post.side_effect = [response(""), response(result_page(["12345678"], 2))]
        with self.assertRaisesRegex(RuntimeError, "名單不完整"):
            query_firms(self.session, "0101", self.ocr)

    def test_failed_page_is_not_a_completed_partial_list(self):
        self.session.post.side_effect = [response(""),
            response(result_page(["12345678"], 2, 2)),
            response(result_page(["87654321"], 2, 2, state="init"))]
        with self.assertRaisesRegex(RuntimeError, "第 2 頁未成功"):
            query_firms(self.session, "0101", self.ocr)

    def test_failed_captcha_is_bounded_and_never_prompts(self):
        self.session.get.side_effect = [response(""), response(), response()]
        self.session.post.return_value = response("驗證錯誤")
        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            with self.assertRaisesRegex(RuntimeError, "2 次內未通過"):
                query_firms(self.session, "0101", self.ocr, attempts=2)
        self.assertEqual(self.ocr.candidates.call_count, 2)
        self.assertEqual(self.session.post.call_count, 2)

    def test_empty_ocr_does_not_submit_guesses(self):
        self.ocr.candidates.return_value = []
        with self.assertRaisesRegex(RuntimeError, "未通過"):
            query_firms(self.session, "0101", self.ocr, attempts=1)
        self.session.post.assert_not_called()

    def test_http_error_cannot_pass_as_blank_captcha_response(self):
        self.session.post.return_value = response("")
        self.session.post.return_value.raise_for_status.side_effect = requests.HTTPError("503")
        with self.assertRaises(requests.HTTPError):
            captcha_ok(self.session, "1234")

    def test_zero_result_is_explicit(self):
        self.session.post.side_effect = [response(""), response(result_page([], 0))]
        self.assertEqual(query_firms(self.session, "0101", self.ocr)[:3], (0, 1, []))

    def test_live_empty_result_does_not_retry_captcha(self):
        page = ("<input name='state' value='queryAllError'>"
                "<input name='queryAllFlag' value='true'>"
                "<script>showErrorMsg('找不到查詢資料，請重新輸入查詢條件!');</script>")
        self.session.post.side_effect = [response(""), response(page)]
        self.assertEqual(query_firms(self.session, "0101", self.ocr)[:3], (0, 1, []))
        self.assertEqual(self.ocr.candidates.call_count, 1)

    def test_other_query_error_is_not_an_empty_result(self):
        page = ("<input name='state' value='queryAllError'>"
                "<input name='queryAllFlag' value='true'>"
                "<script>showErrorMsg('驗證碼錯誤');</script>")
        self.session.post.side_effect = [response(""), response(page)]
        with self.assertRaisesRegex(RuntimeError, "未通過"):
            query_firms(self.session, "0101", self.ocr, attempts=1)


class ParsingTests(unittest.TestCase):
    def test_fields_support_quotes_and_entities(self):
        self.assertEqual(form_fields("<input value='a&amp;b' name='x'>"
                                     '<input name="state" value="queryAllSuccess">'
                                     "<input type='radio' name='r' value='E'>"),
                         {"x": "a&b", "state": "queryAllSuccess"})

    def test_formatted_count_and_missing_values(self):
        ok, total, pages, _ = parse_result(result_page([], "1,234", 124))
        self.assertEqual((ok, total, pages), (True, 1234, 124))
        self.assertEqual(display(None), "")

    def test_live_pagination_state_requires_confirmed_results(self):
        page = result_page(["12345678"], 56, 6, state="queryPage")
        self.assertFalse(parse_result(page)[0])
        self.assertTrue(parse_result(page + "<input name='queryAllFlag' value='true'>")[0])

    def test_ocr_candidates_preserve_case_and_reject_invalid_strings(self):
        # Exercise the real result adapter/preprocessing without loading the model.
        ocr = CaptchaOCR.__new__(CaptchaOCR)
        ocr.model = Mock()
        ocr.model.predict.return_value = [
            Mock(json={"res": {"rec_text": "aB12", "rec_score": 0.8}}),
            Mock(json={"res": {"rec_text": " aB12 ", "rec_score": 0.9}}),
            Mock(json={"res": {"rec_text": "?123", "rec_score": 0.99}}),
        ]
        buf = io.BytesIO()
        Image.new("RGB", (100, 40), "black").save(buf, format="PNG")
        self.assertEqual(ocr.candidates(buf.getvalue()), [("aB12", 0.9)])

    def test_short_recognized_code_is_not_discarded(self):
        ocr = CaptchaOCR.__new__(CaptchaOCR)
        ocr.model = Mock()
        ocr.model.predict.return_value = [Mock(json={"res": {"rec_text": "3 76", "rec_score": 0.8}})]
        buf = io.BytesIO()
        Image.new("RGB", (100, 40), "black").save(buf, format="PNG")
        self.assertEqual(ocr.candidates(buf.getvalue()), [("376", 0.8)])


if __name__ == "__main__":
    unittest.main()
