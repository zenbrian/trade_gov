# Railway 部署

本專案作為背景批次 worker 執行，不需要公開網址、HTTP 服務或網頁健康檢查。查詢固定進口，實績固定民國 115 年。搬到雲端可讓本機關機後繼續執行，但不保證查詢更快，也不保證目標網站接受雲端 IP。

## 執行方式

GitHub 程式碼 → Railway Docker build → 下載本機 OCR 模型到映像檔 → 掛載持久化 Volume → 執行批次查詢 → 將 CSV 保存到 Volume → 離線歸戶。

Dockerfile 使用 Python 3.13、uv 0.13.7 與 `uv.lock`。模型在建置階段下載，執行時本機辨識，不呼叫付費 OCR API。容器的 Linux 相依套件與 Python 環境由 Docker 建置。

## GitHub 上傳內容

建議使用 private repository。提交程式、文件、Docker 設定、`pyproject.toml`、`uv.lock`、測試及官方 `data/input_gc453.csv`。這份輸入有 12,707 筆完整 11 碼號列，查詢時會去重為 9,368 個前 8 碼。

`.gitignore` 已排除 `.venv`、`.models`、快取、`.env` 與查詢結果。`data` 只允許官方輸入 CSV、章節計數及驗證報告進入版本控制；公司名單與查詢進度不透過 GitHub 傳送。

## Railway 設定

1. 建立 Railway Project，選擇從 GitHub repository 部署此專案，分支為 `main`，Root Directory 使用 repository 根目錄。
2. Railway 會偵測根目錄的 `Dockerfile`；建置指令與啟動指令採用 Dockerfile 的預設設定。
3. 建立 Volume、連接這個服務，Mount Path 設定成 **`/data`**。
4. Restart Policy 設成 **Never**，先避免錯誤後自動反覆重跑；Replicas 固定 **1**。不要啟用 cron 排程。
5. 不需要 Generate Domain，也不要設定 HTTP Healthcheck。可設定 `PYTHONUNBUFFERED=1`（映像檔已有預設）。
6. 準備好 Volume、上傳既有進度（如需續跑）後，再啟動部署，查看 Deploy Logs。

若首次從 GitHub 部署時 Volume 尚未建立，啟動腳本會退出並提示掛載 `/data`，不會開始遠端查詢。補上 Volume 後重新啟動即可。Railway 自動提供 `RAILWAY_VOLUME_MOUNT_PATH`；不要只自行填這個變數而沒有實際掛載 Volume。

官方說明：[Dockerfile 偵測](https://docs.railway.com/builds/dockerfiles)、[重啟策略](https://docs.railway.com/deployments/restart-policy)。

## 儲存位置

| 內容 | 容器路徑 | 保存方式 |
|---|---|---|
| 官方輸入 | `/app/data/input_gc453.csv` | 隨程式建立於映像檔 |
| OCR 模型 | `/app/.models/en_PP-OCRv4_mobile_rec` | 建置時下載至映像檔 |
| 查詢明細／續跑進度 | `/data/output_all.csv` | Railway Volume |
| 歸戶結果 | `/data/companies.csv` | Railway Volume |

Volume 必須掛在 `/data`，避免蓋住映像檔內的 `/app/data` 輸入清單。未掛 Volume 的容器檔案不適合作為續跑進度。Volume 只在服務啟動時掛載，不能在 Docker build 或 Pre-Deploy 階段依賴它。[官方 Volume 說明](https://docs.railway.com/volumes)

## 接續本機進度

本機爬蟲應先停止，讓 `data/output_all.csv` 成為穩定快照，再把該檔傳到 Volume 的根目錄，檔名維持 `output_all.csv`。同一份輸出不要同時由兩個 worker 寫入。

安裝並登入 Railway CLI、連結正確專案／服務／環境後，可用檔案管理介面：

```sh
railway volume browse /
```

或依 CLI 提示選擇 Volume，再上傳既有進度：

```sh
railway volume files upload ./data/output_all.csv /output_all.csv
```

只有遠端已存在同名檔案、且確定要取代時才加入 `--overwrite`。不需要續跑則不必上傳，程式會建立新檔。啟動指令已帶 `--resume`，會略過已完成項目並重查失敗項目。

注意：`railway run` 在本機執行指令，不等於把爬蟲放到雲端；實際工作應由 Railway 的 Deployment 啟動。

## 完成與取回結果

查看日誌最後的完成數與失敗數。非零結束碼可能表示有部分項目失敗，不代表之前保存的 CSV 遺失。先檢查失敗原因，再決定是否重新啟動；不要用自動重啟迴圈處理網站拒絕或逾時。

可將明細下載回本機，再執行歸戶：

```sh
railway volume files download /output_all.csv ./data/output_railway.csv
uv run python scripts/group_companies.py data/output_railway.csv data/companies_railway.csv
```

檔案路徑相對於 Volume 根目錄，因此下載時使用 `/output_all.csv`。檔案操作介面與選擇特定 Volume 的方式見 [Railway CLI 文件](https://docs.railway.com/cli/volume)。

## 本機 Docker 驗證

以下命令需要 Docker Desktop 使用 Linux containers，從專案根目錄執行。

```sh
docker build -t trade-gov .
docker run --network none --rm --entrypoint python trade-gov -c "import sys; sys.path.insert(0, '/app/scripts'); from captcha_ocr import CaptchaOCR; CaptchaOCR()"
```

這個檢查只載入建置好的 OCR 模型，不查詢貿易署。真正啟動查詢的方式與 Railway 相同，必須先掛 Volume 並設定掛載路徑；不要把載入模型的測試誤認為已完成全量查詢。

uv 使用方式參考 [官方 Docker 指南](https://docs.astral.sh/uv/guides/integration/docker/)。

## 目前限制

- 廠商爬蟲維持原本約 1 秒的部分請求等待；目前沒有環境變數可調整全域速率。之前的 5～10 秒設定只適用 `fetch_tariff_codes.py`。本次 Docker 部署沒有更改查詢節奏。
- 批次對單項查詢或明細錯誤會記錄後繼續，不是「任何逾時就停止整批」。不要因為換到雲端便提高頻率。
- 只有完成整個原始號列後才提交該筆 CSV；中途中止時，下次重查未提交的項目。原子寫入可保留前一份完整進度。
- 公司明細快取只存在單次執行的記憶體中；重啟後仍可能再次讀取已見過公司的資料。
- 全量結果會增加記憶體及 CSV 重寫成本，先用小清單量測 Railway 的記憶體、CPU 與磁碟使用量，再擴大執行。費用與額度以 Railway 帳號顯示為準。
- CSV 與模型可保存在雲端，不代表網站已允許無限制自動查詢；目標網站拒絕時應先停止並確認原因。

官方主檔來源：[關務署 GC453](https://portal.sw.nat.gov.tw/APGQ/GC453)。程式保留 8 碼查詢與原始 11 碼對照，不能解讀成每個 11 碼品項都已各別確認進口實績。
