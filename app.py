"""
app.py
功能：從 Supabase 讀取水果批發價資料，呈現互動式儀表板
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
    # 這裡會從 Streamlit Community Cloud 的 Secrets 或本機環境變數讀取
    # 如果在本機測試，請確保有設定 DATABASE_URL 環境變數
    db_url = st.secrets["DATABASE_URL"] if "DATABASE_URL" in st.secrets else os.environ.get("DATABASE_URL")
    
    if not db_url:
         st.error("找不到資料庫連線字串 (DATABASE_URL)。請確認 Secrets 已經設定。")
         st.stop()

    engine = create_engine(db_url)
    since = date.today() - timedelta(days=days)
    
    # 簡單的 SQL 查詢
    query = f"""
        SELECT date, market_name, crop_name, avg_price, trade_volume
        FROM   crop_price
        WHERE  date >= '{since}'
        ORDER  BY date DESC
    """
    df = pd.read_sql(query, engine)
    df["date"] = pd.to_datetime(df["date"])
    return df

# 先嘗試載入資料
try:
    df = load_data(days=30)
except Exception as e:
    st.error(f"載入資料失敗，請檢查資料庫連線: {e}")
    st.stop()

if df.empty:
    st.warning("目前資料庫中沒有過去 30 天的資料，請確認 ETL 排程是否有成功執行。")
    st.stop()

# ── 側邊欄篩選器 ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("🔍 篩選條件")
    
    # 水果選擇
    crop_options = df["crop_name"].unique().tolist()
    selected_crop = st.selectbox("選擇水果", crop_options)

    # 市場選擇 (根據選擇的水果過濾可選市場)
    market_options = df[df["crop_name"] == selected_crop]["market_name"].unique().tolist()
    
    # 預設選取前 5 個市場，如果不足 5 個則全選
    default_markets = market_options[:5] if len(market_options) > 5 else market_options
    
    selected_markets = st.multiselect(
        "選擇市場（可複選）",
        market_options,
        default=default_markets,
    )

    # 天數選擇
    days_range = st.slider("顯示天數 (影響折線圖與箱型圖)", min_value=7, max_value=30, value=14)

# ── 過濾資料 ──────────────────────────────────────────────────────────────────
# 根據使用者的選擇過濾 DataFrame
filtered = df[
    (df["crop_name"] == selected_crop) &
    (df["market_name"].isin(selected_markets))
].copy()

if filtered.empty:
    st.warning("您選擇的條件組合下沒有資料，請調整篩選器。")
    st.stop()

# ── 區塊 1：今日各市場價格（最新一天）────────────────────────────────────────
st.subheader(f"📊 {selected_crop} 最新交易日各市場均價")

latest_date = filtered["date"].max()
# 篩選最新一天的資料，並依照價格排序
today_df = filtered[filtered["date"] == latest_date].sort_values("avg_price", ascending=False)

if not today_df.empty:
    col1, col2 = st.columns([2, 1])
    
    with col1:
        # 使用 Plotly Express 畫長條圖
        fig_bar = px.bar(
            today_df,
            x="market_name",
            y="avg_price",
            color="avg_price",
            color_continuous_scale="YlOrRd", # 使用暖色系代表價格
            labels={"market_name": "市場", "avg_price": "均價（元/公斤）"},
            title=f"{latest_date.strftime('%Y-%m-%d')} 各市場均價比較",
        )
        # 隱藏圖例和顏色條，讓圖表更簡潔
        fig_bar.update_layout(showlegend=False, coloraxis_showscale=False)
        st.plotly_chart(fig_bar, use_container_width=True)

    with col2:
        # 顯示重點 Metrics 卡片
        st.metric("最高均價市場", today_df.iloc[0]["market_name"],
                  f"{today_df.iloc[0]['avg_price']:.1f} 元/公斤")
        st.metric("最低均價市場", today_df.iloc[-1]["market_name"],
                  f"{today_df.iloc[-1]['avg_price']:.1f} 元/公斤")
        avg_all = today_df["avg_price"].mean()
        st.metric("全選取市場平均", f"{avg_all:.1f} 元/公斤")
else:
    st.info("尚無最新資料，或您選擇的市場在最新一天休市。")

st.divider()

# ── 區塊 2：近 N 天各市場價格波動折線圖 ──────────────────────────────────────
st.subheader(f"📈 近 {days_range} 天價格走勢")

# 根據 Slider 篩選近期資料
recent = filtered[filtered["date"] >= (pd.to_datetime('today') - pd.Timedelta(days=days_range))]

if not recent.empty:
    # 雙軸圖設定比較複雜，我們改用 Plotly Graph Objects (go) 來精細控制
    fig_line = go.Figure()

    # 為每個市場畫一條折線
    for market in selected_markets:
        market_data = recent[recent["market_name"] == market].sort_values('date')
        
        # 折線圖 (主 Y 軸)
        fig_line.add_trace(go.Scatter(
            x=market_data["date"],
            y=market_data["avg_price"],
            mode='lines+markers',
            name=f"{market} (價格)",
            hovertemplate="日期: %{x}<br>價格: %{y:.1f} 元<extra></extra>"
        ))
        
        # 長條圖 (副 Y 軸，透明度調低當背景)
        fig_line.add_trace(go.Bar(
            x=market_data["date"],
            y=market_data["trade_volume"],
            name=f"{market} (交易量)",
            opacity=0.2, # 淡淡的背景
            yaxis="y2",
            hovertemplate="日期: %{x}<br>交易量: %{y:,.0f} 公斤<extra></extra>"
        ))

    # 更新版面配置，加入雙軸
    fig_line.update_layout(
        xaxis=dict(
            title="日期",
            rangebreaks=[dict(bounds=["sat", "mon"])] # 嘗試跳過週末，如果你的資料只有週一休市，改為 ["mon", "tue"]
        ),
        yaxis=dict(
            title="均價 (元/公斤)",
            side="left"
        ),
        yaxis2=dict(
            title="交易量 (公斤)",
            side="right",
            overlaying="y", # 疊加在同一個圖表上
            showgrid=False  # 隱藏副軸的格線避免雜亂
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified" # 滑鼠游標移過去時，顯示同一天所有數據
    )
    
    st.plotly_chart(fig_line, use_container_width=True)
else:
    st.info("在選定的天數內沒有足夠的資料繪製趨勢圖。")

st.divider()

# ── 區塊 3：各市場箱形圖（整體分布比較）─────────────────────────────────────
st.subheader(f"📦 各市場價格分布比較（近 {days_range} 天）")

if not recent.empty:
    fig_box = px.box(
        recent,
        x="market_name",
        y="avg_price",
        color="market_name",
        labels={"market_name": "市場", "avg_price": "均價（元/公斤）"},
        points="all",   # 顯示所有資料點，方便看極端值
        title=f"各市場價格波動範圍 (過去 {days_range} 天)"
    )
    fig_box.update_layout(showlegend=False)
    st.plotly_chart(fig_box, use_container_width=True)

st.divider()

# ── 原始資料表 ─────────────────────────────────────────────────────────────────
with st.expander("🗂 查看過濾後的原始資料"):
    st.dataframe(filtered.sort_values("date", ascending=False), use_container_width=True)