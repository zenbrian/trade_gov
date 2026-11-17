"""Offline CSV consolidation by business account number; does not run the crawler."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import sys
import tempfile

DETAIL_FIELDS = [
    "ccc", "ccc_full", "name", "direction", "status", "query_total", "ban", "cname",
    "ename", "boss", "address", "phone", "fax", "year", "period", "import_grade",
    "import_range_musd", "export_grade", "export_range_musd", "source_url", "queried_at", "error",
]
COMPANY_FIELDS = [
    "sort_ccc", "ban", "company_display", "cname", "ename", "boss", "address", "phone", "fax",
    "direction", "year", "period", "import_grade", "import_range_musd", "export_grade",
    "export_range_musd", "status", "ccc_codes", "ccc_full_codes", "items", "ccc_details",
    "source_urls", "queried_at_first", "queried_at_last", "field_variants", "source_records",
]
PROFILE_FIELDS = ["cname", "ename", "boss", "address", "phone", "fax", "year", "period",
                  "import_grade", "import_range_musd", "export_grade", "export_range_musd"]


def as_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read_details(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or not set(DETAIL_FIELDS).issubset(reader.fieldnames):
            raise ValueError("請輸入爬蟲產生的明細 CSV，欄位須包含統編、號列與實績等資訊。")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("明細 CSV 存在欄位數不正確的資料列。")
    return rows


def consolidate(rows):
    groups = {}
    skipped = 0
    for line, row in enumerate(rows, 2):
        if not row.get("ban"):
            skipped += 1
            continue
        if (not re.fullmatch(r"[0-9]{8}", row["ban"])
                or not re.fullmatch(r"[0-9]{8}", row.get("ccc", ""))
                or not re.fullmatch(r"[0-9]{11}", row.get("ccc_full", ""))
                or not row["ccc_full"].startswith(row["ccc"])):
            raise ValueError("第 %d 列統編或 CCC 格式不正確，請確認前導零。" % line)
        if row.get("direction") != "I" or row.get("year") != "115":
            raise ValueError("第 %d 列非進口或非 115 年資料，請先確認來源明細。" % line)
        groups.setdefault(row["ban"], []).append(row)

    companies = []
    for ban, sources in groups.items():
        # One displayed profile; all differing source values remain available below.
        ordered = sorted(sources, key=lambda row: row.get("queried_at", ""))
        base = ordered[-1]
        codes = sorted({row["ccc"] for row in sources})
        mappings = sorted({(row["ccc"], row["ccc_full"], row["name"]) for row in sources})
        dates = sorted({row["queried_at"] for row in sources if row.get("queried_at")})
        variants = {}
        for field in PROFILE_FIELDS:
            values = sorted({row.get(field, "") for row in sources if row.get(field, "")})
            if len(values) > 1:
                variants[field] = values
        statuses = {row.get("status", "") for row in sources}
        needs_review = bool(variants) or bool(statuses - {"ok", "no_grades"}) or any(
            row.get("error") for row in sources)
        status = "needs_review" if needs_review else ("ok" if "ok" in statuses else "no_grades")
        result = {field: base.get(field, "") for field in PROFILE_FIELDS}
        result.update({
            "sort_ccc": codes[0], "ban": ban,
            "company_display": "%s（%s）" % (base["cname"], "、".join(codes)),
            "direction": "I", "status": status, "ccc_codes": "、".join(codes),
            "ccc_full_codes": "、".join(sorted({row["ccc_full"] for row in sources})),
            "items": "；".join("%s：%s" % (full, name) for _code, full, name in mappings),
            "ccc_details": as_json([{"ccc": code, "ccc_full": full, "name": name}
                                    for code, full, name in mappings]),
            "source_urls": "、".join(sorted({row["source_url"] for row in sources if row.get("source_url")})),
            "queried_at_first": dates[0] if dates else "",
            "queried_at_last": dates[-1] if dates else "",
            "field_variants": as_json(variants), "source_records": as_json(sources),
        })
        companies.append(result)
    return sorted(companies, key=lambda row: (row["sort_ccc"], row["ban"])), skipped


def write_companies(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="",
                                         dir=path.parent, delete=False) as target:
            temp = Path(target.name)
            writer = csv.DictWriter(target, fieldnames=COMPANY_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def main():
    parser = argparse.ArgumentParser(description="離線依統編歸戶，依最小 CCC 排序；僅接受進口、115 年明細")
    parser.add_argument("input", type=Path, help="原始爬蟲明細，例如 output.csv")
    parser.add_argument("output", type=Path, help="公司歸戶名單，例如 companies.csv")
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("輸出檔不可覆蓋輸入明細。")
    rows = read_details(args.input)
    companies, skipped = consolidate(rows)
    write_companies(args.output, companies)
    print("明細 %d 列，歸戶為 %d 家公司；無統編 %d 列留在原始明細。" %
          (len(rows), len(companies), skipped))
    print("輸出：%s" % args.output.resolve())


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit("歸戶失敗：%s" % error)
