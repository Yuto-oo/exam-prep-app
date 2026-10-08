# -*- coding: utf-8 -*-
import os
import boto3
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

# srs_logicから忘却曲線アルゴリズムを拝借
from srs_logic import evaluate_history_retention
from aws_db import save_reminder_log

def get_boto3_session():
    return boto3.Session(
        region_name=os.getenv('AWS_REGION'),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
    )

def get_all_users_to_notify():
    """通知設定がONで、メアドが登録されているユーザーを取得"""
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
    """Streamlitに依存せず純粋なPythonとして学習履歴を取得して忘却曲線を計算"""
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
    """SESを使用してメールを送信"""
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
                
                # 💡 修正点：1問ずつ保存するループをなくし、関数にリストごと渡す
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