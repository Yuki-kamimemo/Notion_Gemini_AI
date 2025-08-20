import os
import streamlit as st
import google.generativeai as genai
from dotenv import load_dotenv
import notion_client

from notion_utils import get_all_databases, get_pages_in_database
from core_logic import run_new_page_process, run_edit_page_process
from ui_components import (
    render_main_ui,
    render_new_page_form,
    render_edit_page_form
)

# .envファイルから環境変数を読み込む
load_dotenv()

# --- Streamlit UI設定 ---
st.set_page_config(page_title="Notion記事自動生成AI", layout="wide")
st.title("📝 Notion記事自動生成AI")
st.markdown("Webの最新情報やお手元のドキュメントを元に、Notionページの作成から編集までを自動化します。")

# --- APIキーの読み込みとクライアントの初期化 ---
try:
    if 'clients_initialized' not in st.session_state:
        NOTION_API_KEY = os.getenv("NOTION_API_KEY")
        GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
        
        if not NOTION_API_KEY or not GEMINI_API_KEY:
            st.error("APIキーが設定されていません。.envファイルにキーを記述してください。")
            st.stop()

        st.session_state.notion_client = notion_client.Client(auth=NOTION_API_KEY)
        genai.configure(api_key=GEMINI_API_KEY)
        
        GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        GEMINI_LITE_MODEL_NAME = os.getenv("GEMINI_LITE_MODEL", "gemini-1.5-flash-latest")
        
        st.session_state.gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        st.session_state.gemini_lite_model = genai.GenerativeModel(GEMINI_LITE_MODEL_NAME)
        
        st.session_state.notion_client.users.me() # APIキーの有効性チェック
        st.session_state.clients_initialized = True
        st.toast(f"✅ APIクライアントの準備ができました (モデル: {GEMINI_MODEL_NAME})")

except Exception as e:
    st.error(f"APIクライアントの初期化中にエラーが発生しました: {e}")
    st.stop()

# --- メインUIの描画 ---
selected_db_id, mode = render_main_ui(st.session_state.notion_client)

st.markdown("---")

# --- フォーム入力 ---
if mode == "新しいページを作成する":
    form_data = render_new_page_form()
    if form_data:
        status_placeholder = st.empty()
        results_placeholder = st.empty()
        run_new_page_process(
            database_id=selected_db_id,
            user_prompt=form_data["final_prompt"],
            ai_persona=form_data["ai_persona"],
            uploaded_files=form_data["uploaded_files"],
            source_url=form_data["source_url"],
            search_count=form_data["search_count"],
            full_text_token_limit=form_data["full_text_token_limit"],
            status_placeholder=status_placeholder,
            results_placeholder=results_placeholder
        )

elif mode == "既存のページを編集・追記する":
    pages = get_pages_in_database(st.session_state.notion_client, selected_db_id)
    if not pages:
        st.warning("このデータベースには編集可能なページがありません。")
    else:
        form_data = render_edit_page_form(pages)
        if form_data:
            status_placeholder = st.empty()
            results_placeholder = st.empty()
            run_edit_page_process(
                page_id=form_data["selected_page_id"],
                user_prompt=form_data["final_prompt"],
                ai_persona=form_data["ai_persona"],
                uploaded_files=form_data["uploaded_files"],
                source_url=form_data["source_url"],
                search_count=form_data["search_count"],
                full_text_token_limit=form_data["full_text_token_limit"],
                status_placeholder=status_placeholder,
                results_placeholder=results_placeholder
            )