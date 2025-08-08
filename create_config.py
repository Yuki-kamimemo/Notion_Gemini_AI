import csv
import yaml
import streamlit_authenticator as stauth

# --- ファイルパス定義 ---
users_csv_path = "users.csv"
config_yaml_path = "config.yaml"

# --- 処理開始 ---
try:
    # ユーザーCSVファイルを読み込む
    with open(users_csv_path, "r", encoding="utf-8") as f:
        csv_reader = csv.DictReader(f)
        users = list(csv_reader)

    # config.yamlの雛形を読み込む
    with open(config_yaml_path, "r", encoding="utf-8") as f:
        yaml_data = yaml.safe_load(f)

    # ユーザー情報を処理し、辞書を作成
    users_dict = {}
    for user in users:
        # パスワードをハッシュ化
        hashed_password = stauth.Hasher([user["password"]]).generate()[0]
        
        # authenticatorが要求する形式でユーザー情報を格納
        user_info = {
            "name": user["name"],
            "password": hashed_password,
            "email": user["email"],
        }
        users_dict[user["id"]] = user_info

    # yamlデータにハッシュ化済みのユーザー情報を書き込む
    yaml_data["credentials"]["usernames"] = users_dict
    
    # 事前承認メールアドレスリストも更新
    yaml_data["preauthorized"]["emails"] = [user["email"] for user in users]

    # 完成した設定をconfig.yamlに書き出す
    with open(config_yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(yaml_data, f, default_flow_style=False, allow_unicode=True)
    
    print(f"'{config_yaml_path}' の更新が完了しました。")

except FileNotFoundError as e:
    print(f"エラー: ファイルが見つかりません - {e}")
except Exception as e:
    print(f"エラーが発生しました: {e}")