import streamlit as st
import pandas as pd
import googlemaps
from googlemaps.exceptions import ApiError, HTTPError, Timeout, TransportError
import chardet
import io
import json
from datetime import datetime
import os
import google.generativeai as genai

# Gemini APIの設定（Streamlit Secretsからキーを取得）
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

# ----- リクエストカウンタの永続化処理 -----
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

# ----- ファイルアップロード・エンコーディング検出 -----
def detect_encoding(file_bytes):
    """アップロードされたファイルのエンコーディングを検出"""
    result = chardet.detect(file_bytes[:100000])
    return result['encoding']

# ----- Gemini APIによる住所補正 -----
def correct_address_with_gemini(address):
    """
    Gemini APIを利用して住所を正確な住所フォーマットに補正する関数
    """
    prompt = f"以下の住所を正確な住所フォーマットに修正してください: {address}"
    try:
        response = genai.models.generate_content(
            model="gemini-2.0-flash", 
            contents=prompt
        )
        if hasattr(response, "text"):
            corrected = response.text.strip()
        else:
            corrected = response.get("text", "").strip()
        return corrected if corrected else address
    except Exception as e:
        st.error(f"Gemini API 補正エラー: {e}")
        return address

# ----- Gemini APIによる座標精度向上 -----
def refine_coordinate_with_gemini(original_address, corrected_address, current_lat, current_lng):
    """
    Gemini APIを利用して、与えられた住所情報と現在のジオコーディング結果から、
    より正確な緯度経度を提案する関数。
    出力はJSON形式で、例: {"lat": 35.6895, "lng": 139.6917} とするように指示する。
    """
    prompt = (
        "以下の情報に基づいて、より正確な緯度と経度をJSON形式で返してください。\n"
        f"・元の住所: {original_address}\n"
        f"・Geminiで補正した住所: {corrected_address}\n"
        f"・現在の結果: 緯度 {current_lat}, 経度 {current_lng}\n"
        "出力は以下のようにしてください: {\"lat\": 数値, \"lng\": 数値}"
    )
    try:
        response = genai.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        if hasattr(response, "text"):
            text = response.text.strip()
        else:
            text = response.get("text", "").strip()
        refined = json.loads(text)
        if "lat" in refined and "lng" in refined:
            return refined["lat"], refined["lng"]
        else:
            st.warning("Geminiからの応答に期待するキーがありません。")
            return current_lat, current_lng
    except Exception as e:
        st.error(f"Geminiによる座標精度向上処理でエラー: {e}")
        return current_lat, current_lng

# ----- ジオコーディング実行 -----
def perform_geocoding(df):
    """
    DataFrame内の各住所に対して、Geminiで住所補正および座標精度向上を行い、
    Google Maps APIを用いてジオコーディングを実行する。
    月間のリクエスト上限（9800件）を超えないよう、ローカルファイルで管理する。
    """
    gmaps = googlemaps.Client(key=st.secrets["GOOGLE_MAPS_API_KEY"])
    df['latitude'] = None
    df['longitude'] = None
    progress_bar = st.progress(0)
    total = len(df)
    counter_data = load_request_count()
    monthly_count = counter_data["count"]

    for index, row in df.iterrows():
        if monthly_count >= REQUEST_LIMIT:
            st.warning("月間ジオコーディングリクエスト上限（9800件）に達しました。")
            break
        try:
            original_address = row['address']
            corrected_address = correct_address_with_gemini(original_address)
            st.write(f"Original: {original_address} → Corrected: {corrected_address}")
            geocode_result = gmaps.geocode(corrected_address, components={'country': 'JP'})
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
                refined_lat, refined_lng = refine_coordinate_with_gemini(original_address, corrected_address, current_lat, current_lng)
                df.at[index, 'latitude'] = refined_lat
                df.at[index, 'longitude'] = refined_lng
            else:
                st.warning(f"住所 '{corrected_address}' のジオコーディング結果が見つかりませんでした。")
        except (ApiError, HTTPError, Timeout, TransportError) as e:
            st.error(f"Error at row {index}: {e}")
        progress_bar.progress((index + 1) / total)
    st.write(f"現在の月間リクエスト総数: {monthly_count}件")
    return df

# ----- メイン処理 -----
def main():
    st.title("ジオコーディングアプリケーション（Gemini APIで精度向上＋月間リクエスト制限）")
    st.write("Google Maps APIとGemini APIを組み合わせた住所補正・ジオコーディングを実行します。")
    st.write("※月間ジオコーディングリクエストは9800件に制限されています。")
    uploaded_file = st.file_uploader("CSVファイルをアップロードしてください（必ず 'address' カラムが必要）", type=["csv"])
    if uploaded_file is not None:
        file_bytes = uploaded_file.read()
        encoding = detect_encoding(file_bytes)
        df = pd.read_csv(io.StringIO(file_bytes.decode(encoding)))
        st.write("アップロードされたデータ:")
        st.dataframe(df.head())
        if st.button("ジオコーディング開始"):
            with st.spinner("ジオコーディング実行中..."):
                result_df = perform_geocoding(df)
                st.success("ジオコーディングが完了しました。")
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
