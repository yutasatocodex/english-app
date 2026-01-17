import streamlit as st
import pandas as pd
from pypdf import PdfReader
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from st_click_detector import click_detector
import html
from openai import OpenAI

# --- 設定1: Googleスプレッドシート連携 ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

def get_gspread_client():
    # Secretsから情報を取得
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

# --- 設定2: OpenAI (ChatGPT) 翻訳機能 ---
def translate_with_gpt(text):
    # SecretsからOpenAIの鍵を取得
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    
    # 翻訳の実行
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a professional translator. Translate the following English word or phrase into Japanese directly. Output ONLY the Japanese meaning. No explanations."},
            {"role": "user", "content": text}
        ]
    )
    return response.choices[0].message.content.strip()

# --- セッション状態の初期化 ---
if 'last_clicked' not in st.session_state:
    st.session_state.last_clicked = ""

# --- アプリのメイン画面 ---
st.title("🤖 AI English PDF Note (Final)")

# サイドバー: PDFアップロード
st.sidebar.header("1. Upload PDF")
uploaded_file = st.sidebar.file_uploader("Choose a PDF file", type="pdf")

if uploaded_file is not None:
    # PDF読み込み
    reader = PdfReader(uploaded_file)
    total_pages = len(reader.pages)
    page_num = st.sidebar.number_input(f"Page (1-{total_pages})", min_value=1, max_value=total_pages, value=1)
    
    # テキスト抽出
    page = reader.pages[page_num - 1]
    raw_text = page.extract_text()
    
    if raw_text:
        st.subheader("📖 Tap a word to AI Translate")
        
        # HTML化処理
        safe_text = html.escape(raw_text)
        words = safe_text.split()
        
        html_content = """
        <style>
            .word-link { color: #333; text-decoration: none; cursor: pointer; }
            .word-link:hover { color: #e04400; text-decoration: underline; background-color: #f0f0f0;}
        </style>
        <div style='font-size: 16px; line-height: 1.8; padding: 10px; border: 1px solid #ddd; border-radius: 5px;'>
        """
        for word in words:
            # 単語をリンク化
            html_content += f"<a href='#' id='{word}' class='word-link'>{word}</a> "
        
        html_content += "</div>"

        # クリック検知
        clicked_word = click_detector(html_content)

        # クリック時の処理
        if clicked_word and clicked_word != st.session_state.last_clicked:
            st.session_state.last_clicked = clicked_word
            clean_word = clicked_word.strip(".,!?\"'()[]")
            
            if clean_word:
                with st.spinner(f"🤖 AI Translating '{clean_word}'..."):
                    try:
                        # 1. ChatGPTで翻訳 (ここが新しい箇所！)
                        translated_text = translate_with_gpt(clean_word)
                        
                        # 2. スプレッドシート保存
                        client = get_gspread_client()
                        sheet_name = st.secrets["sheet_config"]["sheet_name"]
                        sheet = client.open(sheet_name).sheet1
                        
                        date_str = datetime.now().strftime("%Y-%m-%d")
                        row = [clean_word, translated_text, date_str]
                        sheet.append_row(row)
                        
                        # 成功メッセージ
                        st.toast(f"✅ Saved: {clean_word} = {translated_text}", icon="🎉")
                        st.info(f"**{clean_word}**: {translated_text}")
                        
                    except Exception as e:
                        # エラー詳細を表示
                        st.error(f"Error details: {e}")
    else:
        st.warning("Could not extract text from this page.")

else:
    st.info("👈 Please upload a PDF from the sidebar.")
