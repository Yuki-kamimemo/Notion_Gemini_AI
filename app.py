import os
import streamlit as st
import google.generativeai as genai
from dotenv import load_dotenv
import notion_client
import yaml
import streamlit_authenticator as stauth
import json
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

from notion_utils import get_all_databases, get_pages_in_database
from core_logic import run_new_page_process, run_edit_page_process

# .envファイルから環境変数を読み込む
load_dotenv()

# --- Streamlit UI設定 ---
st.set_page_config(page_title="Notion記事自動生成AI", layout="wide")

# --- 認証設定の読み込み ---
try:
    with open('config.yaml', 'r', encoding='utf-8') as file:
        config = yaml.load(file, Loader=yaml.SafeLoader)
except FileNotFoundError:
    st.error("設定ファイル(config.yaml)が見つかりません。")
    st.stop()

# --- APIキー管理関数 ---
# Cookieのキーをソルトとして利用し、暗号化キーを生成
def get_encryption_key(salt_str: str) -> bytes:
    salt = salt_str.encode()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(salt))
    return key

# 暗号化キーの取得
encryption_key = get_encryption_key(config['cookie']['key'])
fernet = Fernet(encryption_key)
API_KEYS_FILE = "user_api_keys.json"

def save_api_keys(username, notion_key, gemini_key):
    """ユーザーのAPIキーを暗号化してJSONファイルに保存"""
    try:
        with open(API_KEYS_FILE, 'r') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    
    encrypted_notion = fernet.encrypt(notion_key.encode()).decode()
    encrypted_gemini = fernet.encrypt(gemini_key.encode()).decode()
    
    data[username] = {
        'notion_api_key': encrypted_notion,
        'gemini_api_key': encrypted_gemini
    }
    
    with open(API_KEYS_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def load_api_keys(username):
    """JSONファイルからユーザーのAPIキーを読み込み復号して返す"""
    try:
        with open(API_KEYS_FILE, 'r') as f:
            data = json.load(f)
        
        user_data = data.get(username)
        if not user_data:
            return None
            
        decrypted_notion = fernet.decrypt(user_data['notion_api_key'].encode()).decode()
        decrypted_gemini = fernet.decrypt(user_data['gemini_api_key'].encode()).decode()
        
        return {'notion': decrypted_notion, 'gemini': decrypted_gemini}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None

# --- Authenticatorオブジェクトの初期化 ---
authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days']
)

# --- ログインウィジェットの表示 ---
authenticator.login(location='main')

