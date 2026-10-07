# -*- coding: utf-8 -*-
import json
import os
import random
import time
import uuid
import boto3
from datetime import datetime
from dotenv import load_dotenv
import streamlit as st

from aws_db import (
    load_user_history_from_aws, 
    load_suspend_state_from_aws, 
    clear_suspend_state_in_aws,
    load_bookmarks_from_aws,
    load_user_profile,
    save_user_profile
)
from dashboard import show_dashboard
from quiz_page import show_quiz_page

load_dotenv()

# --- 🗄️ セッション状態（状態管理）の初期化 ---
if 'user_name' not in st.session_state: st.session_state.user_name = None
if 'exam_code' not in st.session_state: st.session_state.exam_code = None
if 'exam_name' not in st.session_state: st.session_state.exam_name = None
if 'user_level' not in st.session_state: st.session_state.user_level = None
if 'current_index' not in st.session_state: st.session_state.current_index = 0
if 'start_time' not in st.session_state: st.session_state.start_time = None
if 'answered' not in st.session_state: st.session_state.answered = False
if 'feedback' not in st.session_state: st.session_state.feedback = ""
if 'score' not in st.session_state: st.session_state.score = 0
if 'finished' not in st.session_state: st.session_state.finished = False
if 'history' not in st.session_state: st.session_state.history = {}
if 'config_done' not in st.session_state: st.session_state.config_done = False
if 'quiz_questions' not in st.session_state: st.session_state.quiz_questions = []
if 'suspended' not in st.session_state: st.session_state.suspended = False
if 'saved_session' not in st.session_state: st.session_state.saved_session = None
if 'bookmarks' not in st.session_state: st.session_state.bookmarks = set()
if 'filter_bm' not in st.session_state: st.session_state.filter_bm = False
if 'is_over_time' not in st.session_state: st.session_state.is_over_time = False 
if 'email' not in st.session_state: st.session_state.email = ""
if 'receive_notifications' not in st.session_state: st.session_state.receive_notifications = True

# 💡 研究用ログ保存のための追加ステート
if 'activity_type' not in st.session_state: st.session_state.activity_type = "free_learning"
if 'test_run_id' not in st.session_state: st.session_state.test_run_id = ""
if 'test_set_id' not in st.session_state: st.session_state.test_set_id = ""
if 'test_attempt_no' not in st.session_state: st.session_state.test_attempt_no = 1


# ==========================================
# 管理者へのフィードバック送信関数
# ==========================================
def send_feedback_to_admin(user_id, issue_type, details, question_info=""):
    """ユーザーからの報告をSES経由で管理者のGmailへ送信する"""
    try:
        ses_client = boto3.client(
            'ses',
            region_name=os.getenv('AWS_REGION', 'ap-northeast-1'),
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
        )
        
        # 送信元・送信先ともにアプリのメアドを指定
        SENDER = "学習アプリ フィードバック機能 <exam.app.noreply@gmail.com>"
        RECEIVER = "exam.app.noreply@gmail.com"
        
        subject = f"【報告】{issue_type} - {user_id}さんより"
        body = f"""ユーザー: {user_id}
報告種別: {issue_type}
関連問題: {question_info}

【詳細】
{details}
"""
        ses_client.send_email(
            Source=SENDER,
            Destination={'ToAddresses': [RECEIVER]},
            Message={
                'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                'Body': {'Text': {'Data': body, 'Charset': 'UTF-8'}}
            }
        )
        return True
    except Exception as e:
        print(f"フィードバック送信エラー: {e}")
        return False


