"""Importers only: CSV input to CSV output, reusing one local OCR model."""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import sys
import tempfile
import time

import requests

from captcha_ocr import CaptchaOCR
from trade_query import (DELAY, GRADES, QUERY_URL, TARGET_YEAR, UA, display,
                         grades_for_year, pop, query_firms)

OUTPUT_FIELDS = [
    "ccc", "ccc_full", "name", "direction", "status", "query_total", "ban",
    "cname", "ename", "boss", "address", "phone", "fax", "year", "period",
    "import_grade", "import_range_musd", "export_grade", "export_range_musd",
    "source_url", "queried_at", "error",
]
COMPLETE_STATUSES = {"ok", "no_grades", "no_firms"}
TAIPEI = timezone(timedelta(hours=8))


def read_input(path):
    """Identifiers remain strings: never infer numeric types or drop zeroes."""
    jobs = []
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != ["ccc", "name"]:
            raise ValueError("輸入 CSV 必須只有兩欄，標頭為 ccc,name。")
        for line, raw in enumerate(reader, 2):
            if None in raw or any(raw.get(field) is None for field in ("ccc", "name")):
                raise ValueError("輸入 CSV 第 %d 列欄位數不正確。" % line)
            code = raw["ccc"].strip()
            if not re.fullmatch(r"[0-9]{11}", code):
                raise ValueError("第 %d 列 ccc 必須為完整 11 碼數字，且須保留前導零。" % line)
            jobs.append({"ccc": code[:8], "ccc_full": code, "name": raw["name"].strip()})
    if not jobs:
        raise ValueError("輸入 CSV 沒有資料列。")
    keys = [job_key(job) for job in jobs]
    if len(set(keys)) != len(keys):
        raise ValueError("輸入 CSV 有完全重複的資料列，請先移除重複列。")
    return jobs


def job_key(row):
    return tuple(row.get(field, "") for field in ("ccc", "ccc_full", "name"))


