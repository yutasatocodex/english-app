import streamlit as st
import pandas as pd
from pypdf import PdfReader
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from st_click_detector import click_detector
import html
import traceback
from openai import OpenAI

# --- 設定1: Googleスプレッドシート連携 ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

def get_gspread_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

# --- 設定2: OpenAI (ChatGPT) 翻訳機能 ---
def translate_with_gpt(text: str) -> str:
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system", 
                "content": (
                    "You are a professional translator. Translate the following English word or phrase "
                    "into Japanese directly. Output ONLY the Japanese meaning."
                ),
            },
            {"role": "user", "content": text},
        ],
    )
    return response.choices[0].message.content.strip()

# --- セッション状態の初期化 ---
if "last_clicked_id" not in st.session_state:
    st.session_state.last_clicked_id = ""
if "clicked_ids" not in st.session_state:
    st.session_state.clicked_ids = set() # 翻訳済みの単語IDを保存する場所

st.title("🤖 AI English PDF Note (Final)")

st.sidebar.header("1. Upload PDF")
uploaded_file = st.sidebar.file_uploader("Choose a PDF file", type="pdf")

if uploaded_file is not None:
    try:
        reader = PdfReader(uploaded_file)
        total_pages = len(reader.pages)

        page_num = st.sidebar.number_input(
            "Page", min_value=1, max_value=total_pages, value=1, step=1
        )
        page = reader.pages[page_num - 1]
        raw_text = page.extract_text()

        if raw_text:
            st.subheader("📖 Tap a word to AI Translate")

            # --- HTML生成ロジックの改良 ---
            # 背景を白、文字を黒に固定して見やすくするCSS
            # 翻訳済み（highlighted）のデザインを追加
            html_content = """
            <style>
                .pdf-container {
                    background-color: #ffffff;
                    color: #222222;
                    padding: 20px;
                    border-radius: 8px;
                    border: 1px solid #ddd;
                    font-size: 16px;
                    line-height: 1.8;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                }
                .word-link { 
                    color: #222222; 
                    text-decoration: none; 
                    cursor: pointer; 
                    padding: 2px 1px;
                    border-radius: 3px;
                }
                .word-link:hover { 
                    background-color: #e0e0e0; 
                    text-decoration: underline;
                }
                /* 翻訳済み単語のスタイル（黄色いマーカー風） */
                .highlighted {
                    background-color: #fffacd; /* 薄い黄色 */
                    border-bottom: 2px solid #ffd700; /* 濃い黄色の下線 */
                    font-weight: bold;
                    color: #000000;
                }
            </style>
            <div class='pdf-container'>
            """

            # 改行を維持するために、行ごとに処理する
            lines = raw_text.splitlines()
            word_counter = 0 # 全体を通しての一意なID用
            
            # 後でクリック判定するために単語リストを再構築する辞書
            id_to_word = {}

            for line in lines:
                words_in_line = line.split()
                
                # 空行の場合は改行だけ入れてスキップ
                if not words_in_line:
                    html_content += "<br><br>"
                    continue

                for w in words_in_line:
                    safe_w = html.escape(w)
                    current_id = f"w{word_counter}"
                    id_to_word[current_id] = w
                    
                    # 既にクリックされた単語ならマーカークラスをつける
                    css_class = "word-link"
                    if current_id in st.session_state.clicked_ids:
                        css_class += " highlighted"
                    
                    html_content += f"<a href='#' id='{current_id}' class='{css_class}'>{safe_w}</a> "
                    word_counter += 1
                
                # 行の終わりに改行タグを追加
                html_content += "<br>"
            
            html_content += "</div>"

            # クリック検知
            clicked_id = click_detector(html_content)

            if clicked_id and clicked_id != st.session_state.last_clicked_id:
                st.session_state.last_clicked_id = clicked_id
                
                # 新しくクリックされたIDを記憶セットに追加
                st.session_state.clicked_ids.add(clicked_id)
                # 即座に画面を更新してマーカーを反映させる
                st.rerun()

            # 翻訳処理（リロード後も実行するためにIDチェックはここでも行う）
            if st.session_state.last_clicked_id in id_to_word:
                 target_word = id_to_word[st.session_state.last_clicked_id]
                 
                 clean_word = target_word.strip(".,!?\"'()[]{}:;")
                 
                 if clean_word:
                    # サイドバーなどに結果を表示（あるいはメインエリア下部）
                    st.divider()
                    st.markdown(f"### 🤖 Translating: **{clean_word}**")
                    
                    with st.spinner("Translating..."):
                        try:
                            # 翻訳実行
                            translated_text = translate_with_gpt(clean_word)
                            
                            # スプレッドシート保存
                            client = get_gspread_client()
                            sheet_name = st.secrets["sheet_config"]["sheet_name"]
                            sheet = client.open(sheet_name).sheet1
                            
                            date_str = datetime.now().strftime("%Y-%m-%d")
                            row = [clean_word, translated_text, date_str]
                            sheet.append_row(row)
                            
                            st.success(f"**意味:** {translated_text}")
                            st.caption(f"✅ Saved to {sheet_name}")

                        except Exception as e:
                            st.error(f"Error: {e}")
                            st.code(traceback.format_exc())

        else:
            st.warning("No text found.")
    except Exception as e:
        st.error(f"Error reading PDF: {e}")
else:
    st.info("👈 Upload PDF to start.")
