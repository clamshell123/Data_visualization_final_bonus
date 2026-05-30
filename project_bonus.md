# 期末加分作業：台灣水果批發價格儀表板

> **主題**：台灣常見水果（香蕉、西瓜等）各批發市場每日價格追蹤儀表板  
> **技術棧**：農業部 API → Python ETL → Supabase (PostgreSQL) → Streamlit → Streamlit Community Cloud  
> **預估完成時間**：約 3 小時

---

## 系統架構總覽

```
[農業部 API]
     │  每日定時（GitHub Actions Cron Job）
     ▼
[Python ETL 腳本 etl_pipeline.py]
     │  抓取、清洗、轉換
     ▼
[Supabase (雲端 PostgreSQL)]
     │  SQL SELECT
     ▼
[Streamlit app.py]
     │  部署
     ▼
[Streamlit Community Cloud]（公開 HTTPS URL）
```

---

## Phase 1：基礎設施與資料庫建置（約 20 分鐘）

### 1-1 專案資料夾結構

在本機建立以下結構：

```
fruit-price-dashboard/
├── .github/
│   └── workflows/
│       └── update_data.yml
├── etl_pipeline.py
├── app.py
├── requirements.txt
└── README.md
```

### 1-2 Supabase 資料庫建置

1. 前往 [https://supabase.com/](https://supabase.com/) 免費註冊帳號
2. 建立新 Project（記下你設定的 **Database Password**）
3. 進入 **SQL Editor**，貼上以下語法建立資料表：

```sql
-- 水果批發價格資料表
CREATE TABLE IF NOT EXISTS crop_price (
    id            BIGSERIAL PRIMARY KEY,
    date          DATE           NOT NULL,         -- 交易日期
    market_name   TEXT           NOT NULL,         -- 批發市場名稱（如台北市場、台中市場）
    crop_code     TEXT           NOT NULL,         -- 作物代碼（農業部代碼，香蕉=1001）
    crop_name     TEXT           NOT NULL,         -- 作物中文名稱
    avg_price     NUMERIC(10,2),                   -- 平均批發價（元/公斤）
    trade_volume  NUMERIC(12,2),                   -- 交易量（公斤）
    created_at    TIMESTAMPTZ    DEFAULT NOW(),
    UNIQUE (date, market_name, crop_code)          -- 防止重複寫入
);

-- 建立索引加速查詢
CREATE INDEX IF NOT EXISTS idx_crop_price_date ON crop_price(date DESC);
CREATE INDEX IF NOT EXISTS idx_crop_price_crop ON crop_price(crop_code);
```

> **說明**：`UNIQUE (date, market_name, crop_code)` 確保同一天同一市場同一作物只有一筆，搭配後續 `INSERT ... ON CONFLICT DO NOTHING` 實現冪等寫入（重跑不重複）。

4. 取得連線資訊：  
   - 左側選單 → **Settings → Database**  
   - 複製 **Connection String（URI 格式）**，格式如下：  
     `postgresql://postgres:[密碼]@db.[project-ref].supabase.co:5432/postgres`

---

## Phase 2：ETL 資料流開發（約 60 分鐘）

### 2-1 農業部 API 說明

使用農業部「農產品交易行情」Open API（免費、免申請 Key）：

```
GET https://apis.data.gov.tw/agri/api/v1/transactionData
參數：
  - StartDate: YYYY-MM-DD
  - EndDate:   YYYY-MM-DD
  - CropCode:  作物代碼（香蕉=1001，西瓜=1101，可查農業部作物代碼表）
  - $format:   JSON
```

> **備用 API**：若上述 API 有限制，可改用農委會菜蟲 API：  
> `https://data.moa.gov.tw/Service/OpenData/FromM/FarmTransData.aspx`

### 2-2 `etl_pipeline.py` 完整實作

```python
"""
etl_pipeline.py
功能：從農業部 API 抓取水果批發價，清洗後寫入 Supabase
執行方式：python etl_pipeline.py
"""

import os
import requests
import pandas as pd
from datetime import date, timedelta
from sqlalchemy import create_engine, text

# ── 設定 ─────────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ["DATABASE_URL"]   # 從環境變數讀取，勿寫死

# 要追蹤的作物（代碼: 名稱）
CROPS = {
    "1001": "香蕉",
    "1101": "西瓜",
}

API_URL = "https://apis.data.gov.tw/agri/api/v1/transactionData"

# ── Step 1: 抓取資料 ──────────────────────────────────────────────────────────
def fetch_crop_data(crop_code: str, start_date: date, end_date: date) -> pd.DataFrame:
    """呼叫農業部 API，回傳原始 DataFrame"""
    params = {
        "StartDate": start_date.strftime("%Y-%m-%d"),
        "EndDate":   end_date.strftime("%Y-%m-%d"),
        "CropCode":  crop_code,
        "$format":   "JSON",
    }
    response = requests.get(API_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    if not data:
        print(f"  [警告] {crop_code} 在 {start_date}~{end_date} 無資料")
        return pd.DataFrame()

    df = pd.DataFrame(data)
    return df

# ── Step 2: 資料清洗（Data Wrangling）────────────────────────────────────────
def clean_data(df: pd.DataFrame, crop_code: str, crop_name: str) -> pd.DataFrame:
    """
    清洗邏輯：
    1. 重新命名欄位（API 回傳為中文欄位名）
    2. 型別轉換
    3. 處理休市日（見說明）
    4. 去除異常值（價格為 0 或負數）
    """
    if df.empty:
        return df

    # 依實際 API 回傳欄位調整（範例欄位名）
    rename_map = {
        "交易日期": "date",
        "市場名稱": "market_name",
        "平均價":   "avg_price",
        "交易量":   "trade_volume",
    }
    df = df.rename(columns=rename_map)

    # 保留需要的欄位
    needed = ["date", "market_name", "avg_price", "trade_volume"]
    df = df[[c for c in needed if c in df.columns]].copy()

    # 型別轉換
    df["date"]         = pd.to_datetime(df["date"]).dt.date
    df["avg_price"]    = pd.to_numeric(df["avg_price"],    errors="coerce")
    df["trade_volume"] = pd.to_numeric(df["trade_volume"], errors="coerce")

    # 加上作物欄位
    df["crop_code"] = crop_code
    df["crop_name"] = crop_name

    # 去除無效資料（價格異常）
    df = df[df["avg_price"] > 0].copy()

    # ── 休市日處理說明 ──────────────────────────────────────────────────────
    # 批發市場通常週一休市（農曆假日亦可能休市）。
    # 本專案採「不補值」策略：
    #   - 資料庫只儲存「實際有交易」的日期，休市日不寫入任何資料。
    #   - 前端查詢時，使用 Plotly 的 rangebreaks 功能跳過休市日，
    #     使折線圖不出現斷點，視覺上呈現連續交易日序列。
    #   - 優點：資料真實，無人工填補造成的誤導。
    #   - 如需填補（例如計算移動平均），可在 app.py 的 DataFrame 操作時
    #     使用 df.resample('D').ffill() 僅用於計算，不寫回資料庫。
    # ───────────────────────────────────────────────────────────────────────

    return df

# ── Step 3: 寫入資料庫 ───────────────────────────────────────────────────────
def upsert_to_db(df: pd.DataFrame, engine) -> None:
    """將 DataFrame 寫入 Supabase，使用 ON CONFLICT DO NOTHING 防止重複"""
    if df.empty:
        return

    rows = df.to_dict(orient="records")
    insert_sql = text("""
        INSERT INTO crop_price (date, market_name, crop_code, crop_name, avg_price, trade_volume)
        VALUES (:date, :market_name, :crop_code, :crop_name, :avg_price, :trade_volume)
        ON CONFLICT (date, market_name, crop_code) DO NOTHING
    """)
    with engine.begin() as conn:
        conn.execute(insert_sql, rows)
    print(f"  [成功] 寫入 {len(rows)} 筆資料")

# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    # 抓取「前 7 天」的資料（確保補齊任何遺漏）
    end_date   = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=6)

    engine = create_engine(DATABASE_URL)

    for crop_code, crop_name in CROPS.items():
        print(f"\n▶ 處理 {crop_name}（代碼 {crop_code}）...")
        raw_df     = fetch_crop_data(crop_code, start_date, end_date)
        cleaned_df = clean_data(raw_df, crop_code, crop_name)
        upsert_to_db(cleaned_df, engine)

    print("\n✅ ETL 完成！")

if __name__ == "__main__":
    main()
```

---

## Phase 3：自動化排程（GitHub Actions）（約 20 分鐘）

### 3-1 建立 Workflow 檔案

路徑：`.github/workflows/update_data.yml`

```yaml
name: Daily Fruit Price ETL

on:
  schedule:
    # 每天 UTC 00:00 = 台灣時間 08:00
    - cron: "0 0 * * *"
  workflow_dispatch:  # 允許手動觸發（方便測試）

jobs:
  etl:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install Dependencies
        run: pip install -r requirements.txt

      - name: Run ETL Pipeline
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
        run: python etl_pipeline.py
```

### 3-2 設定 GitHub Secrets

1. 在 GitHub Repo → **Settings → Secrets and variables → Actions**
2. 點選 **New repository secret**
3. 新增以下 Secret：

| Name           | Value                                          |
|----------------|------------------------------------------------|
| `DATABASE_URL` | 你的 Supabase Connection URI（含密碼）         |

---

## Phase 4：儀表板視覺化（Streamlit）（約 50 分鐘）

### 4-1 `app.py` 完整實作

```python
"""
app.py
功能：從 Supabase 讀取水果批發價資料，呈現互動式儀表板
部署：Streamlit Community Cloud
"""

import os
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import create_engine
from datetime import date, timedelta

# ── 頁面基本設定 ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="台灣水果批發價格儀表板",
    page_icon="🍌",
    layout="wide",
)

st.title("🍌 台灣水果批發市場價格儀表板")
st.caption(f"資料來源：農業部農產品交易行情 API｜更新時間：每日台灣時間 08:00")

# ── 讀取資料（加快取，10 分鐘重整一次）────────────────────────────────────────
@st.cache_data(ttl=600)
def load_data(days: int = 30) -> pd.DataFrame:
    """從 Supabase 讀取最近 N 天的資料"""
    engine = create_engine(st.secrets["DATABASE_URL"])
    since  = date.today() - timedelta(days=days)
    query  = f"""
        SELECT date, market_name, crop_name, avg_price, trade_volume
        FROM   crop_price
        WHERE  date >= '{since}'
        ORDER  BY date DESC
    """
    df = pd.read_sql(query, engine)
    df["date"] = pd.to_datetime(df["date"])
    return df

df = load_data(days=30)

# ── 側邊欄篩選器 ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("🔍 篩選條件")
    crop_options = df["crop_name"].unique().tolist()
    selected_crop = st.selectbox("選擇水果", crop_options)

    market_options = df["market_name"].unique().tolist()
    selected_markets = st.multiselect(
        "選擇市場（可複選）",
        market_options,
        default=market_options[:5],  # 預設顯示前 5 個市場
    )

    days_range = st.slider("顯示天數", min_value=7, max_value=30, value=14)

# 過濾資料
filtered = df[
    (df["crop_name"] == selected_crop) &
    (df["market_name"].isin(selected_markets))
].copy()

# ── 區塊 1：今日各市場價格（最新一天）────────────────────────────────────────
st.subheader(f"📊 {selected_crop} 最新交易日各市場均價")

latest_date = filtered["date"].max()
today_df = filtered[filtered["date"] == latest_date].sort_values("avg_price", ascending=False)

if not today_df.empty:
    col1, col2 = st.columns([2, 1])
    with col1:
        fig_bar = px.bar(
            today_df,
            x="market_name",
            y="avg_price",
            color="avg_price",
            color_continuous_scale="YlOrRd",
            labels={"market_name": "市場", "avg_price": "均價（元/公斤）"},
            title=f"{latest_date.strftime('%Y-%m-%d')} 各市場均價",
        )
        fig_bar.update_layout(showlegend=False, coloraxis_showscale=False)
        st.plotly_chart(fig_bar, use_container_width=True)

    with col2:
        st.metric("最高均價市場", today_df.iloc[0]["market_name"],
                  f"{today_df.iloc[0]['avg_price']:.1f} 元/公斤")
        st.metric("最低均價市場", today_df.iloc[-1]["market_name"],
                  f"{today_df.iloc[-1]['avg_price']:.1f} 元/公斤")
        avg_all = today_df["avg_price"].mean()
        st.metric("全市場平均", f"{avg_all:.1f} 元/公斤")
else:
    st.warning("尚無最新資料，請等候每日自動更新。")

st.divider()

# ── 區塊 2：近 N 天各市場價格波動折線圖 ──────────────────────────────────────
st.subheader(f"📈 近 {days_range} 天各市場價格走勢")

recent = filtered[filtered["date"] >= (latest_date - timedelta(days=days_range))]

fig_line = px.line(
    recent,
    x="date",
    y="avg_price",
    color="market_name",
    markers=True,
    labels={"date": "日期", "avg_price": "均價（元/公斤）", "market_name": "市場"},
)
# 跳過休市日（週一），讓折線不出現週末到週二的視覺斷點
fig_line.update_xaxes(
    rangebreaks=[dict(bounds=["mon", "tue"])]  # 跳過每週一
)
st.plotly_chart(fig_line, use_container_width=True)

st.divider()

# ── 區塊 3：各市場箱形圖（整體分布比較）─────────────────────────────────────
st.subheader(f"📦 各市場價格分布比較（近 {days_range} 天）")

fig_box = px.box(
    recent,
    x="market_name",
    y="avg_price",
    color="market_name",
    labels={"market_name": "市場", "avg_price": "均價（元/公斤）"},
    points="all",   # 顯示所有資料點
)
fig_box.update_layout(showlegend=False)
st.plotly_chart(fig_box, use_container_width=True)

st.divider()

# ── 原始資料表 ─────────────────────────────────────────────────────────────────
with st.expander("🗂 查看原始資料"):
    st.dataframe(filtered.sort_values("date", ascending=False), use_container_width=True)
```

### 4-2 Streamlit Secrets 設定

在部署前，於 Streamlit Community Cloud 的 **App Settings → Secrets** 加入：

```toml
DATABASE_URL = "postgresql://postgres:[密碼]@db.[ref].supabase.co:5432/postgres"
```

---

## Phase 5：部署與文件撰寫（約 30 分鐘）

### 5-1 `requirements.txt`

```
streamlit>=1.35.0
pandas>=2.0.0
plotly>=5.20.0
requests>=2.31.0
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.9
```

### 5-2 部署步驟

1. 將所有檔案推送至 GitHub（`git push origin main`）
2. 前往 [https://share.streamlit.io/](https://share.streamlit.io/) 登入
3. 點選 **New app** → 選擇你的 Repo → 主程式設為 `app.py`
4. 展開 **Advanced settings** → 貼上 Secrets（`DATABASE_URL`）
5. 點選 **Deploy**，約 1 分鐘後取得公開 HTTPS URL ✅

### 5-3 手動觸發 ETL（首次執行）

部署後先手動跑一次 ETL 填入初始資料：  
GitHub Repo → **Actions** → 選 `Daily Fruit Price ETL` → **Run workflow**

---

## 休市日處理邏輯說明

| 策略 | 說明 | 本專案選擇 |
|------|------|-----------|
| **不補值** | 只儲存實際交易日，休市日不寫入 DB | ✅ **採用** |
| `ffill()` 向前填補 | 休市日沿用前一交易日價格 | ❌ 可能誤導 |
| `bfill()` 向後填補 | 休市日使用後一交易日價格 | ❌ 使用未來資料 |

**前端對應**：Plotly 的 `rangebreaks` 功能設定跳過週一，折線圖視覺上不會出現「跳空」斷線，且不需要人工填補假資料。

---

## 評分對應說明

| 評分項目 | 對應實作 | 預期得分 |
|----------|----------|----------|
| **Data Pipeline / ETL** | 農業部 API 串接、pandas 清洗、`ON CONFLICT` 冪等寫入、多作物支援 | 3–4 分 |
| **Visualization** | Bar chart（最新均價）、多線折線圖（走勢）、Box plot（分布比較）| 2–3 分 |
| **Data refresh mechanism** | GitHub Actions Cron Job（每日 08:00）+ `@st.cache_data(ttl=600)` | 1–2 分 |
| **Communicate** | 本 Executive Summary + 架構圖 | 1–2 分 |

---

## 開發時程建議（3 小時）

| 時段 | 工作 |
|------|------|
| 0:00–0:20 | Phase 1：Supabase 建表、取得連線字串 |
| 0:20–1:20 | Phase 2：本地跑通 `etl_pipeline.py`，確認資料入庫 |
| 1:20–1:40 | Phase 3：推上 GitHub、設 Secrets、測試 Actions |
| 1:40–2:30 | Phase 4：撰寫並本地測試 `app.py` |
| 2:30–2:50 | Phase 5：Streamlit 部署，取得 URL |
| 2:50–3:00 | 檢查儀表板、截圖、確認 URL 可公開存取 |
