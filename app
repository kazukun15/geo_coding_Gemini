import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
from datetime import datetime
import pandas as pd
import googlemaps
from googlemaps.exceptions import ApiError, HTTPError, Timeout, TransportError
import chardet  # chardetをインポート

# Gemini APIを利用するためのインポートとクライアント初期化
from google import genai
gemini_client = genai.Client(api_key="YOUR_GEMINI_API_KEY")

def select_input_file():
    file_path.set(filedialog.askopenfilename())

def select_output_folder():
    output_folder.set(filedialog.askdirectory())

def detect_encoding(file_path):
    with open(file_path, 'rb') as file:
        result = chardet.detect(file.read(100000))  # 最初の100KBを読み込んでエンコーディングを検出
        return result['encoding']

def correct_address_with_gemini(address):
    """
    Gemini APIを利用して住所を正確なフォーマットに補正する関数
    """
    prompt = f"以下の住所を正確な住所フォーマットに修正してください: {address}"
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash", 
            contents=prompt
        )
        corrected = response.text.strip()
        # 補正結果が得られなかった場合は元の住所を返す
        return corrected if corrected else address
    except Exception as e:
        print(f"Gemini API エラー: {e}")
        return address

def perform_geocoding():
    if not file_path.get() or not output_folder.get():
        messagebox.showwarning("Warning", "Input file and output folder are required.")
        return

    messagebox.showinfo("Info", "ジオコーディングを開始します。")

    try:
        encoding = detect_encoding(file_path.get())  # ファイルのエンコーディングを検出
        df = pd.read_csv(file_path.get(), encoding=encoding)
        # Google Maps APIクライアントの初期化（実際のAPIキーに置き換えてください）
        gmaps = googlemaps.Client(key='AIzaSyAi_Wc2ZSNKqerY0l4NnoLYeJTYRjYbL_A')

        df['latitude'] = None
        df['longitude'] = None

        progress_bar['maximum'] = len(df)
        progress_bar['value'] = 0

        for index, row in df.iterrows():
            try:
                original_address = row['address']
                # Gemini APIを使って住所を補正する
                corrected_address = correct_address_with_gemini(original_address)
                print(f"Original: {original_address} -> Corrected: {corrected_address}")

                # 補正後の住所を使ってジオコーディング（例: 日本に限定）
                geocode_result = gmaps.geocode(corrected_address, components={'country': 'JP'})
                if geocode_result:
                    # location_typeがROOFTOPの結果を優先
                    rooftop_results = [result for result in geocode_result if result['geometry']['location_type'] == 'ROOFTOP']
                    if rooftop_results:
                        location = rooftop_results[0]['geometry']['location']
                    else:
                        location = geocode_result[0]['geometry']['location']
                    
                    df.at[index, 'latitude'] = location['lat']
                    df.at[index, 'longitude'] = location['lng']
            except (ApiError, HTTPError, Timeout, TransportError) as e:
                print(f"Error at row {index}: {e}")

            progress_bar['value'] += 1
            root.update_idletasks()

        base_path = os.path.join(output_folder.get(), 'geocoded_results')
        ext = '.csv'
        output_path = base_path + ext
        if os.path.exists(output_path):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"{base_path}_{timestamp}{ext}"

        df.to_csv(output_path, index=False, encoding='utf-8-sig')
        messagebox.showinfo("Info", f"ジオコーディングが完了しました。ファイルは {output_path} に保存されました。")
    except Exception as e:
        messagebox.showerror("Error", str(e))

root = tk.Tk()
root.title("Geocoding Application")

file_path = tk.StringVar()
output_folder = tk.StringVar()

tk.Button(root, text="Select Input File", command=select_input_file).pack(pady=5)
tk.Button(root, text="Select Output Folder", command=select_output_folder).pack(pady=5)
tk.Button(root, text="Start Geocoding", command=perform_geocoding).pack(pady=5)

progress_bar = ttk.Progressbar(root, orient="horizontal", length=300, mode="determinate")
progress_bar.pack(pady=5)

root.mainloop()