def write_output(path, rows):
    """Write a complete checkpoint atomically, with a BOM for Chinese CSV readers."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="",
                                         dir=path.parent, suffix=".csv.tmp", delete=False) as target:
            temp_path = Path(target.name)
            writer = csv.DictWriter(target, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def read_previous(path):
    groups = {}
    if not Path(path).exists():
        return groups
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != OUTPUT_FIELDS:
            raise ValueError("續跑輸出的欄位格式不符，請指定本程式產生的 output CSV。")
        for row in reader:
            groups.setdefault(job_key(row), []).append(row)
    return groups


def is_complete(rows):
    if not rows or any(row.get("status") not in COMPLETE_STATUSES or row.get("error")
                       or row.get("direction") != "I" for row in rows):
        return False
    totals = {str(row.get("query_total", "")) for row in rows}
    if len(totals) != 1:
        return False
    total = next(iter(totals))
    if not total.isascii() or not total.isdigit():
        return False
    if int(total) == 0:
        return len(rows) == 1 and rows[0]["status"] == "no_firms" and not rows[0].get("ban")
    return (all(re.fullmatch(r"[0-9]{8}", row.get("ban", ""))
                and display(row.get("year")) == TARGET_YEAR for row in rows)
            and len({row["ban"] for row in rows}) == int(total))


def records_for_code(session, ccc, ocr, detail_cache, attempts):
    stamp = datetime.now(TAIPEI).isoformat(timespec="seconds")
    common = {"direction": "I", "source_url": QUERY_URL, "queried_at": stamp, "error": ""}
    total, _pages, firms, token = query_firms(session, ccc, ocr, attempts)
    common["query_total"] = total
    if not firms:
        return [{**common, "status": "no_firms"}]
    records = []
    for firm in firms:
        row = {**common, "year": TARGET_YEAR,
               **{key: firm[key] for key in ("ban", "cname", "ename", "boss")}}
        try:
            if firm["ban"] not in detail_cache:
                time.sleep(DELAY)
                basic = pop(session, "popBasic", firm["ban"], token)
                time.sleep(DELAY)
                grades = grades_for_year(pop(session, "popGrade", firm["ban"], token))
                if basic and len(basic[0]) < 11:
                    raise RuntimeError("廠商基本資料的欄位不完整。")
                detail_cache[firm["ban"]] = basic, grades
            basic, grades = detail_cache[firm["ban"]]
            if basic:
                row.update(address=display(basic[0][6]), phone=display(basic[0][8]), fax=display(basic[0][10]))
            if not grades:
                records.append({**row, "status": "no_grades"})
            for grade in grades:
                records.append({
                    **row, "status": "ok", "year": display(grade[6]), "period": display(grade[1]),
                    "import_grade": display(grade[4]), "import_range_musd": GRADES.get(grade[4], ""),
                    "export_grade": display(grade[5]), "export_range_musd": GRADES.get(grade[5], ""),
                })
        except (RuntimeError, requests.RequestException, ValueError) as error:
            records.append({**row, "status": "detail_error", "error": str(error)})
    return records


def run_batch(jobs, output, session, ocr, attempts=5, resume=False):
    previous = read_previous(output) if resume else {}
    groups = {job_key(job): previous[job_key(job)] for job in jobs
              if job_key(job) in previous}
    detail_cache, query_cache = {}, {}
    for index, job in enumerate(jobs, 1):
        key = job_key(job)
        print("\n[%d/%d] %s %s" % (index, len(jobs), job["ccc"], job["name"]), flush=True)
        if resume and is_complete(groups.get(key)):
            print("  已完成，略過。", flush=True)
            continue
        try:
            if job["ccc"] not in query_cache:
                time.sleep(DELAY)
                query_cache[job["ccc"]] = records_for_code(session, job["ccc"], ocr, detail_cache, attempts)
            records = query_cache[job["ccc"]]
            groups[key] = [{**record, **job} for record in records]
        except (RuntimeError, requests.RequestException, ValueError) as error:
            groups[key] = [{**job, "direction": "I", "status": "query_error", "error": str(error),
                            "source_url": QUERY_URL,
                            "queried_at": datetime.now(TAIPEI).isoformat(timespec="seconds")}]
        # Commit every code, so interruption does not discard earlier queries.
        write_output(output, [row for item in jobs for row in groups.get(job_key(item), [])])
        rows = groups[key]
        if is_complete(rows):
            print("  查詢完成：%s 家；已寫入 CSV。" % rows[0]["query_total"], flush=True)
        else:
            errors = sorted({row["error"] for row in rows if row.get("error")})
            print("  查詢未完整：%s" % " / ".join(errors), flush=True)
    all_rows = [row for job in jobs for row in groups.get(job_key(job), [])]
    failed = sum(not is_complete(groups.get(job_key(job))) for job in jobs)
    print("\n已完成 %d/%d 個號列，失敗 %d 個；輸出 %d 列：%s" %
          (len(jobs) - failed, len(jobs), failed, len(all_rows), Path(output).resolve()), flush=True)
    return failed


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="CSV 批次查進口廠商與 115 年實績；11 碼號列自動取前 8 碼")
    parser.add_argument("input", type=Path, help="UTF-8 CSV，兩欄 ccc,name；ccc 為完整 11 碼")
    parser.add_argument("output", type=Path, help="結果 CSV，固定只保留 115 年實績")
    parser.add_argument("--resume", action="store_true", help="略過已成功的輸入列，只重查失敗或未完成項目")
    parser.add_argument("--captcha-attempts", type=int, default=5, help="每號列的驗證碼次數，1–10（預設 5）")
    parser.add_argument("--model-dir", type=Path, help="本機 OCR 模型目錄")
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("輸入與輸出 CSV 不可為同一個檔案。")
    if not 1 <= args.captcha_attempts <= 10:
        parser.error("--captcha-attempts 必須介於 1 與 10。")
    jobs = read_input(args.input)
    if args.resume:
        read_previous(args.output)  # Validate before loading the OCR model.
    ocr = CaptchaOCR(**({"model_dir": args.model_dir} if args.model_dir else {}))
    with requests.Session() as session:
        session.headers.update({"User-Agent": UA, "Referer": QUERY_URL,
                                "Origin": "https://fbfh.trade.gov.tw"})
        return int(bool(run_batch(jobs, args.output, session, ocr, args.captcha_attempts, args.resume)))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("\n已中止；已完成項目保存在輸出 CSV，可用 --resume 續跑。")
    except (OSError, RuntimeError, ValueError, requests.RequestException, ImportError) as error:
        sys.exit("批次執行失敗：%s" % error)
