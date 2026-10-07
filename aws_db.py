# -*- coding: utf-8 -*-
import os
import time
import boto3
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from boto3.dynamodb.conditions import Key, Attr
import streamlit as st

def get_jst_now():
    """常に日本時間(JST)の現在時刻を返す関数"""
    JST = timezone(timedelta(hours=+9), 'JST')
    return datetime.now(JST).replace(tzinfo=None)

from srs_logic import evaluate_history_retention

def get_dynamodb_resource():
    return boto3.resource(
        'dynamodb',
        region_name=os.getenv('AWS_REGION'),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
    )

def load_user_history_from_aws(username):
    history = {}
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Logs')
        
        response = table.query(KeyConditionExpression=Key('user_id').eq(username))
        items = response.get('Items', [])
        while 'LastEvaluatedKey' in response:
            response = table.query(KeyConditionExpression=Key('user_id').eq(username), ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))
            
        items.sort(key=lambda x: x.get('timestamp', ''))
        
        for item in items:
            activity_type = item.get('activity_type', 'free_learning')
            time_taken = float(item.get('time_taken', 0.0))
            
            valid_for_time_analysis = item.get('valid_for_time_analysis')
            if valid_for_time_analysis is None:
                is_time_valid = (time_taken < 900.0)
            else:
                is_time_valid = bool(valid_for_time_analysis)

            exam_code = str(item.get('exam_code', 'FE'))
            year = str(item.get('year', '不明'))
            q_id = int(item.get('question_id', 0))
            q_key = f"{exam_code}_{year}_{q_id}"
            
            is_correct = bool(item.get('is_correct', False))
            ts_str = str(item.get('timestamp', ''))
            confidence = str(item.get('answer_confidence', '少し自信あり'))
            
            if q_key not in history:
                history[q_key] = {
                    'solve_count': 0,
                    'first_time': time_taken if is_time_valid else 0.0,
                    'times': [],
                    'last_correct': is_correct,
                    'last_confidence': confidence,
                    'last_timestamp': ts_str,
                    'streak': 1 if is_correct else 0
                }
            else:
                history[q_key]['last_timestamp'] = ts_str
                history[q_key]['last_correct'] = is_correct
                history[q_key]['last_confidence'] = confidence
                history[q_key]['streak'] = history[q_key]['streak'] + 1 if is_correct else 0
            
            history[q_key]['solve_count'] += 1
            if is_time_valid:
                history[q_key]['times'].append(time_taken)
            
        history = evaluate_history_retention(history)
        return history
        
    except Exception as e:
        st.error(f"🚨 AWSからの履歴読み込みに失敗しました: {e}")
        return {}

