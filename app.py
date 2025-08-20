import os
import streamlit as st
import google.generativeai as genai
from dotenv import load_dotenv
import notion_client
import streamlit_authenticator as stauth
from cryptography.fernet import Fernet
import logging
import traceback
# ★★★ Firebase Admin SDKの公式なインポート ★★★
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import hashlib
import base64

from notion_utils import get_all_databases, get_pages_in_database
from core_logic import run_new_page_process, run_edit_page_process

# .envファイルから環境変数を読み込む (ローカル開発用)
load_dotenv()

# --- ログ設定 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Streamlit UI設定 ---
st.set_page_config(page_title="Notion記事自動生成AI", layout="wide")

# --- Firestore 初期化 (Firebase Admin SDKを使用) ---
@st.cache_resource
def initialize_firestore():
    """Firebase Admin SDKを初期化し、Firestoreクライアントを返す"""
    try:
        # Streamlit Secretsから認証情報を辞書として取得
        secrets_dict = st.secrets["FIREBASE_SERVICE_ACCOUNT"]
        
        # Firebaseが必要とするキーだけを安全に抽出して新しい辞書を作成
        required_keys = [
            "type", "project_id", "private_key_id", "private_key",
            "client_email", "client_id", "auth_uri", "token_uri",
            "auth_provider_x509_cert_url", "client_x509_cert_url"
        ]
        creds_json = {key: secrets_dict[key] for key in required_keys if key in secrets_dict}

        # 辞書から認証情報オブジェクトを生成
        cred = credentials.Certificate(creds_json)
        
        # アプリがまだ初期化されていない場合のみ初期化する
        # (Streamlitの再実行時にエラーを防ぐため)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
            
        db = firestore.client()
        logging.info("Firestore client initialized successfully via Firebase Admin SDK.")
        return db
    except Exception as e:
        logging.error(f"Firestore initialization failed: {e}")
        st.error("データベースへの接続中にエラーが発生しました。SecretsやFirebaseのAPI設定を確認してください。")
        st.exception(e) # 詳細なエラー情報を画面に表示
        st.stop()

db = initialize_firestore()

# --- 認証情報とAPIキーの管理 ---
def generate_fernet_key(secret_string: str) -> bytes:
    """任意の文字列からFernetが要求する形式のキーを生成する"""
    hasher = hashlib.sha256()
    hasher.update(secret_string.encode('utf-8'))
    return base64.urlsafe_b64encode(hasher.digest())

try:
    # SecretsからENCRYPTION_SECRETを取得し、それを元にFernetキーを生成
    fernet_key = generate_fernet_key(st.secrets["ENCRYPTION_SECRET"])
    fernet = Fernet(fernet_key)
except KeyError:
    st.error("ENCRYPTION_SECRETが設定されていません。StreamlitのSecretsを確認してください。")
    st.stop()




# app.py の fetch_config_from_firestore 関数を以下に置き換えてください

@st.cache_data(ttl=600)
def fetch_config_from_firestore():
    """Firestoreからユーザー設定を読み込み、authenticatorが要求する形式に変換する"""
    try:
        users_ref = db.collection('users')
        users_docs = users_ref.stream()

        usernames_dict = {}
        for doc in users_docs:
            user_data = doc.to_dict()
            username = doc.id

            # 基本的なユーザー情報を作成
            user_entry = {
                'email': user_data.get('email'),
                'name': user_data.get('name'),
                # これらはライブラリが実行時に管理
                'logged_in': False,
                'failed_login_attempts': 0
            }

            # passwordフィールドがFirestoreに存在する場合のみ、辞書に追加する
            if 'password' in user_data and user_data['password'] is not None:
                user_entry['password'] = user_data['password']

            usernames_dict[username] = user_entry

        # --- 以下、変更なし ---
        config = {
            'credentials': {
                'usernames': usernames_dict
            },
            'cookie': {
                'expiry_days': 30,
                'key': st.secrets["ENCRYPTION_SECRET"],
                'name': 'notion_ai_cookie'
            },
            'preauthorized': {
                'emails': []
            }
        }

        if "oauth2" in st.secrets and "google" in st.secrets["oauth2"]:
            google_config = dict(st.secrets["oauth2"]["google"])
            config['oauth2'] = {'google': google_config}
            config['google'] = google_config

        logging.info("Successfully fetched and formatted config from Firestore.")
        return config

    except Exception as e:
        logging.error(f"Failed to fetch config from Firestore: {e}")
        st.error("Firestoreからの設定読み込み中にエラーが発生しました。")
        st.exception(e)
        return None


