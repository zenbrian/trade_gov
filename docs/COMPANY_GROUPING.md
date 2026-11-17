# 公司歸戶

爬取與整理分開執行。原有爬蟲不需修改。

```powershell
uv run python scripts/trade_batch.py data/input_ch1.csv data/output_ch1.csv
uv run python scripts/group_companies.py data/output_ch1.csv data/companies.csv
```

以上指令從專案根目錄執行。第二步完全離線，不載入 OCR，不連線網站，也不改寫輸入明細。

以統編歸戶，每家公司一列，依最小 8 碼 CCC、統編排序。`company_display` 顯示「公司（號列、號列）」，`ccc_codes` 去重排序；`ccc_full_codes`、`items`、`ccc_details` 保留原始 11 碼與品名對應。這是 8 碼查詢結果的彙整，不代表已逐一確認各 11 碼細項的進口紀錄。

只接受進口、115 年公司資料，級距不加總。若同公司資料有不同值，主欄採最後查詢紀錄，`field_variants` 保存差異並標記 `needs_review`。`source_records` 保存該公司所有原始資料列（JSON），包括來源、時間、狀態及錯誤，供 AI 後續整理及追溯。

沒有統編的查無廠商或查詢失敗列不列為公司，仍完整留在原始 `output.csv`。缺少 115 年實績的公司保留 `no_grades`，級距留空。

輸出為 UTF-8 BOM；Excel 匯入時將統編、CCC 欄設為文字以保留前導零。由於單一公司的完整來源資料可能超過 Excel 單格字數限制，AI 讀取時建議直接以 CSV/JSON 解析，不經 Excel 轉存。
