"""貿易署出進口廠商查詢，使用本機 PaddleOCR 自動辨識驗證碼。

用法：uv run python scripts/trade_query.py 08045021
省略號列時只詢問號列；固定只查進口。結果只輸出到終端。
"""
import argparse
import html
from html.parser import HTMLParser
from pathlib import Path
import re
import sys
import time

import requests

BASE = "https://fbfh.trade.gov.tw/fb"
QUERY_URL = BASE + "/web/queryBasicf.do"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")
DELAY = 1.0  # 每次請求之間的間隔（秒）
TARGET_YEAR = "115"  # 民國年；固定只輸出此年度的實績

GRADES = {
    "A": ">=10", "B": ">=9;<10", "C": ">=8;<9", "D": ">=7;<8",
    "E": ">=6;<7", "F": ">=5;<6", "G": ">=4;<5", "H": ">=3;<4",
    "I": ">=2;<3", "J": ">=1;<2", "K": ">=0.5;<1", "L": ">0;<0.5", "M": "=0",
}


class InputParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag != "input" or not attrs.get("name") or "disabled" in attrs:
            return
        if attrs.get("type", "text").lower() in ("radio", "button", "submit", "reset", "file", "checkbox"):
            return
        self.fields[attrs["name"]] = attrs.get("value") or ""


def form_fields(page):
    """讀出 input 的 name/value，支援單引號、雙引號及 HTML entities。"""
    parser = InputParser()
    parser.feed(page)
    return parser.fields


def display(value):
    return "" if value is None else re.sub(r"\s+", " ", str(value)).strip()


def grades_for_year(rows):
    if any(len(row) < 7 for row in rows):
        raise RuntimeError("廠商年度級距的欄位不完整。")
    return [row for row in rows if display(row[6]) == TARGET_YEAR]


def clean(cell):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()


def parse_result(page):
    """回傳 (是否為結果頁, 總筆數, 總頁數, 廠商列表)。"""
    fields = form_fields(page)
    text = clean(page)
    total = re.search(r"共\s*([\d,]+)\s*筆", text)
    pages = re.search(r"共\s*([\d,]+)\s*頁", text)
    # Live pagination keeps state=queryPage; it does not emit queryPageSuccess.
    ok = fields.get("state") in ("queryAllSuccess", "queryPageSuccess") or (
        fields.get("state") == "queryPage" and fields.get("queryAllFlag") == "true"
        and total is not None
    )
    firms = []
    for row in re.findall(r"<tr.*?</tr>", page, flags=re.S | re.I):
        ban = re.search(r"kdbase_showPopBasic\('(\d+)'\)", row)
        if not ban:
            continue
        cells = [clean(c) for c in re.findall(r"<td.*?</td>", row, flags=re.S | re.I)]
        firms.append({
            "ban": ban.group(1),
            "cname": cells[1] if len(cells) > 1 else "",
            "ename": cells[2] if len(cells) > 2 else "",
            "boss": cells[3] if len(cells) > 3 else "",
        })
    # The live site reports an empty search as queryAllError with this message.
    if (fields.get("state") == "queryAllError" and fields.get("queryAllFlag") == "true"
            and not firms and re.search(r"showErrorMsg\(['\"]找不到查詢資料", page)):
        return True, 0, 1, []
    return (ok, int(total.group(1).replace(",", "")) if total else None,
            int(pages.group(1).replace(",", "")) if pages else 1, firms)


def response_page(response):
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def captcha_ok(s, code):
    """和網頁一樣先打預檢；回應空白代表通過。"""
    r = s.post(BASE + "/ajax/jsonChkReal.jsp", params={"p": code}, data={"q": "undefined"},
               headers={"X-Requested-With": "XMLHttpRequest"}, timeout=30)
    return not response_page(r).strip()


def pop(s, action, ban, token):
    r = s.post("%s/common/%s.action" % (BASE, action), json={"banNo": ban, "verifySHidden": token},
               headers={"X-Requested-With": "XMLHttpRequest",
                        "Accept": "application/json, text/javascript, */*; q=0.01"}, timeout=30)
    response_page(r)
    data = r.json()
    if not isinstance(data, dict) or "retrieveDataList" not in data:
        raise RuntimeError("%s 回應格式異常，無法確認廠商明細。" % action)
    rows = data["retrieveDataList"]
    if rows is not None and (not isinstance(rows, list) or
                             any(not isinstance(row, list) for row in rows)):
        raise RuntimeError("%s 回應資料格式異常。" % action)
    return rows or []


def check_firms(firms, seen, page_number):
    for firm in firms:
        if firm["ban"] in seen:
            raise RuntimeError("第 %d 頁出現重複統編 %s，停止以避免輸出錯誤名單。" %
                               (page_number, firm["ban"]))
        seen.add(firm["ban"])


