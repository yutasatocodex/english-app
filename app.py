import streamlit as st
from pypdf import PdfReader
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from st_click_detector import click_detector
import html
import json
import re
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
def translate_list_with_gpt(word_list):
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    words_str = ", ".join(word_list)
    prompt = f"""
    You are an English-Japanese dictionary.
    Identify the following words: {words_str}.
    For each word, provide:
    1. "meaning": Japanese meaning (short).
    2. "pos": Part of Speech (e.g., Verb, Noun).
    3. "details": Brief nuance or synonyms.
    
    Output MUST be a JSON object like:
    {{
        "word1": {{"meaning": "...", "pos": "...", "details": "..."}},
        "word2": {{"meaning": "...", "pos": "...", "details": "..."}}
    }}
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
        return {}

# --- 📖 テキスト整形ロジック（本の見た目に近づける） ---
def format_text_like_a_book(text):
    if not text: return ""
    
    lines = text.splitlines()
    formatted_buffer = ""
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        # --- 改行を入れるべき場所の判定 ---
        # 1. 見出しっぽい（短くて、末尾にピリオドがない）
        is_title = len(line) < 60 and not line.endswith(".")
        # 2. 箇条書き（数字や記号で始まる）
        is_bullet = re.match(r'^(\d+\.|-|•|Chapter)', line)
        
        # 前の行との結合処理
        if formatted_buffer:
            if is_title or is_bullet:
                # 見出しや箇条書きの前は「2回改行」して段落を空ける
                formatted_buffer += "\n\n" + line
            else:
                # 普通の文章は、ハイフンなら繋げ、それ以外はスペースで繋ぐ
                if formatted_buffer.endswith("-"):
                    formatted_buffer = formatted_buffer[:-1] + line
                else:
                    formatted_buffer += " " + line
        else:
            formatted_buffer = line
            
    return formatted_buffer

# --- セッション初期化 ---
if "clicked_ids" not in st.session_state:
    st.session_state.clicked_ids = set()
if "translated_results" not in st.session_state:
    st.session_state.translated_results = {}

# ==========================================
# アプリ画面作成
# ==========================================
st.title("📚 AI Book Reader")

# 1. ファイルアップロード（トップに配置）
uploaded_file = st.file_uploader("Upload PDF", type="pdf")

if uploaded_file is not None:
    reader = PdfReader(uploaded_file)
    total_pages = len(reader.pages)
    
    # ページ選択バー
    col_nav, col_dummy = st.columns([1, 3])
    with col_nav:
        page_num = st.number_input("Page", 1, total_pages, 1)

    # テキスト抽出
    page = reader.pages[page_num - 1]
    raw_text = page.extract_text()
    clean_text = format_text_like_a_book(raw_text)

    # ----------------------------------------------------
    # メインエリア: 本文と翻訳ボタン
    # ----------------------------------------------------
    col_main, col_side = st.columns([2, 1])

    with col_main:
        st.subheader("📄 Reading Area")
        
        # ★ ここに翻訳ボタンを配置（見逃し防止） ★
        selected_count = len(st.session_state.clicked_ids)
        if st.button(f"Translate {selected_count} Words 🚀", type="primary", use_container_width=True):
            # 翻訳実行ロジック
            targets = [cid.split("_", 1)[1] for cid in st.session_state.clicked_ids if "_" in cid]
            if targets:
                with st.spinner("Translating..."):
                    results = translate_list_with_gpt(targets)
                    st.session_state.translated_results = results
                    # シート保存
                    try:
                        client = get_gspread_client()
                        sheet = client.open(st.secrets["sheet_config"]["sheet_name"]).sheet1
                        date_str = datetime.now().strftime("%Y-%m-%d")
                        rows = [[w, i.get("meaning",""), date_str] for w, i in results.items()]
                        sheet.append_rows(rows)
                        st.toast("Saved to Spreadsheet!", icon="✅")
                    except Exception as e:
                        st.error(f"Sheet Error: {e}")

        # HTML生成（クリック検知用）
        html_content = """
        <style>
            .book-text {
                font-family: 'Georgia', serif; /* 本のようなフォント */
                font-size: 18px;
                line-height: 1.8;
                color: #222;
                background: #fff;
                padding: 30px;
                border: 1px solid #ddd;
                border-radius: 5px;
            }
            .w { text-decoration: none; color: #333; cursor: pointer; }
            .w:hover { background-color: #e3f2fd; }
            .marked { 
                background-color: #fff59d; 
                border-bottom: 2px solid #fbc02d; 
                font-weight: bold;
            }
        </style>
        <div class='book-text'>
        """
        
        # 改行コード(\n)を <br> に変換しながら単語リンクを作成
        paragraphs = clean_text.split("\n")
        for p_idx, paragraph in enumerate(paragraphs):
            if not paragraph.strip(): 
                html_content += "<br>" # 空行
                continue
                
            words = paragraph.split()
            for w_idx, w in enumerate(words):
                clean_w = w.strip(".,!?\"'()[]{}:;")
                if not clean_w:
                    html_content += w + " "
                    continue
                
                # ID作成: p{ページ}_i{連番}_{単語}
                unique_id = f"{page_num}_{p_idx}_{w_idx}_{clean_w}"
                
                css = "w"
                if unique_id in st.session_state.clicked_ids:
                    css += " marked"
                
                safe_w = html.escape(w)
                html_content += f"<a href='#' id='{unique_id}' class='{css}'>{safe_w}</a> "
            
            html_content += "<br>"
        
        html_content += "</div>"
        
        # クリック検知実行
        clicked = click_detector(html_content)
        if clicked:
            if clicked in st.session_state.clicked_ids:
                st.session_state.clicked_ids.remove(clicked)
            else:
                st.session_state.clicked_ids.add(clicked)
            st.rerun()

    # ----------------------------------------------------
    # サイドエリア: 翻訳結果
    # ----------------------------------------------------
    with col_side:
        st.subheader("💡 Dictionary")
        
        # チラつきが嫌な人のための「リスト選択モード」
        with st.expander("Or select from list (No Reload)", expanded=False):
            all_words = sorted(list(set(clean_text.split()))) # 簡易的な単語抽出
            selected_from_list = st.multiselect("Select words:", all_words)
            if st.button("Translate List"):
                # リスト選択分をID形式に変換して追加（簡易対応）
                for w in selected_from_list:
                    dummy_id = f"list_0_0_{w}"
                    st.session_state.clicked_ids.add(dummy_id)
                st.rerun()

        # 結果表示
        results = st.session_state.translated_results
        if results:
            for word, info in results.items():
                st.markdown(f"""
                <div style="background:#f1f8e9; padding:15px; margin-bottom:10px; border-radius:8px; border-left:5px solid #558b2f;">
                    <h3 style="margin:0; color:#33691e;">{word}</h3>
                    <span style="background:#333; color:#fff; padding:2px 6px; font-size:0.8em; border-radius:4px;">{info.get('pos','')}</span>
                    <p style="margin:5px 0 0 0; font-weight:bold;">{info.get('meaning','')}</p>
                    <p style="margin:0; font-size:0.9em; color:#555;">{info.get('details','')}</p>
                </div>
                """, unsafe_allow_html=True)
            
            # クリアボタン
            if st.button("Clear Results"):
                st.session_state.clicked_ids = set()
                st.session_state.translated_results = {}
                st.rerun()
        else:
            st.info("Tap words on the left, then click 'Translate' above.")

else:
    st.info("👈 Please upload a PDF file to start.")
