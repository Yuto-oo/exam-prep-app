# -*- coding: utf-8 -*-
import os
import boto3
import math
import uuid
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from boto3.dynamodb.conditions import Key

def get_jst_now():
    """常に日本時間(JST)の現在時刻を返す関数"""
    JST = timezone(timedelta(hours=+9), 'JST')
    return datetime.now(JST).replace(tzinfo=None)

# 💡 AWS Lambda環境では .env が存在しないため、エラーを回避する処理を追加
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def get_boto3_session():
    return boto3.Session(
        region_name=os.getenv('AWS_REGION'),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
    )

# ==========================================
# srs_logic.py から統合（忘却曲線の計算ロジック）
# ==========================================
def calculate_retention_and_days(last_timestamp_str, streak, last_correct, last_confidence, target_rate=40.0):
    if not last_timestamp_str: return 0.0, 0.0
    try:
        last_time = datetime.strptime(last_timestamp_str, '%Y-%m-%d %H:%M:%S')
        now = get_jst_now()
        delta = now - last_time
        t_actual = max(0.0, delta.total_seconds() / 86400.0)
        
        base_h = 1.0
        multiplier = 2.0
        
        if last_correct:
            if last_confidence == "自信あり":
                base_h = 4.4
                multiplier = 1.8
            elif last_confidence == "少し自信あり":
                base_h = 3.3
                multiplier = 1.5
            else:
                base_h = 0.45
                multiplier = 1.1
        else:
            if last_confidence == "自信あり":
                base_h = 0.14
                multiplier = 1.0
            elif last_confidence == "少し自信あり":
                base_h = 0.27
                multiplier = 1.0
            else:
                base_h = 0.5
                multiplier = 1.0
        
        H = base_h * (multiplier ** max(0, streak))
        retention = math.exp(-t_actual / H)
        retention_pct = round(retention * 100, 1)
        
        target_r = target_rate / 100.0
        t_target = -H * math.log(target_r)
        days_until_review = round(t_target - t_actual, 1)
        
        return retention_pct, days_until_review
    except Exception:
        return 0.0, 0.0

def evaluate_history_retention(history):
    for q_key, data in history.items():
        last_correct = data.get('last_correct', False)
        last_confidence = data.get('last_confidence', '少し自信あり')
        retention, days_until = calculate_retention_and_days(data['last_timestamp'], data['streak'], last_correct, last_confidence)
        history[q_key]['retention'] = retention
        history[q_key]['days_until_review'] = days_until
        history[q_key]['needs_review'] = retention <= 40.0
    return history

# ==========================================
# aws_db.py から統合（一括保存ロジック）
# ==========================================
def save_reminder_log(user_id, sent_at, remind_targets):
    try:
        session = get_boto3_session()
        dynamodb = session.resource('dynamodb')
        table = dynamodb.Table('Exam_Learning_Reminder_Logs')
        
        formatted_targets = []
        for t in remind_targets:
            formatted_targets.append({
                'exam_code': str(t['exam_code']),
                'year': str(t['year']),
                'question_id': Decimal(str(t['question_id'])),
                'reminder_reason': str(t['reminder_reason']),
                'predicted_retention_at_send': Decimal(str(t['predicted_retention_at_send'])),
                'last_answer_at': str(t['last_answer_at']),
                'last_is_correct': bool(t['last_is_correct']),
                'last_confidence': str(t['last_confidence']),
                'user_solve_count': Decimal(str(t['user_solve_count'])),
                'days_since_last_answer': Decimal(str(t['days_since_last_answer']))
            })
            
        item = {
            'log_id': str(uuid.uuid4()), 
            'user_id': str(user_id),
            'sent_at': str(sent_at),
            'total_reminded': Decimal(str(len(formatted_targets))),
            'targets': formatted_targets
        }
        table.put_item(Item=item)
    except Exception as e:
        print(f"🚨 リマインドログの保存に失敗しました (user: {user_id}): {e}")

# ==========================================
# メイン処理 (メール送信等のロジック)
# ==========================================
def get_all_users_to_notify():
    session = get_boto3_session()
    dynamodb = session.resource('dynamodb')
    table = dynamodb.Table('Exam_Learning_Users')
    
    try:
        response = table.scan()
        users = response.get('Items', [])
        return [u for u in users if u.get('receive_notifications') and u.get('email')]
    except Exception as e:
        print(f"🚨 ユーザー取得エラー: {e}")
        return []

