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