# --- 🛡️ 1. ログイン画面 ---
if st.session_state.user_name is None:
    st.title("🛡️ ログイン")

    with st.form("login_form"):
        st.info("💡 卒業研究への参加者は、Discord等で配布された研究用ID（例：P001）を入力してください。\n⚠️ 氏名や学籍番号ではなく、配布された研究用IDを必ず使用してください。")
        input_name = st.text_input("研究用ID")
        
        input_level = st.selectbox("現在のあなたの知識レベル", [
            "初学者（IT知識や資格学習経験が少ない）", 
            "中級者（基礎的なIT知識があり、資格学習や問題演習の経験がある）", 
            "上級者（幅広いIT知識があり、十分に学習を重ねており、資格試験の合格水準に近い）"
        ])
        input_password = st.text_input("クラス共通パスワード", type="password")
        
        st.markdown("---")
        
        with st.expander("📖 【必読】本アプリのご利用・実験参加に関する同意事項と使い方", expanded=False):
            st.markdown("""
            #### 【重要】本アプリのご利用・実験参加に関する同意事項
            本アプリは、大学の情報工学における卒業研究のための実証実験として提供されています。ご利用の前に、以下の項目をご一読いただき、同意の上で学習を開始してください。
            
            **1. データの収集とプライバシー保護について**  
            本アプリでは、学習状態・学習行動の分析およびシステムのユーザビリティ評価を目的として、以下のデータを収集・保存します。
            
            * **アカウントおよび属性情報**
              * 研究用ID（アンケート等で取得した氏名と対応付けて管理します）
              * 任意登録のメールアドレス（リマインド通知用）
              * 選択した「現在の知識レベル」
            * **学習・解答プロセスに関するデータ**
              * 各問題の選択解答、正誤結果、および解答日時
              * 1問ごとの解答に要した時間（秒単位）
              * 解答時に選択した「自信度（メタ認知の指標）」
              * 忘却曲線アルゴリズムが算出する復習スケジュールと連続正解数
            * **システム利用履歴およびフィードバック**
              * アプリへのアクセス日時、学習の中断・再開ログ、ブックマーク履歴
              * フィードバックフォームから送信された報告内容（解説の誤り指摘、バグ報告等）
            
            収集したデータは厳重に管理し、卒業論文の執筆および学術発表の目的のみに使用します。外部へのデータ提供は一切行わず、論文等で発表する際は個人が特定されない形で集計・公表します。
            **本研究では、正誤・自信度・解答時間・回答履歴などの学習ログを収集し、学習状態の変化や学習支援への活用可能性を分析します。**
            
            **2. AI（生成AI）による解説の免責事項について**  
            本アプリに収録されている問題文と正答はIPA（情報処理推進機構）の公式過去問に準拠していますが、**「問題の解説文」には複数のLLM（大規模言語モデル）を利用して作成した独自の文章が含まれます**。  
            AI生成文章のため不正確な内容（ハルシネーション）や不自然な日本語が含まれる可能性があることをあらかじめご了承ください。
            
            **3. 参加の任意性と通知の停止（オプトアウト）について**  
            本実験への参加は完全に任意であり、成績や単位等には一切影響しません。  
            また、リマインドメールの受信は、ログイン後の左側メニュー（サイドバー）にある「通知設定」のチェックを外すことで、いつでも即座に停止することができます。
            
            ---
            
            #### 💡 効率的な学習を支援するためのアプリの使い方
            本アプリは、単に過去問を解くだけでなく、忘却曲線を参考にした推定記憶保持率を用いて「復習の目安となるタイミング」で復習ができるよう設計されています。
            
            **機能1：解答時の「自信度（メタ認知）」の入力**  
            問題を解く際、単に選択肢を選ぶだけでなく「どのくらい自信を持って答えたか」を入力してください。
            * **勘で正解した場合**：低い自信度での正答と判定され、早めに復習に回されます。
            * **自信ありで間違えた場合**：高い自信度での誤答（思い込み）と判定され、最優先の復習対象として即座にピックアップされます。
            
            **機能2：忘却曲線ベースの「自動リマインドメール」**  
            過去の解答履歴と自信度から、システムがあなたの「推定記憶保持率」を裏側で計算し続けます。記憶が薄れて「推定記憶保持率が40%を下回った問題」や「自信ありで間違えた危険な問題」があると、朝8時と夜20時（1日2回）にリマインドメールが届きます。
            
            **機能3：研究へのご協力「フィードバック機能」**  
            学習中、「AIの解説が明らかにおかしい」「システムがフリーズした」などの問題を見つけた場合は、画面左側のメニュー（💬 バグ・問題の解説ミスを報告する）からご報告をお願いします。
            """)

        agree_checkbox = st.checkbox("上記の「利用規約および実験参加の同意事項」に同意する")
        submit_btn = st.form_submit_button("学習を開始する")
        
        if submit_btn:
            if not input_name: 
                st.warning("⚠️ 研究用IDを入力してください。")
            elif not agree_checkbox:
                st.warning("⚠️ 実験に参加するには、利用規約への同意が必要です。上のチェックボックスにチェックを入れてください。")
            elif input_password != os.getenv('APP_PASSWORD', 'Exam_Learning'): 
                st.error("❌ パスワードが正しくありません。")
            else:
                st.session_state.user_name = input_name
                st.session_state.user_level = input_level
                st.session_state.exam_name = "基本情報技術者試験A"
                st.session_state.exam_code = "FEA"
                
                with st.spinner("☁️ AWSから過去の学習データと中断データを同期しています..."):
                    profile = load_user_profile(input_name)
                    
                    final_email = profile.get('email', '')
                    final_notif = profile.get('receive_notifications', True)

                    st.session_state.email = final_email
                    st.session_state.receive_notifications = final_notif

                    st.session_state.history = load_user_history_from_aws(input_name)
                    st.session_state.bookmarks = load_bookmarks_from_aws(input_name)
                    saved_state = load_suspend_state_from_aws(input_name)
                    
                    if saved_state:
                        st.session_state.saved_session = saved_state
                        st.session_state.suspended = True
                    else:
                        st.session_state.saved_session = None
                        st.session_state.suspended = False
                
                st.session_state.config_done = False
                st.rerun()
    st.stop()