def query_firms(s, ccc, ocr, attempts=5):
    page = response_page(s.get(QUERY_URL, timeout=30))
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(DELAY * 2)
        image = s.get(BASE + "/image.do", timeout=30)
        image.raise_for_status()
        candidates = ocr.candidates(image.content)
        print("驗證碼自動辨識：第 %d/%d 次" % (attempt, attempts), flush=True)
        code = None
        # Only submit strings actually recognized from this image, at most three.
        for candidate, _confidence in candidates[:3]:
            time.sleep(DELAY)
            if captcha_ok(s, candidate):
                code = candidate
                break
        if code is None:
            continue
        fields = form_fields(page)
        fields.update({"state": "queryAll", "q_ieType": "I", "q_ccc": ccc,
                       "verifyCode": code, "verifyCodeHidden": code, "querySubmit": ""})
        page = response_page(s.post(QUERY_URL, data=fields, timeout=60))
        ok, total, pages, firms = parse_result(page)
        if ok:
            break
        print("網站尚未接受查詢，將重新取得驗證碼。", flush=True)
    else:
        raise RuntimeError("自動驗證在 %d 次內未通過；此次未取得查詢結果，請稍後重試。" % attempts)

    if total is None:
        raise RuntimeError("結果頁缺少總筆數，無法確認名單是否完整。")
    seen = set()
    check_firms(firms, seen, 1)
    for n in range(2, pages + 1):
        time.sleep(DELAY)
        nxt = {**fields, **form_fields(page)}
        nxt.update({"state": "queryPage", "currentPage": str(n), "q_ieType": "I", "q_ccc": ccc})
        page = response_page(s.post(QUERY_URL, data=nxt, timeout=60))
        ok, next_total, next_pages, more = parse_result(page)
        if not ok or not more:
            raise RuntimeError("第 %d 頁未成功，名單不完整；此次不輸出完成結果。" % n)
        if next_total != total or next_pages != pages:
            raise RuntimeError("翻頁期間總筆數或頁數變動，請重新查詢。")
        returned_page = form_fields(page).get("currentPage")
        if returned_page is not None and returned_page != str(n):
            raise RuntimeError("要求第 %d 頁，但網站回傳第 %s 頁。" % (n, returned_page))
        check_firms(more, seen, n)
        firms.extend(more)
    if len(firms) != total:
        raise RuntimeError("網站顯示 %d 家，實際取得 %d 家；名單不完整。" % (total, len(firms)))
    token = form_fields(page).get("verifySHidden", "")
    if firms and not token:
        raise RuntimeError("結果頁缺少廠商明細驗證欄位。")
    return total, pages, firms, token


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="輸入 CCC 號列，自動辨識驗證碼並列出進口廠商與 115 年級距")
    parser.add_argument("ccc", nargs="?", help="4 碼或 8 碼 CCC 號列")
    parser.add_argument("--captcha-attempts", type=int, default=5, help="自動驗證次數，1–10（預設 5）")
    parser.add_argument("--model-dir", type=Path, help="已下載的 PaddleOCR 模型目錄")
    args = parser.parse_args()
    if not 1 <= args.captcha_attempts <= 10:
        parser.error("--captcha-attempts 必須介於 1 與 10。")
    ccc = args.ccc or input("進口貨品號列（4 碼或 8 碼）：").strip()
    if not re.fullmatch(r"[0-9]{4}|[0-9]{8}", ccc):
        parser.error("號列必須是 4 碼或 8 碼數字。")

    from captcha_ocr import CaptchaOCR
    ocr = CaptchaOCR(**({"model_dir": args.model_dir} if args.model_dir else {}))

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": QUERY_URL, "Origin": "https://fbfh.trade.gov.tw"})

    total, pages, firms, token = query_firms(s, ccc, ocr, args.captcha_attempts)
    print("\n號列 %s（進口）共 %s 筆、%s 頁" % (ccc, total, pages))

    for i, f in enumerate(firms, 1):
        time.sleep(DELAY)
        basic = pop(s, "popBasic", f["ban"], token)
        time.sleep(DELAY)
        grade = grades_for_year(pop(s, "popGrade", f["ban"], token))
        print("\n[%d] %s  %s" % (i, f["ban"], f["cname"]))
        print("    英文名稱：%s" % f["ename"])
        print("    負責人　：%s" % f["boss"])
        if basic and len(basic[0]) >= 11:
            b = basic[0]
            print("    地址　　：%s" % display(b[6]))
            print("    電話　　：%s　傳真：%s" % (display(b[8]), display(b[10])))
        print("    %s 年實績級距（單位：百萬美元）" % TARGET_YEAR)
        for g in grade:
            if len(g) >= 7:
                print("      %s 年（%s）進口 %s [%s]　出口 %s [%s]" % (
                    display(g[6]), display(g[1]), display(g[4]), GRADES.get(g[4], "?"),
                    display(g[5]), GRADES.get(g[5], "?")))
        if not grade:
            print("      網站未提供 %s 年實績級距。" % TARGET_YEAR)

    print("\n完成，共列出 %d 家。" % len(firms))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\n已中止。")
    except (RuntimeError, requests.RequestException, ValueError, ImportError) as error:
        sys.exit("執行失敗：%s" % error)
