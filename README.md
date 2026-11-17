# 台灣進口商查詢

- `scripts/`：查詢、歸戶、官方 CCC 轉換腳本。
- `docs/`：完整操作說明與歸戶規則。
- `data/`：輸入 CSV、查詢明細、官方原始主檔及驗證報告。
- `tests/`：離線測試。
- `.models/`、`.venv/`：本機 OCR 模型及 uv 環境。

## 本機執行

在專案根目錄執行（查詢固定進口、實績固定 115 年）：

```powershell
uv sync --locked
uv run --frozen python scripts/trade_batch.py data/input_gc453.csv data/output_all.csv --resume
uv run --frozen python scripts/group_companies.py data/output_all.csv data/companies.csv
```

首次使用會下載官方 PaddleOCR 模型，之後在本機 CPU 辨識。查詢仍須連接貿易署網站；辨識失敗會有限次重試。`--resume` 略過已保存的完成項目，重新查詢失敗項目。

輸入為 UTF-8 CSV，欄位固定 `ccc,name`，`ccc` 保留完整 11 碼及前導零。程式取前 8 碼去重查詢，輸出保留原始 11 碼對照。

## Railway 部署

已提供根目錄 `Dockerfile` 與背景 worker 啟動腳本。請依 [Railway 部署說明](docs/RAILWAY.md) 建立服務，掛載 **`/data` Volume**，再啟動。可上傳本機 `data/output_all.csv` 接續進度。

部署不需要公開網址。Volume 尚未掛載時，啟動腳本會退出，不開始查詢。GitHub 不包含公司名單、查詢進度、本機環境與模型。

## 官方資料與限制

官方完整號列清單 `data/input_gc453.csv` 來自關務署 [GC453](https://portal.sw.nat.gov.tw/APGQ/GC453)，共 **12,707 筆不重複 11 碼 CCC**，對應 **9,368 個 8 碼查詢代碼**。來源驗證與章節統計見 `data/gc453_validation.json`、`data/gc453_chapter_counts.csv`。

實績是公司的整體年度進出口級距，不是各 CCC 品項的進口金額。115 年資料可能只是部分期間，請依輸出 `period` 解讀。8 碼結果對應回 11 碼，不表示每個 11 碼品項都已各別確認進口；廠商亦可不公開以 CCC 查詢。

廠商爬蟲保留原有部分請求約 1 秒等待，批次會記錄單項錯誤後繼續。5～10 秒間隔僅適用另一支 `fetch_tariff_codes.py`。

## 測試

離線測試：

```powershell
uv run --frozen python -m unittest discover -s tests -t . -v
```

詳細說明見 [操作說明](docs/README.md) 與 [公司歸戶](docs/COMPANY_GROUPING.md)。