# --- 📦 2. ローカルJSONデータロード関数 ---
@st.cache_data
def load_data(exam_code):
    file_map = {"FEA": "FEA5-7_ALL_QA.json", "FEB": "FEB5-7_ALL_QA.json", "IP": "IP3-8_ALL_QA.json"}
    file_name = file_map.get(exam_code)
    if not file_name or not os.path.exists(file_name): return []
    with open(file_name, "r", encoding="utf-8") as f: data = json.load(f)
    
    for q in data:
        opts = q.get("options", {})
        if not opts.get("ア"):
            q_text = q.get("question", "")
            idx_a, idx_i, idx_u, idx_e = q_text.find("ア "), q_text.find("イ "), q_text.find("ウ "), q_text.find("エ ")
            if idx_a != -1 and idx_i != -1 and idx_u != -1 and idx_e != -1:
                q["question"] = q_text[:idx_a].strip()
                q["options"] = {
                    "ア": q_text[idx_a:idx_i].strip().lstrip("ア").strip(),
                    "イ": q_text[idx_i:idx_u].strip().lstrip("イ").strip(),
                    "ウ": q_text[idx_u:idx_e].strip().lstrip("ウ").strip(),
                    "エ": q_text[idx_e:].strip().lstrip("エ").strip()
                }
        orig_type = q.get("question_type", "")
        if "アルゴリズム" in orig_type or "プログラム" in orig_type: q["question_type"] = "アルゴリズム問題"
        elif "計算" in orig_type and "図" in orig_type: q["question_type"] = "図表計算問題"
        elif "計算" in orig_type: q["question_type"] = "計算問題"
        elif "図" in orig_type or "表" in orig_type or "SQL" in orig_type: q["question_type"] = "図表問題"
        else: q["question_type"] = "知識問題"
    return data

