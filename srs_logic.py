# -*- coding: utf-8 -*-
import math
from datetime import datetime, timezone, timedelta

def get_jst_now():
    """常に日本時間(JST)の現在時刻を返す関数"""
    JST = timezone(timedelta(hours=+9), 'JST')
    return datetime.now(JST).replace(tzinfo=None)

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