def load_global_statistics_from_aws(exam_code):
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Logs')
        
        response = table.scan(FilterExpression=Attr('exam_code').eq(exam_code))
        items = response.get('Items', [])
        while 'LastEvaluatedKey' in response:
            response = table.scan(FilterExpression=Attr('exam_code').eq(exam_code), ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))
            
        if not items:
            return {"levels": {}, "questions": {}, "total_logs": 0}
            
        levels_data = {}
        questions_data = {}
        
        for item in items:
            activity_type = item.get('activity_type', 'free_learning')
            time_taken = float(item.get('time_taken', 0.0))
            
            valid_for_time_analysis = item.get('valid_for_time_analysis')
            if valid_for_time_analysis is None:
                is_time_valid = (time_taken < 900.0)
            else:
                is_time_valid = bool(valid_for_time_analysis)

            cat = str(item.get('category_large', '未分類'))
            is_correct = 1 if bool(item.get('is_correct', False)) else 0
            raw_level = str(item.get('user_level', ''))
            confidence = str(item.get('answer_confidence', '少し自信あり'))
            
            year = str(item.get('year', '不明'))
            q_id = int(item.get('question_id', 0))
            q_key = f"{exam_code}_{year}_{q_id}"
            
            if "初学者" in raw_level: lvl_group = "初学者"
            elif "中級者" in raw_level: lvl_group = "中級者"
            elif "上級者" in raw_level: lvl_group = "上級者"
            else: lvl_group = "その他"
                
            for target_group in ["全体", lvl_group]:
                if target_group not in levels_data: levels_data[target_group] = {}
                if cat not in levels_data[target_group]: 
                    levels_data[target_group][cat] = {'correct_sum': 0, 'time_sum': 0.0, 'count': 0, 'time_count': 0}
                
                levels_data[target_group][cat]['correct_sum'] += is_correct
                levels_data[target_group][cat]['count'] += 1
                
                if is_time_valid:
                    levels_data[target_group][cat]['time_sum'] += time_taken
                    levels_data[target_group][cat]['time_count'] += 1

            if q_key not in questions_data:
                questions_data[q_key] = {
                    'total_count': 0, 'correct_count': 0, 'total_time': 0.0, 'time_count': 0, 'trick_count': 0,
                    'level_correct': {"初学者": 0, "中級者": 0, "上級者": 0, "その他": 0},
                    'level_count': {"初学者": 0, "中級者": 0, "上級者": 0, "その他": 0}
                }
            
            q_stats = questions_data[q_key]
            q_stats['total_count'] += 1
            q_stats['correct_count'] += is_correct
            
            if is_time_valid:
                q_stats['total_time'] += time_taken
                q_stats['time_count'] += 1
            
            if "自信あり" in confidence and is_correct == 0:
                q_stats['trick_count'] += 1
                
            q_stats['level_count'][lvl_group] += 1
            q_stats['level_correct'][lvl_group] += is_correct
                
        final_levels = {}
        for lvl, categories in levels_data.items():
            final_levels[lvl] = {}
            for cat, stats in categories.items():
                avg_time = (stats['time_sum'] / stats['time_count']) if stats['time_count'] > 0 else 0.0
                final_levels[lvl][cat] = {
                    'avg_correct_rate': (stats['correct_sum'] / stats['count']) * 100,
                    'avg_time': avg_time,
                    'count': stats['count']
                }
                
        final_questions = {}
        for q_k, stats in questions_data.items():
            tot = stats['total_count']
            t_cnt = stats['time_count']
            lvl_rates = {}
            for lg in ["初学者", "中級者", "上級者"]:
                l_cnt = stats['level_count'][lg]
                lvl_rates[lg] = (stats['level_correct'][lg] / l_cnt * 100) if l_cnt > 0 else 0.0

            final_questions[q_k] = {
                'global_correct_rate': (stats['correct_count'] / tot) * 100 if tot > 0 else 0,
                'global_avg_time': stats['total_time'] / t_cnt if t_cnt > 0 else 0.0,
                'trick_count': stats['trick_count'],
                'total_count': tot,
                'level_rates': lvl_rates
            }
                
        return {"levels": final_levels, "questions": final_questions, "total_logs": len(items)}
    except Exception as e:
        st.warning(f"⚠️ 統計データの取得に失敗しました: {e}")
        return {"levels": {}, "questions": {}, "total_logs": 0}

def send_result_to_aws(q_data, selected_label, selected_text, is_correct, time_taken, confidence, metrics,
                       activity_type="free_learning", test_run_id="", test_set_id="", test_attempt_no=1,
                       raw_response_time=0.0, valid_for_time_analysis=True, time_exclusion_reason="",
                       predicted_retention_before_answer=None, retention_model_version="v1"):
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Logs')
        
        if raw_response_time == 0.0:
            raw_response_time = time_taken
            
        item = {
            'user_id': str(st.session_state.user_name),
            'timestamp': get_jst_now().strftime('%Y-%m-%d %H:%M:%S'),
            'exam_code': str(st.session_state.exam_code), 
            'year': str(q_data.get('year', '不明')),
            'question_id': Decimal(str(q_data.get('id', 0))),
            'question_text': str(q_data.get('question', '')),
            'selected_answer': str(selected_label),
            'selected_text': str(selected_text),
            'is_correct': bool(is_correct),
            'time_taken': Decimal(str(time_taken)),
            'category_large': str(q_data.get('category_large', '未分類')),
            'question_type': str(q_data.get('question_type', '知識問題')), 
            'user_level': str(st.session_state.user_level),
            'answer_confidence': str(confidence),
            'user_solve_count': Decimal(str(metrics['solve_count'])),
            'first_answer_time_sec': Decimal(str(metrics['first_time'])),
            'user_average_answer_time_sec': Decimal(str(metrics['avg_time'])),
            
            'activity_type': str(activity_type),
            'test_run_id': str(test_run_id),
            'test_set_id': str(test_set_id),
            'test_attempt_no': Decimal(str(test_attempt_no)),
            'raw_response_time': Decimal(str(raw_response_time)),
            'valid_for_time_analysis': bool(valid_for_time_analysis),
            'time_exclusion_reason': str(time_exclusion_reason),
            'retention_model_version': str(retention_model_version)
        }
        
        if predicted_retention_before_answer is not None:
            item['predicted_retention_before_answer'] = Decimal(str(predicted_retention_before_answer))
            
        table.put_item(Item=item)
    except Exception as e:
        st.error(f"🚨 AWS送信エラー: {e}")