@st.cache_data
def load_assessment_sets():
    """初回確認テスト・任意実力テストの条件用JSONをロード"""
    if os.path.exists("assessment_sets.json"):
        try:
            with open("assessment_sets.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

questions = load_data(st.session_state.exam_code)

# --- 👤 3. メインサイドバーメニュー ---
st.sidebar.title(f"👤 メニュー")
app_mode = st.sidebar.radio("📋 機能を切り替える", ["クイズ学習", "学習ダッシュボード"])

if st.session_state.user_name:
    st.sidebar.markdown("---")
    st.sidebar.subheader("🔔 通知設定")
    st.sidebar.caption("※リマインド通知用のメールアドレス登録・変更はこちらで行えます。通知を停止したい場合はチェックを外してください。")
    
    current_email = st.session_state.get('email', '')
    current_notif = st.session_state.get('receive_notifications', True)

    # 💡 状態だけを表示し、メアド自体は画面に出さない
    if current_email:
        st.sidebar.caption("現在の状態: **✅ アドレス登録済み**")
    else:
        st.sidebar.caption("現在の状態: **❌ アドレス未登録**")

    with st.sidebar.form("notification_form"):
        # 💡 個人情報流出防止のため、登録済みのメアドは画面に出さず常に空欄（value=""）にする
        new_email = st.text_input("新しいメールアドレス（変更・登録時のみ入力）", value="")
        new_notif = st.checkbox("学習リマインド通知を受け取る", value=current_notif)
        
        submit_notif = st.form_submit_button("更新する")

    if submit_notif:
        # 💡 入力が空欄のまま更新ボタンを押した場合は、データベースにある既存のメアドを維持する
        final_email = new_email.strip() if new_email.strip() else current_email
        st.session_state.email = final_email
        st.session_state.receive_notifications = new_notif
        save_user_profile(st.session_state.user_name, final_email, new_notif)
        st.sidebar.success("✅ 通知設定を更新しました")
        st.rerun()

    st.sidebar.markdown("---")
    with st.sidebar.expander("💬 バグ・問題の解説ミスを報告する"):
        with st.form("feedback_form", clear_on_submit=True):
            issue_type = st.selectbox(
                "報告の種類", 
                ["問題の解説が間違っている/不十分", "システムのエラー・バグ", "機能の要望", "その他"]
            )
            question_info = st.text_input("関連する問題番号（任意）", placeholder="例: 基本情報 令和5年 問1")
            details = st.text_area("詳細をお書きください（必須）")
            
            submitted = st.form_submit_button("管理者に報告を送信")

            if submitted:
                if not details.strip():
                    st.warning("詳細を入力してください。")
                else:
                    success = send_feedback_to_admin(st.session_state.user_name, issue_type, details, question_info)
                    if success:
                        st.success("報告を送信しました。ご協力ありがとうございます！")
                    else:
                        st.error("送信に失敗しました。時間をおいて再度お試しください。")

if st.sidebar.button("ログアウト"): 
    st.session_state.clear()
    st.rerun()


# --- 📊 4. ダッシュボード表示モード ---
if app_mode == "学習ダッシュボード":
    show_dashboard(questions, st.session_state.history, st.session_state.exam_code)
    st.stop()


# --- ⚙️ 5. クイズの出題設定 ---
if not st.session_state.config_done:
    st.title("⚙️ 出題設定")
    
    if st.button("🔄 前回の続きから", use_container_width=True):
        if st.session_state.suspended and st.session_state.saved_session:
            ss = st.session_state.saved_session
            st.session_state.exam_code = ss['exam_code']
            
            exam_options = {"FEA": "基本情報技術者試験A", "FEB": "基本情報技術者試験B", "IP": "ITパスポート"}
            st.session_state.exam_name = exam_options.get(ss['exam_code'], "基本情報技術者試験A")
            
            all_q = load_data(ss['exam_code'])
            
            restored_qs = []
            for sq in ss['q_keys']:
                match = next((q for q in all_q if q.get('year') == sq['year'] and int(q.get('id', 0)) == int(sq['id'])), None)
                if match: restored_qs.append(match)
            
            st.session_state.quiz_questions = restored_qs
            st.session_state.current_index = int(ss['current_index'])
            st.session_state.config_done = True
            st.session_state.is_over_time = False 
            st.session_state.start_time = time.time()
            st.rerun()
        else:
            st.warning("前回の中断ポイントがありません。")

    st.write("---")

    exam_options = {"FEA": "基本情報技術者試験A", "FEB": "基本情報技術者試験B", "IP": "ITパスポート"}
    current_exam_name = exam_options.get(st.session_state.exam_code, "基本情報技術者試験A")
    
    selected_exam_name = st.selectbox("🔍 出題する試験を切り替え", options=list(exam_options.values()), index=list(exam_options.values()).index(current_exam_name), key="quiz_exam_select")
    selected_exam_code = [k for k, v in exam_options.items() if v == selected_exam_name][0]
    
    if selected_exam_code != st.session_state.exam_code:
        st.session_state.exam_code = selected_exam_code
        st.session_state.exam_name = selected_exam_name
        st.session_state.config_done = False
        st.session_state.quiz_questions = []
        st.session_state.current_index = 0
        st.session_state.answered = False
        st.session_state.filter_bm = False
        with st.spinner("☁️ AWSから過去の学習データを同期しています..."):
            st.session_state.history = load_user_history_from_aws(st.session_state.user_name)
        st.rerun()

    st.write("---")

    year_options = sorted(list(set([q["year"] for q in questions if q.get("year")])))
    category_options = sorted(list(set([q["category_large"] for q in questions if q.get("category_large")])))
    
    def format_year_label(y):
        num_str = y.replace('IP', '').replace('FEA', '').replace('FEB', '')
        return f"{num_str}年度"

    col1, col2, col3 = st.columns(3)
    with col1: selected_years = [y for y in year_options if st.checkbox(format_year_label(y), value=True, key=f"y_{y}")]
    with col2: selected_categories = [c for c in category_options if st.checkbox(c, value=True, key=f"c_{c}")]
    with col3: selected_types = [t for t in ["知識問題", "計算問題", "図表問題", "図表計算問題", "アルゴリズム問題"] if st.checkbox(t, value=True, key=f"t_{t}")]

    status_options = ["すべて", "未解答のみ", "要復習（忘却曲線ベースすべて）", "🚨 今すぐ必要", "📅 明日以内", "⏳ 3日以内", "🗓️ 1週間以内", "✅ 安全圏 (1週間以上先)"]
    selected_status = st.selectbox("📊 過去の解答状態・未来予測スケジュールで絞り込む", status_options)
    
    filter_bookmarked = st.checkbox("🔖 ブックマークした問題のみ出題する", key="filter_bm")
    
    selected_order = st.radio("🔀 出題順序", ["順番通り", "ランダム"], horizontal=True)

    temp_filtered = []
    for q in questions:
        check_key = f"{st.session_state.exam_code}_{q.get('year', '不明')}_{q.get('id', 0)}"
        hist = st.session_state.history.get(check_key, {})
        if q.get("year") not in selected_years or q.get("category_large") not in selected_categories or q.get("question_type") not in selected_types: continue
        if selected_status == "未解答のみ" and hist.get('solve_count', 0) > 0: continue
        if selected_status == "要復習（忘却曲線ベースすべて）" and (hist.get('solve_count', 0) == 0 or not hist.get('needs_review', False)): continue
        
        if selected_status in ["🚨 今すぐ必要", "📅 明日以内", "⏳ 3日以内", "🗓️ 1週間以内", "✅ 安全圏 (1週間以上先)"]:
            if hist.get('solve_count', 0) == 0: continue
            days = hist.get('days_until_review', 0.0)
            if days <= 0: current_sched_group = "🚨 今すぐ必要"
            elif days <= 1: current_sched_group = "📅 明日以内"
            elif days <= 3: current_sched_group = "⏳ 3日以内"
            elif days <= 7: current_sched_group = "🗓️ 1週間以内"
            else: current_sched_group = "✅ 安全圏 (1週間以上先)"
            if selected_status != current_sched_group: continue
            
        if filter_bookmarked and check_key not in st.session_state.bookmarks:
            continue
            
        temp_filtered.append(q)

    if temp_filtered:
        total_count = len(temp_filtered)
        
        if total_count == 1:
            st.info("ℹ️ 該当する問題が 1 件のみのため、1問に設定されました。")
            max_questions = 1
        else:
            max_questions = st.slider("出題問題数", 1, total_count, total_count)
            
        st.write("---")
        
        profile = load_user_profile(st.session_state.user_name)
        initial_checks = profile.get("initial_checks", {})
        is_initial_completed = initial_checks.get(st.session_state.exam_code, {}).get("completed", False)
        
        if not is_initial_completed:
            st.warning(f"⚠️ 【{current_exam_name}】の初回確認テストが未完了です。")
            st.info("※全問解答後に通常の自由学習が利用可能になります。（上の出題設定は無視され、固定問題が出題されます）")
            
            if st.button("初回確認テストを開始する 🚀", type="primary", use_container_width=True):
                sets = load_assessment_sets()
                exam_set = sets.get(st.session_state.exam_code, {})
                initial_set = exam_set.get("initial_check", {})
                target_q_refs = initial_set.get("questions", [])
                
                test_qs = []
                for ref in target_q_refs:
                    match = next((q for q in questions if q.get('year') == ref.get('year') and int(q.get('id', 0)) == int(ref.get('id'))), None)
                    if match:
                        test_qs.append(match)
                        
                if not test_qs:
                    st.error("初回確認テストの問題が assessment_sets.json から見つかりません。")
                    st.stop()
                    
                st.session_state.quiz_questions = test_qs
                st.session_state.config_done = True
                st.session_state.current_index = 0
                st.session_state.score = 0
                st.session_state.answered = False
                st.session_state.suspended = False
                st.session_state.saved_session = None
                st.session_state.is_over_time = False 

                st.session_state.activity_type = "initial_check"
                st.session_state.test_run_id = uuid.uuid4().hex
                st.session_state.test_set_id = initial_set.get("set_id", f"{st.session_state.exam_code}_initial_v1")
                st.session_state.test_attempt_no = 1
                
                st.session_state.start_time = time.time()
                st.rerun()
        else:
            if st.button("クイズを開始する 🚀", type="primary", use_container_width=True):
                if selected_order == "ランダム": random.shuffle(temp_filtered)
                st.session_state.quiz_questions = temp_filtered[:max_questions]
                st.session_state.config_done = True
                st.session_state.current_index = 0
                st.session_state.score = 0
                st.session_state.answered = False
                
                st.session_state.suspended = False
                st.session_state.saved_session = None
                st.session_state.is_over_time = False 
                clear_suspend_state_in_aws(st.session_state.user_name)
                
                st.session_state.activity_type = "free_learning"
                st.session_state.test_run_id = ""
                st.session_state.test_set_id = ""
                st.session_state.test_attempt_no = 1
                
                st.session_state.start_time = time.time()
                st.rerun()
                
            with st.expander("🎯 任意実力テストを受験する (補助機能)"):
                st.info("※初回確認テスト以外の問題から、ランダムで実力テストを出題します。")
                
                if st.button("実力テストを開始する 🚀", use_container_width=True):
                    sets = load_assessment_sets()
                    ability_set = sets.get(st.session_state.exam_code, {}).get("ability_test", {})
                    req_count = ability_set.get("question_count", 5)
                    balance_fields = ability_set.get("balance_fields", ["category_large", "difficulty", "question_type"])
                    
                    initial_refs = sets.get(st.session_state.exam_code, {}).get("initial_check", {}).get("questions", [])
                    initial_keys = [f"{r.get('year')}_{r.get('id')}" for r in initial_refs]
                    
                    candidates = [q for q in questions if f"{q.get('year')}_{q.get('id')}" not in initial_keys]
                    
                    unanswered = []
                    answered = []
                    for q in candidates:
                        q_key = f"{st.session_state.exam_code}_{q.get('year')}_{q.get('id')}"
                        if st.session_state.history.get(q_key, {}).get('solve_count', 0) == 0:
                            unanswered.append(q)
                        else:
                            answered.append(q)
                            
                    def select_balanced_questions(candidates_pool, needed_count, current_selected):
                        """Greedy方式による条件付きランダム抽出関数"""
                        selected = []
                        pool = candidates_pool.copy()
                        while len(selected) < needed_count and pool:
                            all_selected = current_selected + selected
                            counts = {f: {} for f in balance_fields}
                            for sq in all_selected:
                                for f in balance_fields:
                                    val = str(sq.get(f, "Unknown"))
                                    counts[f][val] = counts[f].get(val, 0) + 1
                            
                            min_score = float('inf')
                            best_candidates = []
                            for q in pool:
                                score = 0
                                for f in balance_fields:
                                    val = str(q.get(f, "Unknown"))
                                    score += counts[f].get(val, 0)
                                
                                if score < min_score:
                                    min_score = score
                                    best_candidates = [q]
                                elif score == min_score:
                                    best_candidates.append(q)
                            
                            chosen = random.choice(best_candidates)
                            selected.append(chosen)
                            pool.remove(chosen)
                        return selected

                    selected_qs = []
                    if len(unanswered) > 0:
                        needed = min(req_count, len(unanswered))
                        selected_qs.extend(select_balanced_questions(unanswered, needed, selected_qs))
                    
                    if len(selected_qs) < req_count and len(answered) > 0:
                        needed = req_count - len(selected_qs)
                        selected_qs.extend(select_balanced_questions(answered, needed, selected_qs))
                    
                    st.session_state.quiz_questions = selected_qs
                    st.session_state.config_done = True
                    st.session_state.current_index = 0
                    st.session_state.score = 0
                    st.session_state.answered = False
                    st.session_state.suspended = False
                    st.session_state.saved_session = None
                    st.session_state.is_over_time = False 

                    st.session_state.activity_type = "ability_test"
                    st.session_state.test_run_id = uuid.uuid4().hex
                    st.session_state.test_set_id = ability_set.get("rule_id", f"{st.session_state.exam_code}_ability_random_v1")
                    st.session_state.test_attempt_no = 1
                    
                    st.session_state.start_time = time.time()
                    st.rerun()

    else:
        st.warning("⚠️ 選択した条件に合致する問題がありません。絞り込みを緩めてください。")
        
    st.write("---")
    st.caption("※ 本アプリの問題文・正答はIPA（情報処理推進機構）の公表資料を利用しています。  \n※ 解説文はLLM（AI）により自動生成された独自コンテンツです。")
    
    st.stop()


# --- 🎊 6. クイズの全問終了画面 ---
if st.session_state.finished:
    st.title("🎊 学習完了！")
    st.markdown(f"### 今回の成果: **{st.session_state.score} / {len(st.session_state.quiz_questions)}** 問正解")
    
    if st.session_state.get("activity_type") == "initial_check":
        profile = load_user_profile(st.session_state.user_name)
        initial_checks = profile.get("initial_checks", {})
        
        if not initial_checks.get(st.session_state.exam_code, {}).get("completed"):
            initial_checks[st.session_state.exam_code] = {
                "completed": True,
                "completed_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "completed_run_id": st.session_state.test_run_id,
                "test_set_id": st.session_state.test_set_id,
                "score": st.session_state.score,
                "total": len(st.session_state.quiz_questions)
            }
            try:
                dynamodb = boto3.resource('dynamodb', region_name=os.getenv('AWS_REGION'))
                table = dynamodb.Table('Exam_Learning_Users')
                table.update_item(
                    Key={'user_id': str(st.session_state.user_name)},
                    UpdateExpression="SET initial_checks = :ic",
                    ExpressionAttributeValues={":ic": initial_checks}
                )
                st.success("🎉 初回確認テストが完了しました！次回から自由学習が利用できます。")
            except Exception as e:
                st.error(f"初回確認テストの完了保存に失敗しました: {e}")

    if st.button("トップへ戻り、もう一度設定する", use_container_width=True, type="primary"): 
        st.session_state.config_done = False
        st.session_state.finished = False
        st.session_state.quiz_questions = []
        
        st.session_state.suspended = False
        st.session_state.saved_session = None
        st.session_state.is_over_time = False 
        clear_suspend_state_in_aws(st.session_state.user_name)
        
        st.rerun()
    st.stop()


# --- 📝 7. クイズ学習画面 ---
if st.session_state.config_done and not st.session_state.finished:
    if st.session_state.current_index >= len(st.session_state.quiz_questions):
        st.session_state.finished = True
        st.rerun()
    else:
        current_q = st.session_state.quiz_questions[st.session_state.current_index]
        show_quiz_page(current_q)