def add_or_update_user_in_firestore(username, name, email, password_hash=None):
    """Firestoreに新規ユーザーを追加または既存ユーザーを更新する"""
    try:
        user_ref = db.collection('users').document(username)
        user_data = {
            'name': name,
            'email': email
        }
        if password_hash:
            user_data['password'] = password_hash
        
        # merge=Trueで既存のフィールドを上書きせずにドキュメントを作成・更新
        user_ref.set(user_data, merge=True)
        logging.info(f"User '{username}' data saved/updated in Firestore.")
        st.cache_data.clear()
        return True
    except Exception as e:
        logging.error(f"Failed to save/update user {username} in Firestore: {e}")
        return False

def save_api_keys_to_firestore(username, notion_key, gemini_key):
    """ユーザーのAPIキーを暗号化してFirestoreに保存"""
    encrypted_notion = fernet.encrypt(notion_key.encode()).decode()
    encrypted_gemini = fernet.encrypt(gemini_key.encode()).decode()
    
    user_ref = db.collection('users').document(username)
    user_ref.update({
        'notion_api_key': encrypted_notion,
        'gemini_api_key': encrypted_gemini
    })
    logging.info(f"API keys saved for user: {username}")
    st.cache_data.clear()

def load_api_keys_from_firestore(username):
    """FirestoreからユーザーのAPIキーを読み込み復号して返す"""
    user_ref = db.collection('users').document(username)
    user_doc = user_ref.get()
    if user_doc.exists:
        user_data = user_doc.to_dict()
        try:
            decrypted_notion = fernet.decrypt(user_data['notion_api_key'].encode()).decode()
            decrypted_gemini = fernet.decrypt(user_data['gemini_api_key'].encode()).decode()
            return {'notion': decrypted_notion, 'gemini': decrypted_gemini}
        except (KeyError, TypeError):
            return None
    return None

def update_password_in_firestore(username, new_hashed_password):
    """Firestoreのユーザーパスワードを更新する"""
    try:
        user_ref = db.collection('users').document(username)
        user_ref.update({
            'password': new_hashed_password
        })
        logging.info(f"Password updated successfully in Firestore for user: {username}")
        st.cache_data.clear() # Clear cache to force re-fetch of config
        return True
    except Exception as e:
        logging.error(f"Failed to update password in Firestore for user {username}: {e}")
        return False

# --- メインアプリケーション ---
# 設定取得
config = fetch_config_from_firestore()

# Authenticator作成
authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days']
)

# Googleログイン処理（公式の推奨は experimental_guest_login ではなく login + OAuth）
if "google" in config and "oauth2" in config and st.session_state["authentication_status"] is None:
    # 呼び出す際に oauth2 の設定を渡す
    if authenticator.experimental_guest_login(provider="google", location='main', oauth2=config['oauth2']):
        st.rerun()

# ... (188行目あたり)
# ログインフォームを表示・処理します。この関数の戻り値は使いません。
authenticator.login(
    location='main',
    fields={'Form name': 'ログイン', 'Username': 'ユーザー名', 'Password': 'パスワード', 'Login': 'ログイン'}
)

# 認証ステータスは st.session_state から取得します。
if st.session_state["authentication_status"]:
    # --- ログイン成功後の処理 ---
    # Googleログイン経由の新規ユーザーをFirestoreに登録
    add_or_update_user_in_firestore(
        st.session_state["username"],
        st.session_state["name"],
        st.session_state["email"]
    )
    # ... (以降のログイン成功後のUI表示コードはここに続く) ...

elif st.session_state["authentication_status"] is False:
    st.error('ユーザー名かパスワードが間違っています')

elif st.session_state["authentication_status"] is None:
    st.warning('ユーザー名とパスワードを入力してください')
    # ... (以降のパスワード忘れなどのコードはここに続く) ...

