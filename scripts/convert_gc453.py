"""Validate the official GC453 binary workbook and export ccc,name CSV.

Run with: uv run --no-project --no-cache --with xlrd==2.0.2 python scripts/convert_gc453.py SOURCE
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil

import xlrd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main():
    parser = argparse.ArgumentParser(description="驗證 GC453 官方完整稅則並轉成兩欄 CSV")
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw = args.source.read_bytes()
    if not raw.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        raise ValueError("檔案不是本次官方下載的舊版 Excel 格式。")
    book = xlrd.open_workbook(file_contents=raw)
    if book.nsheets != 1:
        raise ValueError("工作表數量不符預期。")
    sheet = book.sheet_by_index(0)
    if sheet.row_values(0)[:3] != ["貨品分類號列", "中文貨名", "英文貨名"]:
        raise ValueError("官方主檔欄位不符預期。")
    rows, blank_rows, invalid = [], [], []
    for index in range(1, sheet.nrows):
        values = sheet.row_values(index)
        if not any(str(value).strip() for value in values):
            blank_rows.append(index + 1)
            continue
        code, name = values[:2]
        if (not isinstance(code, str) or not re.fullmatch(r"[0-9]{11}", code.strip())
                or not isinstance(name, str) or not name.strip()):
            invalid.append({"source_row": index + 1, "code": str(code), "name": str(name)})
            continue
        rows.append((code.strip(), name.strip()))
    counts = Counter(code for code, _name in rows)
    duplicates = {code: count for code, count in counts.items() if count > 1}
    names = defaultdict(set)
    for code, name in rows:
        names[code].add(name)
    conflicts = {code: sorted(items) for code, items in names.items() if len(items) > 1}
    report = {
        "source_page": "https://portal.sw.nat.gov.tw/APGQ/GC453",
        "download_url": "https://portal.sw.nat.gov.tw/APGQ/Download?fileName=海關進口稅則資料2026.ods",
        "page_last_modify_date_observed": "2026-11-10",
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(raw),
        "actual_file_format": "Excel BIFF/OLE (download named .ods)",
        "sheet_name": sheet.name, "data_rows": sheet.nrows - 1,
        "valid_rows": len(rows), "unique_11": len(counts),
        "unique_8": len({code[:8] for code, _name in rows}),
        "duplicate_codes": duplicates, "conflicting_names": conflicts,
        "invalid_rows": invalid, "blank_rows": blank_rows,
    }
    chapters = Counter(code[:2] for code, _name in rows)
    report["chapters"] = dict(sorted(chapters.items()))
    for filename in ("input.csv", "input_all.partial.csv"):
        path = DATA_DIR / filename
        if path.exists():
            with path.open(encoding="utf-8-sig", newline="") as source:
                previous = list(csv.DictReader(source))
            old_codes = {row["ccc"] for row in previous}
            report["comparison_" + filename] = {
                "previous_rows": len(previous), "matched_codes": len(old_codes & set(counts)),
                "codes_absent_from_official": sorted(old_codes - set(counts)),
                "name_differences": sum(row["ccc"] in names and row["name"] not in names[row["ccc"]]
                                        for row in previous),
            }
    report["passed"] = not (duplicates or conflicts or invalid or blank_rows) and len(rows) > 10000
    (DATA_DIR / "gc453_validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["passed"]:
        raise ValueError("主檔未通過檢查，請查看 gc453_validation.json；未輸出輸入清單。")
    archived = (DATA_DIR / "gc453_source_2026.xls")
    if args.source.resolve() != archived.resolve():
        shutil.copyfile(args.source, archived)
    rows.sort()
    with (DATA_DIR / "input_gc453.csv").open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(["ccc", "name"])
        writer.writerows(rows)
    with (DATA_DIR / "gc453_chapter_counts.csv").open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(["chapter", "count"])
        writer.writerows(sorted(chapters.items()))
    with (DATA_DIR / "input_gc453.csv").open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        assert reader.fieldnames == ["ccc", "name"]
        saved = [(row["ccc"], row["name"]) for row in reader]
    assert saved == rows
    print(json.dumps({key: report[key] for key in
          ("passed", "valid_rows", "unique_11", "unique_8", "duplicate_codes", "invalid_rows")}, ensure_ascii=False))
    print("CHAPTERS", len(chapters))
    for key in report:
        if key.startswith("comparison_"):
            print(key, report[key])


if __name__ == "__main__":
    main()
