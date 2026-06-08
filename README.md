# 臺灣農產品批發價格與氣候風險監測系統
### 端到端自動化資料工程與動態視覺化平台 (End-to-End Data Pipeline & Dashboard)

本專案為自動化更新，用以即時監測臺灣核心農產品（以水果類別為主，如香蕉、西瓜）在各產地與批發市場的交易價格波動。
---

## 系統架構 (System Architecture)

系統整體架構依循標準資料工程生命週期設計，從數據萃取到最終前端呈現共分為四個主要層次：

### 1. 資料來源 (Data Source)
* **網站：** [農業資料開放平台農產品交易行情](https://data.moa.gov.tw/open_detail.aspx?id=037) 
* **API：** [資料API](https://data.moa.gov.tw/Service/OpenData/FromM/FarmTransData.aspx)
透過介接說明文件，輸入指定的交易日期、作物代號及種類代碼，即可取得該日作物交易資料。

### 2. 自動化更新與 ETL pipeline
* **自動化排程：** 利用 **GitHub Actions** 部署自動化工作流程（`.github/workflows/update_data.yml`），於每日台灣時間上午 8 點（UTC 00:00）更新。
* **資料處理 (`etl_pipeline.py`)：** 
  * **Extract (萃取)：** 透過 `requests` 與 API 爬取作物交易資料。
  * **Transform (轉換)：** 運用 `pandas` 進行資料清洗、極端值剔除、過濾交易量或均價小於等於 0 的異常值，並規範化欄位名稱。
  * **Load (載入)：** 透過 `sqlalchemy` 建立與資料庫(supabase)連線，將清洗後的乾淨資料整齊寫入雲端。

### 3. 雲端資料倉儲 (Database)
* **資料庫平台：** 採用 **Supabase 資料庫**。
* **數據冪等性 (Idempotence)：** 資料表特別設計 `(date, market_name, crop_code)` 之複合唯一鍵限制（Unique Constraint），寫入時利用 SQL 語法 `ON CONFLICT DO NOTHING` 相結合，確保管線即使重複執行，資料也不會重複或污染。

### 4. 互動視覺化前端 (Dashboard)
* **網頁框架：** 使用 **Streamlit 網頁框架** (`app.py`) 快速搭建。
* **互動圖表：** 搭配 **Plotly Express / Graph Objects**，實作最新交易日市場均價橫向對比、動態天數控制的價格與交易量「雙軸折線圖」、以及各市場價格波動區間之「箱型圖（Box Plot）」。

---

## 技術細節

本系統針對農業資料的特殊性與雲端部署環境，實作了多項健全性機制：

* **休市日與缺失值處理策略：** 後端資料庫堅持「資料純潔性」，對於市場休市（如週一休市）或無交易之日期不進行盲目向前補值（Imputation），維持真實歷史帳本。前端視覺化則運用 Plotly 的 `rangebreaks` 屬性，在繪圖時自動折疊並隱藏無交易日之 X 軸刻度，在不偽造數據的前提下提供連續、流暢的折線圖閱讀體驗。
* **效能優化 (Caching)：** 網頁端加載資料時實作了 `@st.cache_data(ttl=600)` 快取機制，限制十分鐘內重複存取直接由記憶體讀取，大幅降低雲端資料庫之併發查詢負載。

---

### 網站連結: https://datavisualizationfinalbonus-d4drexyqyrp8yackbz6yth.streamlit.app/