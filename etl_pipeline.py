import urllib3
import pandas as pd
import requests
import os
from datetime import date, timedelta
from sqlalchemy import create_engine, text

# 停用 SSL 警告 (避免雲端環境憑證報錯)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── 設定 ─────────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ["DATABASE_URL"]

# 作物代碼
CROPS = {
    "A1": "香蕉",
    "T1": "西瓜",
    "J1": "玉荷包"
}

# 對接 API
API_URL = "https://data.moa.gov.tw/Service/OpenData/FromM/FarmTransData.aspx"

# ── Step 1: 抓取資料 ──────────────────────────────────────────────────────────
def fetch_crop_data(crop_code: str, start_date: date, end_date: date) -> pd.DataFrame:
    """呼叫農業部 API，回傳原始 DataFrame"""

    # 將西元日期轉換為 API 需要的民國年格式 (如 2026-05-23 轉為 115.05.23)
    start_tw = f"{start_date.year - 1911}.{start_date.strftime('%m.%d')}"
    end_tw = f"{end_date.year - 1911}.{end_date.strftime('%m.%d')}"

    params = {
        "StartDate": start_tw, # 開始日期
        "EndDate": end_tw,     # 結束日期
        "CropCode": crop_code, # 作物代碼
        "TcType": "N05"        # 種類代碼: 水果
    }
    
    response = requests.get(API_URL, params=params, timeout=30, verify=False)
    response.raise_for_status()
    data = response.json()

    if not data:
        print(f"  [警告] {crop_code} 在 {start_tw}~{end_tw} 無資料")
        return pd.DataFrame()

    return pd.DataFrame(data)

# ── Step 2: 資料清洗（Data Wrangling）────────────────────────────────────────
def clean_data(df: pd.DataFrame, crop_code: str, crop_name: str) -> pd.DataFrame:
    if df.empty:
        return df

    rename_map = {
        "交易日期": "date",
        "市場名稱": "market_name",
        "平均價":   "avg_price",
        "交易量":   "trade_volume",
    }

    df = df.rename(columns=rename_map)

    needed = ["date", "market_name", "avg_price", "trade_volume"]
    df = df[[c for c in needed if c in df.columns]].copy()

    # --- 關鍵修正：將 API 傳回的民國年 (115.05.23) 轉回西元年 (2026-05-23) 存入資料庫 ---
    def convert_tw_date(tw_date_str):
        parts = str(tw_date_str).split('.')
        if len(parts) == 3:
            return pd.to_datetime(f"{int(parts[0]) + 1911}-{parts[1]}-{parts[2]}").date()
        return None

    df["date"] = df["date"].apply(convert_tw_date)
    # -------------------------------------------------------------

    df["avg_price"]    = pd.to_numeric(df["avg_price"],    errors="coerce")
    df["trade_volume"] = pd.to_numeric(df["trade_volume"], errors="coerce")

    df["crop_code"] = crop_code
    df["crop_name"] = crop_name

    df = df[df["avg_price"] > 0].copy()

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
    # 抓取「前 30 天」的資料（確保補齊任何遺漏）
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