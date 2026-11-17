# 貿易署廠商查詢

自動用本機 PaddleOCR 辨識驗證碼，固定只查進口廠商，實績固定只保留民國 **115 年**。支援單筆終端查詢及 CSV 批次輸入、輸出。

## CSV 批次查詢

輸入檔固定為 UTF-8 CSV，只有 `ccc,name` 兩欄。`ccc` 填完整 **11 碼**，程式查詢時才取前 8 碼：

```csv
ccc,name
01012100003,馬，純種繁殖用
01029090009,其他牛
```

`data/input_ch1.csv` 已放入指定的 122 筆資料。執行：

```powershell
uv run python scripts/trade_batch.py data/input_ch1.csv data/output_ch1.csv
```

每完成一個號列即保存 CSV；中斷或部分失敗後，可跳過已成功項目續跑：

```powershell
uv run python scripts/trade_batch.py data/input_ch1.csv data/output_ch1.csv --resume
```

批次只載入一次 OCR；同前 8 碼只查一次，同公司明細在本次執行中也只取一次。號列與公司的對應仍各自保留。

輸出只保留 115 年，以「號列、公司、年度」為一列，包含 `ccc`（查詢用 8 碼）、`ccc_full`（原始 11 碼）、`name`（貨品名）、`ban`（統編）、`cname` / `ename`（公司名稱）、聯絡資料、`year`（民國年）、`period`（原始統計期間）、進出口實績級距及來源網址、查詢時間。`period` 保留網站實際涵蓋月份，不將部分年度當成全年。

`status`：`ok` 為已取得 115 年資料，`no_grades` 為有公司但未提供 115 年資料，`no_firms` 為查無公司，`query_error` / `detail_error` 為查詢或明細失敗。缺少 115 年資料時保留公司、標示 `year=115` 並留空級距，不以其他年度或零實績代替。查無公司與失敗也會保留一列，失敗原因寫在 `error`。

輸出為 UTF-8 BOM。CSV 本身沒有欄位型別；用 Excel 匯入時，請將 CCC 與統編欄設為文字，保留前導零。

## 執行（uv）

在專案根目錄執行：

```powershell
uv sync
uv run python scripts/trade_query.py
```

輸入號列後會自動辨識並查詢進口廠商，不再詢問驗證碼。也可以直接指定號列：

```powershell
uv run python scripts/trade_query.py 08045021
```

Python 支援範圍為 3.10–3.13。相依版本由 `uv.lock` 鎖定。Windows 使用複製安裝與無快取模式，以避開本機遇到的快取重新命名問題。

## 本機模型

首次使用會從 PaddleOCR 官方網址下載英數文字辨識模型，存放在專案 `.models/en_PP-OCRv4_mobile_rec`。之後從這個目錄載入，在 CPU 上辨識，不需 API Key，也不把圖片送到第三方 OCR 服務。取得驗證碼、驗證與查詢廠商仍需連線貿易署。

可事先下載模型：

```powershell
uv run python scripts/captcha_ocr.py --prepare
```

可用 `--model-dir 路徑` 指定同款已下載的完整模型目錄。

## 失敗處理與資料意義

- 自動驗證最多重取 5 張圖片；每張最多提交 3 個實際辨識出的不同字串。失敗會停止並回傳非零狀態，不改成手動詢問，也不誤報為沒有廠商。
- `--captcha-attempts 3` 可調整重取次數，允許範圍 1–10。
- 翻頁會檢查成功狀態、重複統編與總筆數，名單不完整時不輸出「完成」。
- 級距是公司整體年度進出口實績，單位為百萬美元，不是該號列金額。公司可能申請不公開 CCC 查詢結果。
- OCR 不保證每次辨識成功；辨識失敗、網站錯誤與沒有廠商是不同狀態。

## 測試

```powershell
uv run python -m unittest discover -s tests -t . -v
```

自動測試不連線網站，涵蓋有界重試、驗證錯誤、HTTP 錯誤、翻頁重複、缺筆及格式解析。

2026-11-16 官方網站實測：`08045021` 進口取得 1 家及五年級距，未人工輸入驗證碼。這是實測個案，不代表每次辨識都成功；測試期間也曾遇到連續辨識失敗。