# --- 認証ステータスに応じた処理分岐 ---
if st.session_state["authentication_status"]:
    # --- ログイン成功後の処理 ---
    st.sidebar.title(f'Welcome *{st.session_state["name"]}*')
    authenticator.logout(location='sidebar')

    # --- APIキー設定UI ---
    with st.sidebar.expander("APIキー設定"):
        st.info("ご自身のNotionとGeminiのAPIキーを入力してください。")
        with st.form("api_key_form", clear_on_submit=True):
            notion_key_input = st.text_input("Notion API Key", type="password", key="notion_key")
            gemini_key_input = st.text_input("Gemini API Key", type="password", key="gemini_key")
            submitted = st.form_submit_button("保存")
            if submitted:
                if notion_key_input and gemini_key_input:
                    save_api_keys(st.session_state["username"], notion_key_input, gemini_key_input)
                    st.success("APIキーを保存しました！")
                else:
                    st.warning("両方のAPIキーを入力してください。")

    # --- ユーザーのAPIキーを読み込み ---
    user_api_keys = load_api_keys(st.session_state["username"])

    if not user_api_keys:
        st.warning("APIキーが設定されていません。サイドバーの「APIキー設定」から登録してください。")
        st.stop()

    # ↓↓↓↓ ここから下が元のアプリケーションのメインロジックです ↓↓↓↓
    st.title("📝 Notion記事自動生成AI")
    st.markdown("Webの最新情報やお手元のドキュメントを元に、Notionページの作成から編集までを自動化します。")

    # --- APIクライアントの初期化 ---
    try:
        # ユーザーが切り替わった場合、またはクライアントが未初期化の場合のみ実行
        if st.session_state.get('current_user') != st.session_state["username"] or 'clients_initialized' not in st.session_state:
            st.session_state.notion_client = notion_client.Client(auth=user_api_keys['notion'])
            genai.configure(api_key=user_api_keys['gemini'])
            GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
            GEMINI_LITE_MODEL_NAME = os.getenv("GEMINI_LITE_MODEL", "gemini-2.5-flash-lite")
            st.session_state.gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)
            st.session_state.gemini_lite_model = genai.GenerativeModel(GEMINI_LITE_MODEL_NAME)
            st.session_state.notion_client.users.me() # Notion APIへの接続確認
            st.session_state.clients_initialized = True
            st.session_state.current_user = st.session_state["username"] # 現在のユーザーを記録
            st.toast(f"✅ APIクライアントの準備ができました")
    except Exception as e:
        st.error(f"APIクライアントの初期化中にエラーが発生しました。APIキーが正しいか確認してください。エラー: {e}")
        st.stop()

    # --- メインUI ---
    with st.spinner("データベースを読み込んでいます..."):
        databases = get_all_databases(st.session_state.notion_client)

    if not databases:
        st.error("アクセス可能なNotionデータベースが見つかりませんでした。")
        st.stop()

    db_options = {db['id']: db['title'] for db in databases}
    selected_db_id = st.selectbox("1. 操作するNotionデータベースを選択してください", options=db_options.keys(), format_func=lambda x: db_options[x])
    mode = st.radio("2. 実行する操作を選択してください", ("新しいページを作成する", "既存のページを編集・追記する"), horizontal=True)
    st.markdown("---")

    # --- ペルソナ選択肢の定義 ---
    persona_options = {
        "プロのライター": "あなたはプロのライターです。",
        "マーケティング担当者": "あなたは経験豊富なマーケティング担当者です。読者のエンゲージメントを高めることを意識してください。",
        "技術ドキュメントライター": "あなたは正確で分かりやすい文章を書く技術ドキュメントライターです。専門用語は適切に解説してください。",
        "フレンドリーな解説者": "あなたは複雑なトピックを、親しみやすくフレンドリーな口調で解説する専門家です。",
        "社内資料作成・利用ガイドの作成者": "あなたは、社内での利用を目的とした公式な資料やガイドを作成する担当者です。以下の点を重視してください：情報の正確性と客観性、専門用語の統一、後から他の人が追記・編集しやすいような構造的な記述。",
        "カスタム": "カスタム...",
    }

    # --- プロンプトテンプレートの定義 ---
    prompt_templates = {
        "記事作成": "{topic}について、読者の興味を引く魅力的な記事を作成してください。",
        "要約": "{topic}について、重要なポイントを箇条書きで分かりやすく要約してください。",
        "アイデア出し": "{topic}というテーマで、ユニークなアイデアを5つ提案してください。",
        "プレスリリース作成": "{topic}に関するプレスリリースを作成してください。背景、目的、主要な特徴、今後の展望を含めてください。",
        "利用ガイドの作成": "{topic}についての利用ガイドを作成してください。初心者でも理解できるよう、以下の構成で記述してください： 1. 概要と目的 2. 主な機能や特徴 3. 基本的な使い方（ステップバイステップ形式） 4. よくある質問（Q&A）",
        "カスタム": "カスタム...",
    }

    # --- フォーム入力 ---
    if mode == "新しいページを作成する":
        st.subheader("新しいページを作成")
        with st.form("new_page_form"):
            st.markdown("##### 1. AIの役割（ペルソナ）を選択")
            selected_persona_key = st.selectbox("AIのペルソナ:", options=persona_options.keys(), label_visibility="collapsed")
            if selected_persona_key == "カスタム":
                ai_persona = st.text_input("AIの具体的な役割を入力:", placeholder="例：小学生にもわかるように説明する科学の先生")
            else:
                ai_persona = persona_options[selected_persona_key]

            st.markdown("##### 2. 参考資料とWeb検索設定")
            st.markdown("<small>※ ファイル > 単一URL > Web検索 の優先順位で情報源として利用します。</small>", unsafe_allow_html=True)
            uploaded_files = st.file_uploader("参考ドキュメント (PDF/Word/Text):", type=['pdf', 'docx', 'txt'], accept_multiple_files=True)
            source_url = st.text_input("参考URL (上記ファイルがない場合):", placeholder="https://example.com/article")
            
            col1, col2 = st.columns(2)
            with col1:
                search_count = st.slider("Web検索数（件）:", min_value=1, max_value=15, value=5, help="参考資料がない場合にWeb検索する最大記事数。")
            with col2:
                full_text_token_limit = st.slider("全文取得のトークン上限:", min_value=5000, max_value=150000, value=20000, step=5000, help="このトークン数までは記事の全文を使います。超えた分は要約されます。")

            st.markdown("##### 3. AIへの指示")
            selected_template_key = st.selectbox("行いたい作業を選択してください:", options=prompt_templates.keys())
            if selected_template_key == "カスタム":
                user_prompt_new = st.text_area("AIへの具体的な指示を入力してください:", placeholder="例：{topic}について比較表を作成してください。")
                topic_new = ""
            else:
                user_prompt_new = prompt_templates[selected_template_key]
                topic_new = st.text_area("具体的なテーマやキーワードを入力してください:", placeholder="例：最新のAI技術")

            submitted_new = st.form_submit_button("記事を生成する", type="primary")

        if submitted_new:
            if selected_template_key != "カスタム":
                final_prompt_new = user_prompt_new.format(topic=topic_new)
            else:
                final_prompt_new = user_prompt_new
            if not final_prompt_new or (selected_template_key != "カスタム" and not topic_new):
                st.warning("作業内容とテーマの両方を入力してください。")
            elif not ai_persona:
                 st.warning("AIのペルソナを入力してください。")
            else:
                status_placeholder = st.empty()
                results_placeholder = st.empty()
                run_new_page_process(selected_db_id, final_prompt_new, ai_persona, uploaded_files, source_url, search_count, full_text_token_limit, status_placeholder, results_placeholder)

    elif mode == "既存のページを編集・追記する":
        st.subheader("既存のページを編集・追記")
        pages = get_pages_in_database(st.session_state.notion_client, selected_db_id)
        if not pages:
            st.warning("このデータベースには編集可能なページがありません。")
        else:
            page_options = {p['id']: p['title'] for p in pages}
            selected_page_id = st.selectbox("編集・追記したいページを選択してください", options=page_options.keys(), format_func=lambda x: page_options[x])
            with st.form("edit_page_form"):
                st.markdown("##### 1. AIの役割（ペルソナ）を選択")
                selected_persona_key_edit = st.selectbox("AIのペルソナ:", options=persona_options.keys(), key="persona_edit", label_visibility="collapsed")
                if selected_persona_key_edit == "カスタム":
                    ai_persona_edit = st.text_input("AIの具体的な役割を入力:", placeholder="例：この記事の誤りを指摘する校正者", key="custom_persona_edit")
                else:
                    ai_persona_edit = persona_options[selected_persona_key_edit]

                st.markdown("##### 2. 参考資料とWeb検索設定")
                st.markdown("<small>※ ファイル > 単一URL > Web検索 の優先順位で情報源として利用します。</small>", unsafe_allow_html=True)
                uploaded_files_edit = st.file_uploader("参考ドキュメント (PDF/Word/Text):", type=['pdf', 'docx', 'txt'], accept_multiple_files=True, key="uploader_edit")
                source_url_edit = st.text_input("参考URL (上記ファイルがない場合):", placeholder="https://example.com/article", key="source_url_edit")
                
                col1_edit, col2_edit = st.columns(2)
                with col1_edit:
                    search_count_edit = st.slider("Web検索数（件）:", min_value=1, max_value=15, value=5, help="参考資料がない場合にWeb検索する最大記事数。", key="slider_edit")
                with col2_edit:
                    full_text_token_limit_edit = st.slider("全文取得のトークン上限:", min_value=5000, max_value=150000, value=20000, step=5000, help="このトークン数までは記事の全文を使います。超えた分は要約されます。", key="slider_token_edit")

                st.markdown("##### 3. AIへの指示")
                selected_template_key_edit = st.selectbox("行いたい作業を選択してください:", options=prompt_templates.keys(), key="template_edit")
                if selected_template_key_edit == "カスタム":
                    user_prompt_edit = st.text_area("AIへの具体的な指示を入力してください:", placeholder="例：この記事に{topic}の情報を追記してください。")
                    topic_edit = ""
                else:
                    user_prompt_edit = prompt_templates[selected_template_key_edit]
                    topic_edit = st.text_area("具体的なテーマやキーワードを入力してください:", placeholder="例：ビジネスでの具体的な活用事例")

                submitted_edit = st.form_submit_button("編集・追記を実行する", type="primary")

            if submitted_edit:
                if selected_template_key_edit != "カスタム":
                    final_prompt_edit = user_prompt_edit.format(topic=topic_edit)
                else:
                    final_prompt_edit = user_prompt_edit
                
                if not final_prompt_edit or (selected_template_key_edit != "カスタム" and not topic_edit):
                    st.warning("作業内容とテーマの両方を入力してください。")
                elif not ai_persona_edit:
                    st.warning("AIのペルソナを入力してください。")
                else:
                    status_placeholder = st.empty()
                    results_placeholder = st.empty()
                    run_edit_page_process(selected_page_id, final_prompt_edit, ai_persona_edit, uploaded_files_edit, source_url_edit, search_count_edit, full_text_token_limit_edit, status_placeholder, results_placeholder)
    
    # --- ★重要★ 認証情報をファイルに保存 ---
    try:
        with open('config.yaml', 'w', encoding='utf-8') as file:
            yaml.dump(config, file, default_flow_style=False)
    except Exception as e:
        st.error(f"設定ファイルの保存中にエラーが発生しました: {e}")


# --- ログイン失敗時、または未ログイン時の表示 ---
elif st.session_state["authentication_status"] is False:
    st.error('ユーザー名かパスワードが間違っています')
elif st.session_state["authentication_status"] is None:
    st.warning('ユーザー名とパスワードを入力してください')
