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

# --- セッションとメイン画面 ---
if "last_clicked_id" not in st.session_state:
    st.session_state.last_clicked_id = ""

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

            # 🔥 重要: idは安全な連番にして、表示テキストだけescapeする
            words = raw_text.split()

            html_content = """
            <style>
                .word-link { color: #333; text-decoration: none; cursor: pointer; }
                .word-link:hover { color: #e04400; text-decoration: underline; background-color: #f0f0f0;}
            </style>
            <div style='font-size: 16px; line-height: 1.8; padding: 10px; border: 1px solid #ddd; border-radius: 5px;'>
            """

            for i, w in enumerate(words):
                disp = html.escape(w)
                html_content += f"<a href='#' id='w{i}' class='word-link'>{disp}</a> "
            html_content += "</div>"

            clicked_id = click_detector(html_content)

            # クリックが安定するように「id」で判定
            if clicked_id and clicked_id != st.session_state.last_clicked_id:
                st.session_state.last_clicked_id = clicked_id

                if clicked_id.startswith("w"):
                    try:
                        idx = int(clicked_id[1:])
                        clicked_word = words[idx]
                    except Exception:
                        clicked_word = ""

                    clean_word = clicked_word.strip(".,!?\"'()[]{}:;")

                    if clean_word:
                        with st.spinner(f"🤖 AI Translating '{clean_word}'..."):
                            try:
                                translated_text = translate_with_gpt(clean_word)

                                client = get_gspread_client()
                                sheet_name = st.secrets["sheet_config"]["sheet_name"]
                                sheet = client.open(sheet_name).sheet1

                                date_str = datetime.now().strftime("%Y-%m-%d")
                                row = [clean_word, translated_text, date_str]
                                sheet.append_row(row)

                                st.toast(f"✅ Saved: {clean_word} = {translated_text}", icon="🎉")
                                st.info(f"**{clean_word}**: {translated_text}")

                            except Exception as e:
                                # ✅ ここが「<Response [200]>」問題を潰す本体
                                st.error(f"{type(e).__name__}: {e!r}")
                                st.code(traceback.format_exc())

                                # 例外オブジェクトがResponseっぽいときは中身を出す
                                if hasattr(e, "status_code") and hasattr(e, "text"):
                                    st.write("status:", getattr(e, "status_code", None))
                                    st.code(getattr(e, "text", "")[:3000])

                                # Streamlit標準の例外表示（便利）
                                st.exception(e)
                else:
                    st.warning("Clicked value was unexpected. (id format mismatch)")
        else:
            st.warning("No text found.")
    except Exception as e:
        st.error(f"{type(e).__name__}: {e!r}")
        st.code(traceback.format_exc())
        st.exception(e)
else:
    st.info("👈 Upload PDF to start.")
