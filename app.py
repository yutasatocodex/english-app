import streamlit as st
from pypdf import PdfReader
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from st_click_detector import click_detector
import html
import re
import json
from openai import OpenAI

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="AI Book Reader")

# --- 設定: Google連携 ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

def get_gspread_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

# --- 設定: OpenAI翻訳 (JSON) ---
def translate_word_with_gpt(text):
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    prompt = f"""
    You are an English-Japanese dictionary.
    Explain the word: "{text}".
    Output MUST be a JSON object with these keys:
    1. "meaning": Japanese meaning (short & clear).
    2. "pos": Part of Speech (e.g., Verb, Noun).
    3. "details": Synonyms or nuance explanation.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that outputs JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {"meaning": "Error", "pos": "-", "details": "Could not translate."}

# --- 📖 テキスト整形ロジック（見出し強調・自然な改行） ---
def format_text_smart(text):
    if not text: return ""
    
    lines = text.splitlines()
    formatted_blocks = []
    current_paragraph = ""
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        # 見出し判定（短くて、文末がピリオドじゃない、または数字/Chapterで始まる）
        is_header = (len(line) < 60 and not line.endswith(".")) or \
                    re.match(r'^(Chapter|\d+\.|[IVX]+\.)', line)
        
        if is_header:
            # 今までの段落を吐き出す
            if current_paragraph:
                formatted_blocks.append({"type": "p", "text": current_paragraph})
                current_paragraph = ""
            # 見出しとして追加
            formatted_blocks.append({"type": "h", "text": line})
        else:
            # 文章をつなげる処理（ハイフンなら結合、それ以外はスペース）
            if current_paragraph:
                if current_paragraph.endswith("-"):
                    current_paragraph = current_paragraph[:-1] + line
                else:
                    current_paragraph += " " + line
            else:
                current_paragraph = line
    
    # 最後の段落を追加
    if current_paragraph:
        formatted_blocks.append({"type": "p", "text": current_paragraph})
            
    return formatted_blocks

# --- セッション初期化 ---
if "last_clicked" not in st.session_state:
    st.session_state.last_clicked = ""
if "current_result" not in st.session_state:
    st.session_state.current_result = None

# ==========================================
# アプリ画面作成
# ==========================================
st.title("📚 AI Book Reader")

# ファイルアップロード
uploaded_file = st.file_uploader("Upload PDF", type="pdf")

if uploaded_file is not None:
    reader = PdfReader(uploaded_file)
    total_pages = len(reader.pages)
    
    # ページ選択
    page_num = st.number_input("Page", 1, total_pages, 1)

    # ----------------------------------------------------
    # レイアウト: 左右分割
    # ----------------------------------------------------
    col_main, col_side = st.columns([2, 1])

    # --- 左側: 本文表示エリア ---
    with col_main:
        st.subheader("📄 Reading Area")
        
        page = reader.pages[page_num - 1]
        raw_text = page.extract_text()
        blocks = format_text_smart(raw_text)

        # HTML生成（マーカー機能なし＝再描画時の変化なし）
        html_content = """
        <style>
            .book-container {
                font-family: 'Georgia', serif;
                font-size: 18px;
                line-height: 1.8;
                color: #222;
                background: #fff;
                padding: 30px;
                border: 1px solid #ddd;
                border-radius: 5px;
            }
            .header-text {
                font-weight: bold;
                font-size: 1.2em;
                margin-top: 20px;
                margin-bottom: 10px;
                color: #000;
            }
            .w { 
                text-decoration: none; 
                color: #333; 
                cursor: pointer; 
            }
            .w:hover { 
                background-color: #fff9c4; /* ホバー時のみ色が変わる */
                border-radius: 3px;
            }
        </style>
        <div class='book-container'>
        """
        
        word_counter = 0
        
        for block in blocks:
            if block["type"] == "h":
                # 見出し処理（太字にする）
                html_content += f"<div class='header-text'>{html.escape(block['text'])}</div>"
            else:
                # 本文処理（単語リンク化）
                words = block["text"].split()
                html_content += "<p>"
                for w in words:
                    clean_w = w.strip(".,!?\"'()[]{}:;")
                    if not clean_w:
                        html_content += w + " "
                        continue
                    
                    # IDはシンプルに連番＋単語
                    unique_id = f"{word_counter}_{clean_w}"
                    safe_w = html.escape(w)
                    
                    # クラスは常に一定（マーカー用の分岐を削除）
                    html_content += f"<a href='#' id='{unique_id}' class='w'>{safe_w}</a> "
                    word_counter += 1
                html_content += "</p>"
        
        html_content += "</div>"
        
        # クリック検知
        clicked = click_detector(html_content)
        
        # --- クリック時の処理 ---
        if clicked and clicked != st.session_state.last_clicked:
            st.session_state.last_clicked = clicked
            
            # IDから単語を取り出す
            target_word = clicked.split("_", 1)[1]
            
            # 翻訳実行（トーストのみで、スピナーで画面を隠さない）
            st.toast(f"Searching: {target_word}...", icon="🔍")
            
            result = translate_word_with_gpt(target_word)
            st.session_state.current_result = {"word": target_word, "info": result}
            
            # シート保存
            try:
                client = get_gspread_client()
                sheet = client.open(st.secrets["sheet_config"]["sheet_name"]).sheet1
                date_str = datetime.now().strftime("%Y-%m-%d")
                sheet.append_row([target_word, result["meaning"], date_str])
            except Exception:
                pass # 保存エラーでも閲覧は止めない
            
            # 画面更新（マーカー色が変わらないのでチラつきを感じにくい）
            st.rerun()

    # --- 右側: 辞書表示エリア ---
    with col_side:
        st.subheader("💡 Dictionary")
        
        res = st.session_state.current_result
        if res:
            info = res["info"]
            st.markdown(f"""
            <div style="
                border: 2px solid #4CAF50;
                border-radius: 10px;
                padding: 20px;
                background-color: #f1f8e9;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            ">
                <h2 style="color: #2e7d32; margin-top:0;">{res['word']}</h2>
                <span style="background:#2e7d32; color:#fff; padding:2px 8px; border-radius:4px; font-size:0.8em;">{info.get('pos')}</span>
                <hr style="border-top: 1px solid #a5d6a7;">
                <h3 style="margin:10px 0;">{info.get('meaning')}</h3>
                <p style="color: #555; font-size: 0.9em;">{info.get('details')}</p>
            </div>
            <div style="text-align:right; color:#888; font-size:0.8em; margin-top:5px;">
                ✅ Saved to Spreadsheet
            </div>
            """, unsafe_allow_html=True)
        else:
            st.info("Tap any word on the left.")

else:
    st.info("👈 Please upload a PDF file.")
