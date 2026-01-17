import streamlit as st
import pandas as pd
from pypdf import PdfReader
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from st_click_detector import click_detector
import html
import traceback
import re
from openai import OpenAI

# --- 設定: ページ設定（ワイド表示） ---
st.set_page_config(layout="wide")

# --- 設定1: Googleスプレッドシート連携 ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

def get_gspread_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

# --- 設定2: OpenAI (ChatGPT) 辞書機能 ---
def translate_with_gpt(text: str) -> dict:
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    # プロンプトを強化：品詞や他の意味も取得する
    prompt = (
        f"Explain the English word '{text}' for a Japanese learner.\n"
        "Output format must be exactly like this (3 lines):\n"
        "JAPANESE_MEANING: (The most common Japanese meaning)\n"
        "POS: (Part of Speech, e.g., Verb, Noun)\n"
        "DETAILS: (Other meanings, synonyms, or a brief nuance explanation in Japanese)"
    )
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful English-Japanese dictionary AI."},
            {"role": "user", "content": prompt},
        ],
    )
    raw_content = response.choices[0].message.content.strip()
    
    # 結果を解析して辞書にする
    result = {"meaning": "???", "pos": "", "details": ""}
    for line in raw_content.split('\n'):
        if line.startswith("JAPANESE_MEANING:"):
            result["meaning"] = line.replace("JAPANESE_MEANING:", "").strip()
        elif line.startswith("POS:"):
            result["pos"] = line.replace("POS:", "").strip()
        elif line.startswith("DETAILS:"):
            result["details"] = line.replace("DETAILS:", "").strip()
            
    # 解析失敗時のフォールバック
    if result["meaning"] == "???":
        result["meaning"] = raw_content
        
    return result

# --- PDFテキスト整形関数（ここが重要！） ---
def clean_pdf_text(text):
    if not text:
        return ""
    # 1. ハイフネーション（行末の - ）をつなげる
    text = re.sub(r'-\n', '', text)
    # 2. 基本的な改行をスペースに置換（文章をつなげる）
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
    # 3. 連続する空白を1つにまとめる
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# --- セッション状態の初期化 ---
if "last_clicked_id" not in st.session_state:
    st.session_state.last_clicked_id = ""
if "clicked_ids" not in st.session_state:
    st.session_state.clicked_ids = set()
if "current_translation" not in st.session_state:
    st.session_state.current_translation = None

# --- アプリ画面構成 ---
st.title("🤖 AI English PDF Dictionary")

# 2カラムレイアウト（左：PDF操作、右：辞書結果）
col1, col2 = st.columns([2, 1])

with col1:
    st.markdown("### 📄 PDF Viewer")
    uploaded_file = st.file_uploader("Choose a PDF file", type="pdf")

if uploaded_file is not None:
    try:
        reader = PdfReader(uploaded_file)
        total_pages = len(reader.pages)

        # ページネーション（メインエリアに配置）
        with col1:
            page_num = st.number_input(
                f"Page (Total {total_pages})", min_value=1, max_value=total_pages, value=1, step=1
            )
            
            page = reader.pages[page_num - 1]
            raw_text = page.extract_text()

            if raw_text:
                # PDFテキストをきれいに整形（改行削除）
                clean_text = clean_pdf_text(raw_text)
                
                # HTML生成（単語ごとにリンク化）
                html_content = """
                <style>
                    .pdf-container {
                        font-family: 'Helvetica Neue', Arial, sans-serif;
                        background-color: #ffffff;
                        color: #222222;
                        padding: 25px;
                        border-radius: 8px;
                        border: 1px solid #e0e0e0;
                        font-size: 18px; /* 文字を少し大きく */
                        line-height: 1.8; /* 行間を広めに */
                        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
                        text-align: justify; /* 両端揃えで見やすく */
                    }
                    .word-link { 
                        color: #222222; 
                        text-decoration: none; 
                        cursor: pointer; 
                        padding: 0 2px;
                    }
                    .word-link:hover { 
                        background-color: #e3f2fd; 
                        color: #1565c0;
                        border-radius: 3px;
                    }
                    /* 翻訳済み単語（黄色マーカー） */
                    .highlighted {
                        background-color: #fff9c4; 
                        border-bottom: 2px solid #fbc02d;
                        color: #000000;
                    }
                </style>
                <div class='pdf-container'>
                """

                words = clean_text.split()
                # 単語リストを辞書化（クリック判定用）
                id_to_word = {}
                
                for i, w in enumerate(words):
                    current_id = f"w{i}"
                    id_to_word[current_id] = w
                    
                    # 記号を除去して表示用の単語を作る
                    safe_w = html.escape(w)
                    
                    # 既にクリックされた単語ならマーカークラスをつける
                    css_class = "word-link"
                    if current_id in st.session_state.clicked_ids:
                        css_class += " highlighted"
                    
                    html_content += f"<a href='#' id='{current_id}' class='{css_class}'>{safe_w}</a> "
                
                html_content += "</div>"

                # クリック検知
                clicked_id = click_detector(html_content)

                # --- クリック時の処理 ---
                if clicked_id and clicked_id != st.session_state.last_clicked_id:
                    st.session_state.last_clicked_id = clicked_id
                    st.session_state.clicked_ids.add(clicked_id) # マーカー用に記憶
                    
                    # 翻訳対象の単語を取得
                    if clicked_id in id_to_word:
                        target_word = id_to_word[clicked_id]
                        clean_word = target_word.strip(".,!?\"'()[]{}:;")
                        
                        if clean_word:
                            # OpenAIで辞書検索
                            # 暗転を防ぐため st.spinner は使わず、トースト通知だけ出す
                            st.toast(f"🔍 Searching: {clean_word}...", icon="🤖")
                            
                            try:
                                result = translate_with_gpt(clean_word)
                                
                                # 結果をセッションに保存（画面再描画用）
                                st.session_state.current_translation = {
                                    "word": clean_word,
                                    "result": result
                                }

                                # スプレッドシート保存（バックグラウンド的に実行）
                                client = get_gspread_client()
                                sheet_name = st.secrets["sheet_config"]["sheet_name"]
                                sheet = client.open(sheet_name).sheet1
                                date_str = datetime.now().strftime("%Y-%m-%d")
                                row = [clean_word, result["meaning"], date_str]
                                sheet.append_row(row)
                                
                            except Exception as e:
                                st.error(f"Error: {e}")
                    
                    # 画面更新（これでマーカーが反映される）
                    st.rerun()

            else:
                st.warning("No text extracted from this page.")
    except Exception as e:
        col1.error(f"Error reading PDF: {e}")

# --- 右カラム：辞書結果表示エリア（固定表示） ---
with col2:
    st.markdown("### 💡 Dictionary")
    
    current = st.session_state.current_translation
    if current:
        word = current["word"]
        res = current["result"]
        
        # 辞書カードのデザイン
        st.markdown(f"""
        <div style="padding: 20px; border: 2px solid #4CAF50; border-radius: 10px; background-color: #f9fff9;">
            <h2 style="color: #2e7d32; margin-top: 0;">{word}</h2>
            <p><b>{res['pos']}</b></p>
            <hr>
            <h3 style="color: #333;">{res['meaning']}</h3>
            <p style="color: #666; font-size: 0.9em;">{res['details']}</p>
        </div>
        """, unsafe_allow_html=True)
        
        st.caption("✅ Automatically saved to Spreadsheet")
    else:
        st.info("👈 Tap any word in the PDF to see the meaning here.")