if st.session_state["authentication_status"]:
    # --- ログイン成功後の処理 ---
    # Googleログイン経由の新規ユーザーをFirestoreに登録
    add_or_update_user_in_firestore(
        st.session_state["username"],
        st.session_state["name"],
        st.session_state["email"]
    )

    st.sidebar.title(f'ようこそ, *{st.session_state["name"]}* さん')
    authenticator.logout('ログアウト', 'sidebar')

    with st.sidebar.expander("APIキー設定"):
        st.info("ご自身のNotionとGeminiのAPIキーを入力してください。")
        with st.form("api_key_form", clear_on_submit=True):
            notion_key_input = st.text_input("Notion APIキー", type="password", key="notion_key")
            gemini_key_input = st.text_input("Gemini APIキー", type="password", key="gemini_key")
            submitted = st.form_submit_button("保存")
            if submitted:
                if notion_key_input and gemini_key_input:
                    save_api_keys_to_firestore(st.session_state["username"], notion_key_input, gemini_key_input)
                    st.success("APIキーを保存しました！")
                else:
                    st.warning("両方のAPIキーを入力してください。")

    # --- パスワードリセット機能 ---
    with st.sidebar.expander("パスワードリセット"):
        try:
            with st.form(key='reset_pw_form', clear_on_submit=True):
                st.write("現在のパスワードと新しいパスワードを入力してください。")
                current_password = st.text_input("現在のパスワード", type="password")
                new_password = st.text_input("新しいパスワード", type="password")
                new_password_repeat = st.text_input("新しいパスワード（確認）", type="password")
                
                if st.form_submit_button("パスワードをリセット"):
                    if authenticator.authentication_controller.reset_password(
                        st.session_state["username"],
                        current_password,
                        new_password,
                        new_password_repeat
                    ):
                        st.success('パスワードが正常に変更されました。データベースを更新しています...')
                        new_password_hash = config['credentials']['usernames'][st.session_state["username"]]['password']
                        if update_password_in_firestore(st.session_state["username"], new_password_hash):
                            st.success('データベースのパスワードが更新されました。')
                        else:
                            st.error('データベースのパスワード更新に失敗しました。')
        except Exception as e:
            st.error(e)

    user_api_keys = load_api_keys_from_firestore(st.session_state["username"])

    if not user_api_keys:
        st.warning("APIキーが設定されていません。サイドバーの「APIキー設定」から登録してください。")
        st.stop()

    # (ここから下のメインUIロジックは、APIクライアント初期化以外はほぼ変更なし)
    st.title("📝 Notion記事自動生成AI")
    st.markdown("Webの最新情報やお手元のドキュメントを元に、Notionページの作成から編集までを自動化します。")
    
    try:
        if st.session_state.get('current_user') != st.session_state["username"] or 'clients_initialized' not in st.session_state:
            st.session_state.notion_client = notion_client.Client(auth=user_api_keys['notion'])
            genai.configure(api_key=user_api_keys['gemini'])
            GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
            GEMINI_LITE_MODEL_NAME = os.getenv("GEMINI_LITE_MODEL", "gemini-2.5-flash-lite")
            st.session_state.gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)
            st.session_state.gemini_lite_model = genai.GenerativeModel(GEMINI_LITE_MODEL_NAME)
            st.session_state.notion_client.users.me()
            st.session_state.clients_initialized = True
            st.session_state.current_user = st.session_state["username"]
            st.toast(f"✅ APIクライアントの準備ができました")
    except Exception as e:
        st.error(f"APIクライアントの初期化中にエラーが発生しました。APIキーが正しいか確認してください。\n\nエラー詳細: {e}")
        st.stop()
    
    # (メインUIの残り... 省略)
    with st.spinner("データベースを読み込んでいます..."):
        databases = get_all_databases(st.session_state.notion_client)

    if not databases:
        st.error("アクセス可能なNotionデータベースが見つかりませんでした。")
        st.stop()

    db_options = {db['id']: db['title'] for db in databases}
    selected_db_id = st.selectbox("1. 操作するNotionデータベースを選択してください", options=db_options.keys(), format_func=lambda x: db_options[x])
    mode = st.radio("2. 実行する操作を選択してください", ("新しいページを作成する", "既存のページを編集・追記する"), horizontal=True)
    st.markdown("---")

    persona_options = {
        "プロのライター": "あなたはプロのライターです。",
        "マーケティング担当者": "あなたは経験豊富なマーケティング担当者です。読者のエンゲージメントを高めることを意識してください。",
        "技術ドキュメントライター": "あなたは正確で分かりやすい文章を書く技術ドキュメントライターです。専門用語は適切に解説してください。",
        "フレンドリーな解説者": "あなたは複雑なトピックを、親しみやすくフレンドリーな口調で解説する専門家です。",
        "社内資料作成・利用ガイドの作成者": "あなたは、社内での利用を目的とした公式な資料やガイドを作成する担当者です。以下の点を重視してください：情報の正確性と客観性、専門用語の統一、後から他の人が追記・編集しやすいような構造的な記述。",
        "カスタム": "カスタム...",
    }

    prompt_templates = {
        "記事作成": "{topic}について、読者の興味を引く魅力的な記事を作成してください。",
        "要約": "{topic}について、重要なポイントを箇条書きで分かりやすく要約してください。",
        "アイデア出し": "{topic}というテーマで、ユニークなアイデアを5つ提案してください。",
        "プレスリリース作成": "{topic}に関するプレスリリースを作成してください。背景、目的、主要な特徴、今後の展望を含めてください。",
        "利用ガイドの作成": "{topic}についての利用ガイドを作成してください。初心者でも理解できるよう、以下の構成で記述してください： 1. 概要と目的 2. 主な機能や特徴 3. 基本的な使い方（ステップバイステップ形式） 4. よくある質問（Q&A）",
        "カスタム": "カスタム...",
    }

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

