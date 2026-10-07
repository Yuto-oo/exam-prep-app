# -*- coding: utf-8 -*-
"""
卒業研究用 学習ログ分析スクリプト (research_analysis.py)
用途: DynamoDBから各種ログを取得し、卒論用のCSVを出力する
"""

import os
import boto3
import pandas as pd
import numpy as np
from datetime import datetime
from dotenv import load_dotenv
from decimal import Decimal

load_dotenv()

# --- 設定 ---
LOGS_TABLE_NAME = 'Exam_Learning_Logs'
USERS_TABLE_NAME = 'Exam_Learning_Users'
REMINDERS_TABLE_NAME = 'Exam_Learning_Reminder_Logs' 
OUTPUT_DIR = 'research_results'

def get_dynamodb_resource():
    return boto3.resource(
        'dynamodb',
        region_name=os.getenv('AWS_REGION', 'ap-northeast-1'),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
    )

def fetch_all_logs():
    print("☁️ DynamoDBから学習ログ(Exam_Learning_Logs)を取得中...")
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(LOGS_TABLE_NAME)
    response = table.scan()
    items = response.get('Items', [])
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        items.extend(response.get('Items', []))
    print(f"✅ 計 {len(items)} 件のログを取得しました。")
    return items

def fetch_all_users():
    print("☁️ DynamoDBからユーザー情報(Exam_Learning_Users)を取得中...")
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(USERS_TABLE_NAME)
    response = table.scan()
    items = response.get('Items', [])
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        items.extend(response.get('Items', []))
    print(f"✅ 計 {len(items)} 件のユーザー情報を取得しました。")
    return items

def fetch_all_reminders():
    print("☁️ DynamoDBからリマインド履歴(Exam_Learning_Reminder_Logs)を取得中...")
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(REMINDERS_TABLE_NAME)
    try:
        response = table.scan()
        items = response.get('Items', [])
        while 'LastEvaluatedKey' in response:
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))
        print(f"✅ 計 {len(items)} 件のリマインド履歴を取得しました。")
        return items
    except Exception as e:
        print(f"⚠️ リマインド履歴テーブルが見つかりません（初回実行前等）: {e}")
        return []

