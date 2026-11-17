"""Download actual chapter listings from 0523.tw, with count reconciliation."""
import csv
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import random
import sys
import time
from urllib.parse import urljoin, urlparse, urlencode

import requests

INDEX_URL = "https://0523.tw/tariff-chapters"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE = PROJECT_ROOT / ".test-output" / "tariffs"
DATA_DIR = PROJECT_ROOT / "data"


class IndexParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None
        self.chapters = {}

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href", "")
            self.parts = []

    def handle_data(self, data):
        if self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            url = urljoin(INDEX_URL, self.href)
            match = re.fullmatch(r"/tariff-chapter/(\d{2})", urlparse(url).path)
            if match and urlparse(url).netloc == "0523.tw":
                text = " ".join("".join(self.parts).split())
                count = re.search(r"([\d,]+)\s*個(?:稅則|號列)", text)
                if not count:
                    raise ValueError("章節連結缺少筆數：" + text)
                self.chapters[match[1]] = (url, int(count[1].replace(",", "")))
            self.href = None


class CodeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = None
        self.codes, self.names = [], []

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        if tag == "span" and ("tch-code" in classes or "tch-name" in classes):
            self.active = "code" if "tch-code" in classes else "name"
            self.parts = []

    def handle_data(self, text):
        if self.active:
            self.parts.append(text)

    def handle_endtag(self, tag):
        if tag == "span" and self.active:
            (self.codes if self.active == "code" else self.names).append(
                " ".join("".join(self.parts).split()))
            self.active = None


def fetch(session, url):
    # All network requests pass here; cached reads never wait or contact the site.
    delay = random.uniform(5.0, 10.0)
    print("等待 %.1f 秒後請求：%s" % (delay, url), flush=True)
    time.sleep(delay)
    # No automatic retries: a connect/read timeout propagates and stops the run.
    response = session.get(url, timeout=(15, 60))
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def write_csv(path, headers, rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (DATA_DIR / path).open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(headers)
        writer.writerows(rows)


def api_json(session, endpoint, params, filename):
    path = CACHE / filename
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = json.loads(fetch(session, "https://0523.tw/api/search/" + endpoint + "?" + urlencode(params)))
        if data.get("success") is not True:
            raise ValueError("搜尋 API 未成功：" + endpoint)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def complete_chapter(session, chapter):
    headings = api_json(session, "categories/next-level",
                        {"current_code": chapter, "current_level": 2, "origin": "cn"},
                        chapter + "-headings.json")
    rows = []
    for heading in headings["data"]:
        prefix = heading["code"]
        rows.extend(complete_prefix(session, prefix))
    return rows


def complete_prefix(session, prefix):
    data = api_json(session, "tariffs", {"query": prefix, "page": 1,
                    "per_page": 100, "origin": "cn", "nav": 1}, prefix + "-1.json")
    rows = [(item["code"], item.get("name_zh") or item["name"]) for item in data["data"]]
    if any(not code.startswith(prefix) for code, _name in rows):
        raise ValueError("搜尋結果包含其他號列：" + prefix)
    # The API itself caps both returned items and its reported total at 100.
    # Split full result sets into the next classification level instead of trusting total.
    if len(rows) >= 100:
        children = api_json(session, "categories/next-level",
                            {"current_code": prefix, "current_level": len(prefix), "origin": "cn"},
                            prefix + "-headings.json")
        if not children["data"]:
            raise ValueError("滿 100 筆但無法細分：" + prefix)
        expanded = []
        for child in children["data"]:
            if not child["code"].startswith(prefix) or len(child["code"]) <= len(prefix):
                raise ValueError("細分前綴不正確：" + prefix)
            expanded.extend(complete_prefix(session, child["code"]))
        if not {code for code, _name in rows}.issubset({code for code, _name in expanded}):
            raise ValueError("細分結果未涵蓋原搜尋結果：" + prefix)
        return expanded
    return rows


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        session.headers["User-Agent"] = "Mozilla/5.0 (compatible; tariff-csv-collector/1.0)"
        index_path = CACHE / "index.html"
        index = index_path.read_text(encoding="utf-8") if index_path.exists() else fetch(session, INDEX_URL)
        (CACHE / "index.html").write_text(index, encoding="utf-8")
        parser = IndexParser()
        parser.feed(index)
        if not parser.chapters:
            raise ValueError("總覽頁未解析出任何章節。")
        all_rows, audit = [], []
        for chapter, (url, expected) in sorted(parser.chapters.items()):
            cache = CACHE / (chapter + ".html")
            if cache.exists():
                page = cache.read_text(encoding="utf-8")
            else:
                page = fetch(session, url)
                cache.write_text(page, encoding="utf-8")
            codes = CodeParser()
            codes.feed(page)
            if len(codes.codes) != len(codes.names):
                raise ValueError("第 %s 章號列與品名不對齊。" % chapter)
            rows = list(zip(codes.codes, codes.names))
            visible = len(rows)
            if expected > 250 and visible == 250:
                rows = complete_chapter(session, chapter)
                if not set(codes.codes).issubset({code for code, _name in rows}):
                    raise ValueError("第 %s 章搜尋結果未涵蓋可見的 250 筆。" % chapter)
            if len(rows) != expected:
                raise ValueError("第 %s 章筆數不符：標示 %d、實抓 %d" % (chapter, expected, len(rows)))
            if any(not re.fullmatch(r"[0-9]{11}", code) or not code.startswith(chapter)
                   or not name for code, name in rows):
                raise ValueError("第 %s 章號列或品名格式不符。" % chapter)
            if len({code for code, _name in rows}) != expected:
                raise ValueError("第 %s 章有重複號列。" % chapter)
            links = set(re.findall(r"/tariff/([0-9]{11})(?:[\"'/#?]|$)", page))
            if not set(codes.codes).issubset(links):
                raise ValueError("第 %s 章貨品連結與顯示號列不一致。" % chapter)
            cache.write_text(page, encoding="utf-8")
            all_rows.extend(rows)
            audit.append((chapter, expected, len(rows), len({code[:8] for code, _name in rows}),
                          url, hashlib.sha256(page.encode()).hexdigest()))
            write_csv("input_all.partial.csv", ["ccc", "name"], sorted(all_rows))
            write_csv("ccc_chapter_counts.partial.csv",
                      ["chapter", "expected", "actual", "unique_8", "url", "sha256"], audit)
            print("第 %s 章：%d / %d 筆，核對通過" % (chapter, len(rows), expected), flush=True)
    if len({code for code, _name in all_rows}) != len(all_rows):
        raise ValueError("跨章出現重複號列。")
    all_rows.sort()
    write_csv("input_all.csv", ["ccc", "name"], all_rows)
    write_csv("ccc_chapter_counts.csv", ["chapter", "expected", "actual", "unique_8", "url", "sha256"], audit)
    report = {"source": INDEX_URL, "chapters": len(audit), "chapter_numbers": [r[0] for r in audit],
              "index_counts_sum": sum(row[1] for row in audit), "actual_rows": len(all_rows),
              "unique_11": len({r[0] for r in all_rows}), "unique_8": len({r[0][:8] for r in all_rows}),
              "duplicate_codes": 0, "count_mismatches": 0}
    (DATA_DIR / "ccc_fetch_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, ValueError) as error:
        sys.exit("抓取未完成；已核對的資料保存在 input_all.partial.csv，重跑會使用快取續抓。原因：%s" % error)