elif st.session_state["authentication_status"] is False:
    st.error('ユーザー名かパスワードが間違っています')

elif st.session_state["authentication_status"] is None:
    st.warning('ユーザー名とパスワードを入力してください')
    
    # --- パスワード忘れ対応機能 ---
    with st.expander("パスワードをお忘れですか？"):
        try:
            (username_of_forgotten_password,
             email_of_forgotten_password,
             new_random_password) = authenticator.forgot_password(
                 location='main',
                 fields={'Form name': 'パスワードリセット', 'Username': 'ユーザー名', 'Submit': '送信'}
             )

            if username_of_forgotten_password:
                st.success('新しい一時パスワードが生成されました。')
                st.warning('**重要:** このパスワードを安全な場所にコピーし、ログイン後に必ずパスワードをリセットしてください。')
                st.code(new_random_password)
                
                new_password_hash = config['credentials']['usernames'][username_of_forgotten_password]['password']
                if update_password_in_firestore(username_of_forgotten_password, new_password_hash):
                    st.info('データベースのパスワードが更新されました。')
                else:
                    st.error('データベースのパスワード更新に失敗しました。')

            elif username_of_forgotten_password == False:
                st.error('ユーザー名が見つかりませんでした。')
        except Exception as e:
            st.error(e)
            
    # --- ユーザー名忘れ対応機能の追加 ---
    with st.expander("ユーザー名をお忘れですか？"):
        try:
            (username_of_forgotten_username,
             email_of_forgotten_username) = authenticator.forgot_username(
                 location='main',
                 fields={'Form name': 'ユーザー名検索', 'Email': 'メールアドレス', 'Submit': '検索'}
             )
            
            if username_of_forgotten_username:
                st.success('あなたのユーザー名はこちらです:')
                st.info(username_of_forgotten_username)
            elif username_of_forgotten_username == False:
                st.error('入力されたメールアドレスに紐づくユーザーが見つかりませんでした。')
        except Exception as e:
            st.error(e)
    # --- ここまでが追加機能 ---

    try:
        logging.info("ユーザー登録フォームの表示を開始します。")
        email, username, name = authenticator.register_user(
            location='main',
            fields={'Form name': '新規ユーザー登録', 'Username': 'ユーザー名 (半角英数字のみ)', 'Email': 'メールアドレス', 'Name': '氏名', 'Password': 'パスワード', 'Repeat password': 'パスワードを再入力', 'Register': '登録する'}
        )
        logging.info(f"register_userの戻り値: email={email}, username={username}, name={name}")

        if email:
            logging.info("ユーザー登録情報のFirestore保存処理を開始します。")

            try:
                # register_user 実行後に渡した config が更新されているはずなので直接参照
                if username in config['credentials']['usernames']:
                    hashed_password = config['credentials']['usernames'][username]['password']
                    add_or_update_user_in_firestore(username, name, email, hashed_password)
                    st.success('ユーザー登録が成功しました。再度ログインしてください。')
                else:
                    logging.error(f"ユーザー '{username}' のパスワード情報がconfigに見つかりません。")
                    st.error("登録情報の取得に失敗しました。もう一度お試しください。")

            except Exception as e:
                logging.error("Firestore保存処理でエラーが発生しました。")
                logging.error(traceback.format_exc())
                st.error("Firestoreへのユーザー登録中にエラーが発生しました。")


        else:
            logging.info("emailがNoneまたは空のため、Firestore保存処理をスキップしました。")

    except stauth.utilities.exceptions.RegisterError as e:
        error_message = str(e)
        logging.warning(f"RegisterError発生: {error_message}")
        if "Password must" in error_message:
            st.error("パスワードは以下の要件を満たす必要があります：\n- 8文字以上\n- 1つ以上の小文字を含む\n- 1つ以上の大文字を含む\n- 1つ以上の数字を含む\n- 1つ以上の特殊文字を含む (@$!%*?&)")
        elif "Captcha" in error_message:
            st.error("CAPTCHAの入力が間違っています。再度お試しください。")
        else:
            st.error(e)
    except Exception as e:
        logging.error("register_userウィジェットで予期せぬエラーが発生しました。")
        logging.error(traceback.format_exc())
        st.error(f"ユーザー登録フォームの表示中に予期せぬエラーが発生しました。")