def convert_decimals(obj):
    if isinstance(obj, list):
        return [convert_decimals(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, Decimal):
        return float(obj) if obj % 1 else int(obj)
    return obj

def preprocess_logs(raw_items):
    print("🧹 データの前処理を実行中...")
    items = convert_decimals(raw_items)
    df = pd.DataFrame(items)
    if df.empty: return df

    if 'activity_type' not in df.columns: df['activity_type'] = 'free_learning'
    else: df['activity_type'] = df['activity_type'].fillna('free_learning')

    df['is_correct'] = df['is_correct'].astype(bool)
    df['time_taken'] = pd.to_numeric(df['time_taken'], errors='coerce').fillna(0.0)

    if 'answer_confidence' not in df.columns: df['answer_confidence'] = '少し自信あり'
    else: df['answer_confidence'] = df['answer_confidence'].fillna('少し自信あり')
    
    if 'valid_for_time_analysis' not in df.columns: df['valid_for_time_analysis'] = df['time_taken'] < 900.0
    else: df['valid_for_time_analysis'] = df['valid_for_time_analysis'].fillna(df['time_taken'] < 900.0).astype(bool)
        
    df['valid_time_taken'] = df.apply(lambda row: row['time_taken'] if row['valid_for_time_analysis'] and row['time_taken'] < 900.0 else np.nan, axis=1)

    if 'predicted_retention_before_answer' not in df.columns: df['predicted_retention_before_answer'] = np.nan
    else: df['predicted_retention_before_answer'] = pd.to_numeric(df['predicted_retention_before_answer'], errors='coerce')
    
    if 'retention_model_version' not in df.columns: df['retention_model_version'] = 'unknown'
    else: df['retention_model_version'] = df['retention_model_version'].fillna('unknown')

    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    return df

def analyze_individual_transitions(df):
    print("👤 個人・再回答のトランジション（推移）を分析中...")
    df_sorted = df.sort_values(by=['user_id', 'exam_code', 'year', 'question_id', 'timestamp'])
    transitions = []
    grouped = df_sorted.groupby(['user_id', 'exam_code', 'year', 'question_id'])
    for name, group in grouped:
        if len(group) >= 2:
            first_attempt = group.iloc[0]
            second_attempt = group.iloc[1]
            days_diff = (second_attempt['timestamp'] - first_attempt['timestamp']).total_seconds() / 86400.0
            transitions.append({
                'user_id': name[0], 'exam_code': name[1], 'year': name[2], 'question_id': name[3],
                'activity_type_1st': first_attempt['activity_type'], 'activity_type_2nd': second_attempt['activity_type'],
                'timestamp_1st': first_attempt['timestamp'], 'timestamp_2nd': second_attempt['timestamp'],
                'days_between': days_diff, 'is_correct_1st': first_attempt['is_correct'], 'is_correct_2nd': second_attempt['is_correct'],
                'correctness_transition': f"{first_attempt['is_correct']} -> {second_attempt['is_correct']}",
                'confidence_1st': first_attempt['answer_confidence'], 'confidence_2nd': second_attempt['answer_confidence'],
                'time_1st': first_attempt['valid_time_taken'], 'time_2nd': second_attempt['valid_time_taken'],
                'time_diff': second_attempt['valid_time_taken'] - first_attempt['valid_time_taken'] if pd.notnull(first_attempt['valid_time_taken']) and pd.notnull(second_attempt['valid_time_taken']) else np.nan,
                'predicted_retention_before_2nd': second_attempt['predicted_retention_before_answer'],
                'retention_model_version': second_attempt['retention_model_version']
            })
    return pd.DataFrame(transitions)

def analyze_retention_accuracy(df_transitions):
    print("🧠 推定記憶保持率モデルの精度を分析中...")
    df_ret = df_transitions.dropna(subset=['predicted_retention_before_2nd']).copy()
    if df_ret.empty: return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    bins = [0, 20, 40, 60, 80, 100]
    labels = ['0-20%', '20-40%', '40-60%', '60-80%', '80-100%']
    df_ret['retention_bin'] = pd.cut(df_ret['predicted_retention_before_2nd'], bins=bins, labels=labels, include_lowest=True)
    bin_analysis = df_ret.groupby('retention_bin', observed=False).agg(sample_count=('is_correct_2nd', 'count'), actual_correct_rate=('is_correct_2nd', 'mean')).reset_index()
    bin_analysis['actual_correct_rate'] = (bin_analysis['actual_correct_rate'] * 100).round(2)
    
    # 💡 60%以下だった閾値フラグを「40%以下」に修正
    df_ret['is_under_40'] = df_ret['predicted_retention_before_2nd'] <= 40.0
    df_ret['threshold_label'] = df_ret['is_under_40'].map({True: '40%以下 (要復習)', False: '40%超 (非要復習)'})
    
    threshold_analysis = df_ret.groupby('threshold_label').agg(sample_count=('is_correct_2nd', 'count'), actual_correct_rate=('is_correct_2nd', 'mean')).reset_index()
    threshold_analysis['actual_correct_rate'] = (threshold_analysis['actual_correct_rate'] * 100).round(2)
    version_counts = df_ret['retention_model_version'].value_counts().reset_index()
    version_counts.columns = ['model_version', 'sample_count']
    return bin_analysis, threshold_analysis, version_counts

def analyze_global_questions(df):
    print("🌍 問題ごとの全体特性を分析中...")
    df_sorted = df.sort_values(by=['user_id', 'exam_code', 'year', 'question_id', 'timestamp'])
    grouped_q = df_sorted.groupby(['exam_code', 'year', 'question_id'])
    question_stats = []
    for name, group in grouped_q:
        first_attempts = group.drop_duplicates(subset=['user_id'], keep='first')
        is_high_conf_wrong = (~group['is_correct']) & (group['answer_confidence'] == '自信あり')
        question_stats.append({
            'exam_code': name[0], 'year': name[1], 'question_id': name[2],
            'unique_user_count': group['user_id'].nunique(),
            'first_attempt_count': len(first_attempts),
            'first_attempt_correct_rate_pct': round(first_attempts['is_correct'].mean() * 100, 2),
            'first_attempt_median_time_sec': round(first_attempts['valid_time_taken'].median(), 2) if pd.notnull(first_attempts['valid_time_taken'].median()) else np.nan,
            'total_attempts': len(group), 'high_confidence_wrong_count': is_high_conf_wrong.sum(),
            'high_confidence_wrong_rate_pct': round((is_high_conf_wrong.sum() / len(group)) * 100, 2) if len(group) > 0 else 0
        })
    return pd.DataFrame(question_stats)

def analyze_initial_and_free_learning(df, raw_users):
    print("📊 初回確認テストと自由学習の関係(RQ1)を分析中...")
    valid_runs = {}
    for u in raw_users:
        uid = u.get('user_id')
        checks = u.get('initial_checks', {})
        for exam, data in checks.items():
            if data.get('completed') is True and data.get('completed_run_id'):
                valid_runs[(uid, exam)] = data.get('completed_run_id')
                
    def check_formal(row):
        if row['activity_type'] != 'initial_check': return False
        return valid_runs.get((row['user_id'], row['exam_code'])) == row['test_run_id']

    df_initial = df[df['activity_type'] == 'initial_check'].copy()
    if not df_initial.empty: df_initial['is_formal_run'] = df_initial.apply(check_formal, axis=1)
    else: df_initial['is_formal_run'] = pd.Series(dtype=bool)

    df_incomplete = df_initial[~df_initial['is_formal_run']]
    if not df_incomplete.empty:
        df_inc_agg = df_incomplete.groupby(['user_id', 'exam_code', 'test_run_id']).agg(answer_count=('question_id', 'count'), first_timestamp=('timestamp', 'min'), last_timestamp=('timestamp', 'max')).reset_index()
    else:
        df_inc_agg = pd.DataFrame(columns=['user_id', 'exam_code', 'test_run_id', 'answer_count', 'first_timestamp', 'last_timestamp'])

    df_formal = df_initial[df_initial['is_formal_run']]
    if not df_formal.empty:
        df_form_agg = df_formal.groupby(['user_id', 'exam_code', 'test_run_id']).agg(
            initial_score=('is_correct', 'sum'), initial_total=('is_correct', 'count'),
            initial_correct_rate=('is_correct', lambda x: round(x.mean() * 100, 2)), initial_median_time_sec=('valid_time_taken', 'median'),
            confident_count=('answer_confidence', lambda x: (x == '自信あり').sum()), somewhat_confident_count=('answer_confidence', lambda x: (x == '少し自信あり').sum()), low_confidence_count=('answer_confidence', lambda x: (x == '自信なし（勘）').sum()),
        ).reset_index()
        df_form_agg['confident_rate'] = round((df_form_agg['confident_count'] / df_form_agg['initial_total']) * 100, 2)
        df_form_agg['somewhat_confident_rate'] = round((df_form_agg['somewhat_confident_count'] / df_form_agg['initial_total']) * 100, 2)
        df_form_agg['low_confidence_rate'] = round((df_form_agg['low_confidence_count'] / df_form_agg['initial_total']) * 100, 2)
        high_conf_wrong = df_formal[(~df_formal['is_correct']) & (df_formal['answer_confidence'] == '自信あり')]
        hcw_counts = high_conf_wrong.groupby(['user_id', 'exam_code', 'test_run_id']).size().reset_index(name='high_confidence_wrong_count')
        df_form_agg = pd.merge(df_form_agg, hcw_counts, on=['user_id', 'exam_code', 'test_run_id'], how='left')
        df_form_agg['high_confidence_wrong_count'] = df_form_agg['high_confidence_wrong_count'].fillna(0).astype(int)
    else:
        df_form_agg = pd.DataFrame(columns=['user_id', 'exam_code', 'test_run_id', 'initial_score', 'initial_total', 'initial_correct_rate', 'initial_median_time_sec', 'confident_count', 'somewhat_confident_count', 'low_confidence_count', 'confident_rate', 'somewhat_confident_rate', 'low_confidence_rate', 'high_confidence_wrong_count'])

    df_free = df[df['activity_type'] == 'free_learning'].copy()
    if not df_free.empty:
        df_free['q_key'] = df_free['year'].astype(str) + '_' + df_free['question_id'].astype(str)
        df_free['date'] = df_free['timestamp'].dt.date
        df_free_agg = df_free.groupby(['user_id', 'exam_code']).agg(
            free_attempt_count=('is_correct', 'count'), free_unique_question_count=('q_key', 'nunique'), free_learning_days=('date', 'nunique'),
            free_correct_rate=('is_correct', lambda x: round(x.mean() * 100, 2)), free_median_time_sec=('valid_time_taken', 'median'),
            confident_count=('answer_confidence', lambda x: (x == '自信あり').sum()), somewhat_confident_count=('answer_confidence', lambda x: (x == '少し自信あり').sum()), low_confidence_count=('answer_confidence', lambda x: (x == '自信なし（勘）').sum()),
        ).reset_index()
        high_conf_wrong_free = df_free[(~df_free['is_correct']) & (df_free['answer_confidence'] == '自信あり')]
        hcw_counts_free = high_conf_wrong_free.groupby(['user_id', 'exam_code']).size().reset_index(name='high_confidence_wrong_count')
        df_free_agg = pd.merge(df_free_agg, hcw_counts_free, on=['user_id', 'exam_code'], how='left')
        df_free_agg['high_confidence_wrong_count'] = df_free_agg['high_confidence_wrong_count'].fillna(0).astype(int)
        q_counts = df_free.groupby(['user_id', 'exam_code', 'q_key']).size().reset_index(name='solve_cnt')
        reanswered = q_counts[q_counts['solve_cnt'] >= 2].groupby(['user_id', 'exam_code']).size().reset_index(name='reanswered_question_count')
        df_free_agg = pd.merge(df_free_agg, reanswered, on=['user_id', 'exam_code'], how='left')
        df_free_agg['reanswered_question_count'] = df_free_agg['reanswered_question_count'].fillna(0).astype(int)
    else:
        df_free_agg = pd.DataFrame(columns=['user_id', 'exam_code', 'free_attempt_count', 'free_unique_question_count', 'free_learning_days', 'free_correct_rate', 'free_median_time_sec', 'confident_count', 'somewhat_confident_count', 'low_confidence_count', 'high_confidence_wrong_count', 'reanswered_question_count'])

    df_merged = pd.merge(df_form_agg, df_free_agg, on=['user_id', 'exam_code'], how='outer', suffixes=('_initial', '_free'))
    return df_form_agg, df_merged, df_inc_agg

def analyze_reminder_impact(df_logs, raw_reminders):
    print("📬 リマインド送信後の再回答行動(サブ分析)を処理中...")
    if not raw_reminders:
        return pd.DataFrame()
    
    df_rem = pd.DataFrame(convert_decimals(raw_reminders))
    if df_rem.empty: return pd.DataFrame()
        
    df_rem['reminder_sent_at'] = pd.to_datetime(df_rem['sent_at'], errors='coerce')
    df_rem['question_id'] = df_rem['question_id'].astype(str)
    
    df_l = df_logs.copy()
    df_l['question_id'] = df_l['question_id'].astype(str)
    df_l = df_l.sort_values('timestamp')
    
    results = []
    
    for _, rem in df_rem.iterrows():
        u_id = rem['user_id']
        e_code = rem['exam_code']
        yr = rem['year']
        q_id = rem['question_id']
        sent_time = rem['reminder_sent_at']
        
        mask = (
            (df_l['user_id'] == u_id) & (df_l['exam_code'] == e_code) &
            (df_l['year'] == yr) & (df_l['question_id'] == q_id) &
            (df_l['timestamp'] > sent_time)
        )
        future_logs = df_l[mask]
        
        if not future_logs.empty:
            next_log = future_logs.iloc[0]
            next_ans_at = next_log['timestamp']
            hours_diff = (next_ans_at - sent_time).total_seconds() / 3600.0
            
            results.append({
                'user_id': u_id, 'exam_code': e_code, 'year': yr, 'question_id': q_id,
                'reminder_reason': rem.get('reminder_reason', ''),
                'reminder_sent_at': sent_time,
                'predicted_retention_at_send': rem.get('predicted_retention_at_send'),
                'next_answer_at': next_ans_at,
                'hours_until_next_answer': round(hours_diff, 2),
                'next_is_correct': next_log['is_correct'],
                'next_confidence': next_log['answer_confidence'],
                'next_response_time': next_log.get('valid_time_taken', np.nan)
            })
        else:
            results.append({
                'user_id': u_id, 'exam_code': e_code, 'year': yr, 'question_id': q_id,
                'reminder_reason': rem.get('reminder_reason', ''), 'reminder_sent_at': sent_time,
                'predicted_retention_at_send': rem.get('predicted_retention_at_send'),
                'next_answer_at': pd.NaT, 'hours_until_next_answer': np.nan,
                'next_is_correct': np.nan, 'next_confidence': np.nan, 'next_response_time': np.nan
            })
            
    return pd.DataFrame(results)

def main():
    print("="*50)
    print("🎓 卒業研究 学習ログ一括分析スクリプト")
    print("="*50)
    
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        
    raw_logs = fetch_all_logs()
    raw_users = fetch_all_users()
    raw_reminders = fetch_all_reminders()
    
    if not raw_logs:
        print("⚠ ログデータが存在しません。処理を終了します。")
        return
        
    df = preprocess_logs(raw_logs)
    df.to_csv(os.path.join(OUTPUT_DIR, '01_raw_logs_processed.csv'), index=False, encoding='utf-8-sig')
    
    df_transitions = analyze_individual_transitions(df)
    df_transitions.to_csv(os.path.join(OUTPUT_DIR, '02_reanswer_transitions.csv'), index=False, encoding='utf-8-sig')
    
    df_bin, df_thresh, df_version = analyze_retention_accuracy(df_transitions)
    if not df_bin.empty:
        df_bin.to_csv(os.path.join(OUTPUT_DIR, '03_retention_bins_analysis.csv'), index=False, encoding='utf-8-sig')
        df_thresh.to_csv(os.path.join(OUTPUT_DIR, '04_retention_threshold_analysis.csv'), index=False, encoding='utf-8-sig')
        df_version.to_csv(os.path.join(OUTPUT_DIR, '05_retention_model_versions.csv'), index=False, encoding='utf-8-sig')
        
    df_questions = analyze_global_questions(df)
    df_questions.to_csv(os.path.join(OUTPUT_DIR, '06_global_question_stats.csv'), index=False, encoding='utf-8-sig')
    
    df_initial, df_merged, df_inc = analyze_initial_and_free_learning(df, raw_users)
    
    if not df_initial.empty:
        df_initial.to_csv(os.path.join(OUTPUT_DIR, '07_initial_check_runs.csv'), index=False, encoding='utf-8-sig')
    if not df_merged.empty:
        df_merged.to_csv(os.path.join(OUTPUT_DIR, '08_initial_vs_free_learning.csv'), index=False, encoding='utf-8-sig')
    if not df_inc.empty:
        df_inc.to_csv(os.path.join(OUTPUT_DIR, '09_incomplete_initial_check_runs.csv'), index=False, encoding='utf-8-sig')

    df_reminders = analyze_reminder_impact(df, raw_reminders)
    if not df_reminders.empty:
        df_reminders.to_csv(os.path.join(OUTPUT_DIR, '10_reminder_impact_analysis.csv'), index=False, encoding='utf-8-sig')
    
    print("="*50)
    print(f"🎉 すべての分析が完了しました！")
    print(f"📂 出力先ディレクトリ: ./{OUTPUT_DIR}/")
    print("卒論のグラフ・表作成に上記のCSVファイルを活用してください。")
    print("="*50)

if __name__ == "__main__":
    main()