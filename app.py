import streamlit as st
import pandas as pd
import googlemaps
from googlemaps.exceptions import ApiError, HTTPError, Timeout, TransportError
import chardet
import io
import json
from datetime import datetime
import os
import time
import google.generativeai as genai

# --- Gemini API の設定 ---
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
# モデルの初期化（例: gemini-pro）
model = genai.GenerativeModel('gemini-pro')

# --- リクエストカウンタの永続化 ---
REQUEST_COUNT_FILE = "request_count.json"
REQUEST_LIMIT = 9800

def load_request_count():
    """ローカルファイルから月間リクエスト数を読み込み、現在の月でなければリセットする"""
    current_month = datetime.now().strftime("%Y-%m")
    if os.path.exists(REQUEST_COUNT_FILE):
        try:
            with open(REQUEST_COUNT_FILE, "r") as f:
                data = json.load(f)
            if data.get("month") != current_month:
                data = {"month": current_month, "count": 0}
        except Exception:
            data = {"month": current_month, "count": 0}
    else:
        data = {"month": current_month, "count": 0}
    return data

def save_request_count(data):
    """ローカルファイルに月間リクエスト数を保存する"""
    with open(REQUEST_COUNT_FILE, "w") as f:
        json.dump(data, f)

# --- ファイルアップロード時のエンコーディング検出 ---
def detect_encoding(file_bytes):
    result = chardet.detect(file_bytes[:100000])
    return result['encoding']

# --- Gemini API による住所補正 ---
def correct_address_with_gemini(model, address):
    prompt = f"以下の住所を正確な住所フォーマットに修正してください: {address}"
    try:
        response = model.generate_content(prompt)
        corrected = response.text.strip() if response and hasattr(response, "text") else ""
        return corrected if corrected else address
    except genai.types.generation_types.GenerateContentError as e:
        st.error(f"Gemini API 補正エラー: {e.message}")
        return address
    except Exception as e:
        st.error(f"Gemini API 補正エラー: {e}")
        return address

# --- Gemini API による座標精度向上 ---
def refine_coordinates(model, original_address, corrected_address, current_lat, current_lng):
    prompt = (
        "以下の情報に基づいて、より正確な緯度と経度をJSON形式で返してください。\n"
        f"・元の住所: {original_address}\n"
        f"・Geminiで補正した住所: {corrected_address}\n"
        f"・現在の結果: 緯度 {current_lat}, 経度 {current_lng}\n"
        "出力は以下の形式にしてください: {\"lat\": 数値, \"lng\": 数値}"
    )
    try:
        response = model.generate_content(prompt)
        text = response.text.strip() if response and hasattr(response, "text") else ""
        refined = json.loads(text)
        if "lat" in refined and "lng" in refined:
            return refined["lat"], refined["lng"]
        else:
            st.warning("Gemini からの応答に期待するキーがありません。")
            return current_lat, current_lng
    except genai.types.generation_types.GenerateContentError as e:
        st.error(f"Gemini API 座標精度向上エラー: {e.message}")
        return current_lat, current_lng
    except Exception as e:
        st.error(f"Gemini API 座標精度向上エラー: {e}")
        return current_lat, current_lng

# --- Google Maps API を用いたジオコーディング ---
def geocode_address(gmaps, address):
    try:
        result = gmaps.geocode(address, components={'country': 'JP'})
        return result
    except ApiError as e:
        st.error(f"Google Maps API エラー: {e}")
        return None

# --- メインジオコーディング処理 ---
def perform_geocoding(df):
    # Google Maps API クライアントの初期化
    gmaps = googlemaps.Client(key=st.secrets["GOOGLE_MAPS_API_KEY"])
    df['latitude'] = None
    df['longitude'] = None
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(df)
    counter_data = load_request_count()
    monthly_count = counter_data["count"]

    # 成功/失敗のカウントと処理時間計測
    success_count = 0
    fail_count = 0
    start_time = time.time()

    for index, row in df.iterrows():
        if monthly_count >= REQUEST_LIMIT:
            st.warning("月間ジオコーディングリクエスト上限（9800件）に達しました。")
            break

        original_address = row['address']
        corrected_address = correct_address_with_gemini(model, original_address)
        status_text.text(f"処理中: {index+1}/{total} 件 - {original_address} → {corrected_address}")

        geocode_result = geocode_address(gmaps, corrected_address)
        monthly_count += 1
        counter_data["count"] = monthly_count
        save_request_count(counter_data)

        if geocode_result:
            rooftop_results = [result for result in geocode_result if result['geometry']['location_type'] == 'ROOFTOP']
            if rooftop_results:
                location = rooftop_results[0]['geometry']['location']
            else:
                location = geocode_result[0]['geometry']['location']
            current_lat = location['lat']
            current_lng = location['lng']
            refined_lat, refined_lng = refine_coordinates(model, original_address, corrected_address, current_lat, current_lng)
            df.at[index, 'latitude'] = refined_lat
            df.at[index, 'longitude'] = refined_lng
            success_count += 1
        else:
            fail_count += 1

        progress_bar.progress((index + 1) / total)

    end_time = time.time()
    elapsed_time = end_time - start_time

    status_text.text("処理完了")
    st.write(f"処理時間: {elapsed_time:.2f}秒")
    st.write(f"成功件数: {success_count}件")
    st.write(f"失敗件数: {fail_count}件")
    st.write(f"現在の月間リクエスト総数: {monthly_count}件")
    return df

# --- メイン処理 ---
def main():
    st.title("ジオコーディングアプリケーション")
    st.markdown("**Google Maps API** と **Gemini API** を組み合わせた住所補正・ジオコーディングアプリです。")
    st.sidebar.title("使い方・設定")
    st.sidebar.info(
        """
        1. **CSVファイル** をアップロードしてください。（必ず **address** カラムが必要です）  
        2. **ジオコーディング開始** ボタンを押すと処理が実行されます。  
        3. 月間リクエスト上限は **9800件** に設定されています。  
        4. 結果は画面上に表示され、CSV ダウンロードも可能です。
        """
    )
    uploaded_file = st.file_uploader("CSVファイルをアップロードしてください", type=["csv"])
    if uploaded_file is not None:
        file_bytes = uploaded_file.read()
        encoding = detect_encoding(file_bytes)
        df = pd.read_csv(io.StringIO(file_bytes.decode(encoding)))
        st.subheader("アップロードされたデータ")
        st.dataframe(df.head())
        if st.button("ジオコーディング開始"):
            with st.spinner("ジオコーディング実行中..."):
                result_df = perform_geocoding(df)
                st.success("ジオコーディングが完了しました。")
                st.subheader("結果")
                st.dataframe(result_df)
                csv = result_df.to_csv(index=False, encoding='utf-8-sig')
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"geocoded_results_{timestamp}.csv"
                st.download_button(
                    label="結果CSVをダウンロード",
                    data=csv,
                    file_name=filename,
                    mime='text/csv'
                )

if __name__ == "__main__":
    main()
