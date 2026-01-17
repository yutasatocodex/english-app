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

# --- ページ設定（ワイド表示） ---
st.set_page_config(layout="wide", page_title="AI PDF Note")

# --- 設定1: Googleスプレッドシート連携 ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

def get_gspread_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

# --- 設定2: OpenAI (ChatGPT) 辞書機能 (JSONモード) ---
def translate_list_with_gpt(word_list):
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    
    # 複数の単語をまとめてJSONで返させるプロンプト
    words_str = ", ".join(word_list)
    prompt = f"""
    You are an English-Japanese dictionary.
    Identify the following words: {words_str}.
    For each word, provide:
    1. "meaning": Japanese meaning (short).
    2. "pos": Part of Speech (e.g., Verb, Noun) in Japanese or English.
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
            response_format={"type": "json_object"} # JSONを強制
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return {}

# --- PDFテキスト整形関数（改良版） ---
def clean_pdf_text_smart(text):
    if not text:
        return ""
    
    lines = text.splitlines()
    new_lines = []
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
            
        # 1. ハイフネーション行末処理 (例: com- \n puter -> computer)
        if line.endswith("-"):
            line = line[:-1] # ハイフンを取って次の行と繋げる準備
            # この場合は改行コードを入れずにリストに追加（後でjoinするときに工夫が必要だが簡易的に）
        
        new_lines.append(line)

    # 結合ロジック
    # 基本はスペースで繋ぐが、以下の場合は「改行」を入れる
    # A. 文末記号 (., !, ?) で終わっている
    # B. 行が極端に短い（見出しの可能性）
    # C. 箇条書き記号で始まっている
    
    final_text = ""
    for line in new_lines:
        is_end_of_sentence = line.endswith(('.', '!', '?', ':', ';'))
        is_short_title = len(line) < 50 and not line.endswith(',')
        is_bullet = line.strip().startswith(('•', '-', '*', '1.', '2.', '3.', 'Chapter'))
        
        if final_text:
            # 前の行が「文の終わり」か「見出し」なら改行を入れる
            # そうでなければスペースで繋ぐ（文章をつなげる）
            prev_char = final_text[-1]
            if prev_char in ['.', '!', '?', '\n'] or is_bullet or is_short_title:
                final_text += "\n" + line
            else:
                final_text += " " + line
        else:
            final_text = line
            
    return final_text

# --- セッション状態の初期化 ---
if "clicked_ids" not in st.session_state:
    st.session_state.clicked_ids = set() # 選択中の単語ID
if "translated_results" not in st.session_state:
    st.session_state.translated_results = {} # 翻訳結果

# --- アプリ画面構成 ---
st.title("🤖 AI PDF Reader & Marker")

# レイアウト: サイドバーで操作、メインで閲覧
st.sidebar.header("1. Upload & Controls")
uploaded_file = st.sidebar.file_uploader("Upload PDF", type="pdf")

if uploaded_file is not None:
    try:
        reader = PdfReader(uploaded_file)
        total_pages = len(reader.pages)

        # ページ移動
        page_num = st.sidebar.number_input(
            "Page", min_value=1, max_value=total_pages, value=1, step=1
        )
        
        # --- 翻訳実行ボタン（サイドバー） ---
        st.sidebar.markdown("---")
        st.sidebar.markdown("### 2. Actions")
        
        # 現在選択されている単語のリストを作成
        selected_words_display = []
        # IDから単語を復元するための辞書が必要だが、
        # ここでは簡易的にセッションに保存されたIDを使う
        
        if st.sidebar.button("Translate Selected Words", type="primary"):
            # 選択されたIDから単語リストを作る（後述のロジックでIDに単語を埋め込む）
            targets = []
            for cid in st.session_state.clicked_ids:
                if "_" in cid: # ID形式: index_word
                    word = cid.split("_", 1)[1]
                    targets.append(word)
            
            if targets:
                with st.spinner("Translating all words..."):
                    results = translate_list_with_gpt(targets)
                    st.session_state.translated_results = results
                    
                    # スプレッドシート保存
                    try:
                        client = get_gspread_client()
                        sheet_name = st.secrets["sheet_config"]["sheet_name"]
                        sheet = client.open(sheet_name).sheet1
                        date_str = datetime.now().strftime("%Y-%m-%d")
                        
                        rows_to_add = []
                        for word, info in results.items():
                            rows_to_add.append([word, info.get("meaning", ""), date_str])
                        
                        if rows_to_add:
                            sheet.append_rows(rows_to_add)
                            st.toast(f"✅ Saved {len(rows_to_add)} words!", icon="📂")
                    except Exception as e:
                        st.error(f"Sheet Error: {e}")

        # --- クリアボタン ---
        if st.sidebar.button("Clear Markers"):
            st.session_state.clicked_ids = set()
            st.session_state.translated_results = {}
            st.rerun()

        # --- メインエリア表示 ---
        page = reader.pages[page_num - 1]
        raw_text = page.extract_text()

        if raw_text:
            # 改良版テキスト整形
            clean_text = clean_pdf_text_smart(raw_text)
            
            # 2カラム: 左(本文), 右(翻訳結果)
            col_text, col_res = st.columns([2, 1])
            
            with col_text:
                st.markdown("### 📄 Reader View")
                
                # HTML生成
                html_content = """
                <style>
                    .pdf-box {
                        font-family: 'Helvetica Neue', Arial, sans-serif;
                        background-color: #ffffff;
                        color: #333;
                        padding: 30px;
                        border-radius: 5px;
                        border: 1px solid #ddd;
                        line-height: 1.8;
                        font-size: 18px;
                    }
                    .w { 
                        text-decoration: none; 
                        color: #333; 
                        cursor: pointer; 
                        padding: 2px 1px;
                        border-radius: 3px;
                    }
                    .w:hover { background-color: #eee; }
                    /* 選択済みマーカー（黄色） */
                    .marked {
                        background-color: #fff176; 
                        border-bottom: 2px solid #fdd835;
                        color: #000;
                        font-weight: bold;
                    }
                </style>
                <div class='pdf-box'>
                """
                
                # 改行を <br> に変換しつつ単語リンクを作る
                # splitlinesで行ごとに処理
                lines = clean_text.split('\n')
                
                for line_idx, line in enumerate(lines):
                    words = line.split()
                    for word_idx, w in enumerate(words):
                        # 記号除去
                        clean_w = w.strip(".,!?\"'()[]{}:;")
                        if not clean_w:
                            html_content += w + " "
                            continue
                            
                        # IDに単語そのものを埋め込む (形式: p{ページ}l{行}i{連番}_{単語})
                        # これで後から単語を復元できる
                        unique_id = f"{page_num}l{line_idx}i{word_idx}_{clean_w}"
                        
                        css_class = "w"
                        if unique_id in st.session_state.clicked_ids:
                            css_class += " marked"
                        
                        safe_w = html.escape(w)
                        html_content += f"<a href='#' id='{unique_id}' class='{css_class}'>{safe_w}</a> "
                    
                    html_content += "<br>" # 行末に改行タグ
                
                html_content += "</div>"
                
                # クリック検知
                clicked = click_detector(html_content)
                
                if clicked:
                    # クリックされたらセットに追加/削除（トグル動作）
                    if clicked in st.session_state.clicked_ids:
                        st.session_state.clicked_ids.remove(clicked)
                    else:
                        st.session_state.clicked_ids.add(clicked)
                    st.rerun()

            # --- 右カラム: 翻訳結果リスト ---
            with col_res:
                st.markdown("### 💡 Word List")
                
                results = st.session_state.translated_results
                if results:
                    for word, info in results.items():
                        st.markdown(f"""
                        <div style="background:#f9f9f9; padding:10px; margin-bottom:10px; border-left:4px solid #4CAF50; border-radius:4px;">
                            <div style="font-weight:bold; font-size:1.1em; color:#2e7d32;">{word}</div>
                            <div style="font-size:0.9em; color:#555;"><i>{info.get('pos', '')}</i></div>
                            <div style="font-weight:bold;">{info.get('meaning', '')}</div>
                            <div style="font-size:0.85em; color:#666;">{info.get('details', '')}</div>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    if len(st.session_state.clicked_ids) > 0:
                        st.info(f"👉 {len(st.session_state.clicked_ids)} words selected.\nClick 'Translate Selected Words' in the sidebar!")
                    else:
                        st.info("Tap words in the text to mark them.")

    except Exception as e:
        st.error(f"Error: {e}")
else:
    st.info("👈 Please upload a PDF file.")