# --- セッション中断・再開用 ---
def save_suspend_state_to_aws(username, exam_code, quiz_questions, current_index, activity_type="free_learning", test_run_id="", test_set_id="", test_attempt_no=1):
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Sessions')
        q_keys = [{"year": str(q.get("year", "")), "id": int(q.get("id", 0))} for q in quiz_questions]
        item = {
            'user_id': str(username),
            'updated_at': get_jst_now().strftime('%Y-%m-%d %H:%M:%S'),
            'exam_code': str(exam_code),
            'q_keys': q_keys,
            'current_index': int(current_index),
            'is_suspended': True,
            'activity_type': str(activity_type),
            'test_run_id': str(test_run_id),
            'test_set_id': str(test_set_id),
            'test_attempt_no': int(test_attempt_no)
        }
        table.put_item(Item=item)
    except Exception as e:
        pass

def load_suspend_state_from_aws(username):
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Sessions')
        response = table.get_item(Key={'user_id': str(username)})
        item = response.get('Item')
        if item and item.get('is_suspended'):
            return item
    except Exception:
        pass
    return None

def clear_suspend_state_in_aws(username):
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Sessions')
        table.delete_item(Key={'user_id': str(username)})
    except Exception:
        pass

# --- ブックマーク機能用 ---
def load_bookmarks_from_aws(username):
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Bookmarks')
        response = table.query(KeyConditionExpression=Key('user_id').eq(username))
        items = response.get('Items', [])
        while 'LastEvaluatedKey' in response:
            response = table.query(KeyConditionExpression=Key('user_id').eq(username), ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))
        return set([item['q_key'] for item in items])
    except Exception:
        return set()

def add_bookmark_to_aws(username, q_key):
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Bookmarks')
        table.put_item(Item={
            'user_id': str(username),
            'q_key': str(q_key),
            'timestamp': get_jst_now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except Exception:
        pass

def remove_bookmark_from_aws(username, q_key):
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Bookmarks')
        table.delete_item(Key={'user_id': str(username), 'q_key': str(q_key)})
    except Exception:
        pass

def load_global_bookmark_counts():
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Bookmarks')
        response = table.scan(ProjectionExpression="q_key")
        items = response.get('Items', [])
        while 'LastEvaluatedKey' in response:
            response = table.scan(ProjectionExpression="q_key", ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))
            
        bookmark_counts = {}
        for item in items:
            q_key = item.get('q_key')
            if q_key:
                bookmark_counts[q_key] = bookmark_counts.get(q_key, 0) + 1
        return bookmark_counts
    except Exception:
        return {}

def load_user_profile(username):
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Users')
        response = table.get_item(Key={'user_id': str(username)})
        return response.get('Item', {})
    except Exception:
        return {}

def save_user_profile(username, email, receive_notifications):
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Users')
        table.update_item(
            Key={'user_id': str(username)},
            UpdateExpression="SET email = :e, receive_notifications = :n, updated_at = :u",
            ExpressionAttributeValues={
                ':e': str(email) if email else "",
                ':n': bool(receive_notifications),
                ':u': get_jst_now().strftime('%Y-%m-%d %H:%M:%S')
            }
        )
    except Exception:
        pass

def save_initial_check_completion(username, exam_code, result_dict):
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Users')
        
        profile = load_user_profile(username)
        initial_checks = profile.get("initial_checks", {})
        initial_checks[exam_code] = result_dict
        
        table.update_item(
            Key={'user_id': str(username)},
            UpdateExpression="SET initial_checks = :ic, updated_at = :u",
            ExpressionAttributeValues={
                ':ic': initial_checks,
                ':u': get_jst_now().strftime('%Y-%m-%d %H:%M:%S')
            }
        )
    except Exception as e:
        st.error(f"完了状態の保存エラー: {e}")

def save_reminder_log(log_data):
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table('Exam_Learning_Reminder_Logs')
        
        item = {
            'log_id': str(uuid.uuid4()), 
            'user_id': str(log_data['user_id']),
            'sent_at': str(log_data['sent_at']),
            'exam_code': str(log_data['exam_code']),
            'year': str(log_data['year']),
            'question_id': Decimal(str(log_data['question_id'])),
            'reminder_reason': str(log_data['reminder_reason']),
            'predicted_retention_at_send': Decimal(str(log_data['predicted_retention_at_send'])),
            'last_answer_at': str(log_data['last_answer_at']),
            'last_is_correct': bool(log_data['last_is_correct']),
            'last_confidence': str(log_data['last_confidence']),
            'user_solve_count': Decimal(str(log_data['user_solve_count'])),
            'days_since_last_answer': Decimal(str(log_data['days_since_last_answer']))
        }
        table.put_item(Item=item)
    except Exception as e:
        print(f"🚨 リマインドログの保存に失敗しました (user: {log_data.get('user_id')}): {e}")