def get_user_history_raw(username):
    session = get_boto3_session()
    dynamodb = session.resource('dynamodb')
    table = dynamodb.Table('Exam_Learning_Logs')
    
    history = {}
    response = table.query(KeyConditionExpression=Key('user_id').eq(username))
    items = response.get('Items', [])
    while 'LastEvaluatedKey' in response:
        response = table.query(KeyConditionExpression=Key('user_id').eq(username), ExclusiveStartKey=response['LastEvaluatedKey'])
        items.extend(response.get('Items', []))
        
    items.sort(key=lambda x: x.get('timestamp', ''))
    
    for item in items:
        q_key = f"{item.get('exam_code')}_{item.get('year')}_{item.get('question_id')}"
        is_correct = bool(item.get('is_correct', False))
        confidence = str(item.get('answer_confidence', '少し自信あり'))
        ts_str = str(item.get('timestamp', ''))
        
        if q_key not in history:
            history[q_key] = {
                'last_correct': is_correct,
                'last_confidence': confidence,
                'last_timestamp': ts_str,
                'streak': 1 if is_correct else 0,
                'solve_count': 1
            }
        else:
            history[q_key]['last_correct'] = is_correct
            history[q_key]['last_confidence'] = confidence
            history[q_key]['last_timestamp'] = ts_str
            history[q_key]['streak'] = history[q_key]['streak'] + 1 if is_correct else 0
            history[q_key]['solve_count'] = history[q_key].get('solve_count', 0) + 1
            
    return evaluate_history_retention(history)

def send_email(ses_client, to_email, subject, body_text):
    SENDER = "資格学習アプリ (送信専用) <exam.app.noreply@gmail.com>"
    
    try:
        response = ses_client.send_email(
            Source=SENDER,
            Destination={'ToAddresses': [to_email]},
            Message={
                'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                'Body': {'Text': {'Data': body_text, 'Charset': 'UTF-8'}}
            }
        )
        return True
    except Exception as e:
        print(f"🚨 {to_email} への送信に失敗: {e}")
        return False

def main():
    print("=== 🚀 リマインドメール送信バッチを開始します ===")
    session = get_boto3_session()
    ses_client = session.client('ses')
    
    users = get_all_users_to_notify()
    if not users:
        print("ℹ️ 通知対象のユーザーがいませんでした。処理を終了します。")
        return
        
    for user in users:
        user_id = user['user_id']
        email = user['email']
        
        history = get_user_history_raw(user_id)
        
        review_count = 0
        trick_count = 0
        remind_targets = []
        
        for q_key, data in history.items():
            retention = data.get('retention', 100.0)
            last_correct = data.get('last_correct', False)
            last_confidence = data.get('last_confidence', '')
            
            is_forgetting = retention <= 40.0
            is_trick = (not last_correct) and (last_confidence == "自信あり")
            
            if is_forgetting or is_trick:
                review_count += 1
                if is_trick:
                    trick_count += 1
                
                reason_parts = []
                if is_forgetting: reason_parts.append("推定記憶保持率低下(40%以下)")
                if is_trick: reason_parts.append("要復習対象(高確信誤答)")
                
                last_time_str = data.get('last_timestamp', '')
                days_since = 0.0
                if last_time_str:
                    try:
                        lt = datetime.strptime(last_time_str, '%Y-%m-%d %H:%M:%S')
                        days_since = round((get_jst_now() - lt).total_seconds() / 86400.0, 2)
                    except:
                        pass
                
                parts = q_key.split('_')
                if len(parts) >= 3:
                    remind_targets.append({
                        'exam_code': parts[0],
                        'year': parts[1],
                        'question_id': parts[2],
                        'reminder_reason': " / ".join(reason_parts),
                        'predicted_retention_at_send': round(retention, 1),
                        'last_answer_at': last_time_str,
                        'last_is_correct': last_correct,
                        'last_confidence': last_confidence,
                        'user_solve_count': data.get('solve_count', 1),
                        'days_since_last_answer': days_since
                    })
                    
        if review_count > 0:
            subject = "【資格学習アプリ】本日の復習リマインド"
            body = f"""{user_id} さん\n\n現在、あなたの学習データと忘却曲線に基づき、「復習が強く推奨される問題」が【 {review_count} 問 】あります。\n\n"""
            if trick_count > 0:
                body += f"⚠️ 特に、過去に「自信ありと答えて間違えた問題（思い込みの可能性）」が {trick_count} 問含まれています。\n\n"
            body += """記憶が完全に消えてしまう前に、アプリを開いて「要復習」フィルターから再挑戦しましょう！\n\n▼ 学習アプリはこちら\nhttps://(ここに後でStreamlitのURLを記載します)\n\n--------------------------------------------------\n※本メールは送信専用アドレスから自動配信されています。\n※試験に合格したなど、今後の通知が不要な場合は、アプリのメニュー画面より「通知設定」をオフにしてください。\n--------------------------------------------------\n"""
            
            print(f"📧 {user_id} ({email}) にメールを送信します... (復習対象: {review_count}問)")
            if send_email(ses_client, email, subject, body):
                print("   -> ✅ 送信成功！")
                sent_at = get_jst_now().strftime('%Y-%m-%d %H:%M:%S')
                save_reminder_log(user_id, sent_at, remind_targets)
        else:
            print(f"👍 {user_id} ({email}) は記憶が定着しており、復習対象の問題はありません。")
            
    print("=== 🎉 すべての送信処理が完了しました ===")


# 💡 AWS Lambda 用のハンドラー関数
def lambda_handler(event, context):
    """AWS Lambdaから定期実行される際の入り口"""
    print("=== ☁️ AWS Lambda Execution Started ===")
    main()
    return {
        'statusCode': 200,
        'body': 'Reminder process completed successfully.'
    }

if __name__ == "__main__":
    main()