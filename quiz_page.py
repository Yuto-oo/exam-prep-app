# -*- coding: utf-8 -*-
import os
import re
import time
from datetime import datetime
from aws_db import (
    load_global_statistics_from_aws, 
    send_result_to_aws, 
    save_suspend_state_to_aws,
    add_bookmark_to_aws,
    remove_bookmark_from_aws
)
import pandas as pd
import plotly.express as px
from srs_logic import evaluate_history_retention, calculate_retention_and_days
import streamlit as st


def convert_markdown_table_to_html(lines):
  if len(lines) < 2:
    return "\n".join(lines)
  html = [
      '<table style="width:100%; border-collapse: collapse; margin-top: 10px;'
      ' margin-bottom: 10px; border: 1px solid #555;">'
  ]
  headers = [c.strip() for c in lines[0].split("|")[1:-1]]
  html.append(
      '  <thead>\n    <tr style="background-color: rgba(128,128,128,0.15);">'
  )
  for h in headers:
    html.append(
        '      <th style="padding: 10px; text-align: left; border: 1px solid'
        f' #555; font-weight: bold;">{h}</th>'
    )
  html.append("    </tr>\n  </thead><tbody>")
  for line in lines[2:]:
    cells = [c.strip() for c in line.split("|")[1:-1]]
    html.append("    <tr>")
    for c in cells:
      html.append(f'      <td style="padding: 10px; border: 1px solid #555;">{c}</td>')
    html.append("    </tr>")
  html.append("  </tbody></table>")
  return "\n".join(html)


def format_markdown(text):
  if not text:
    return ""
  text = text.replace("\\n", "\n")
  lines = text.split("\n")
  formatted_lines = []
  in_table = False
  table_lines = []
  for line in lines:
    striped = line.strip()
    if striped.startswith("|") and striped.endswith("|"):
      if not in_table:
        in_table = True
      table_lines.append(line)
    else:
      if in_table:
        formatted_lines.append(convert_markdown_table_to_html(table_lines))
        table_lines = []
        in_table = False
      formatted_lines.append(line + "  ")
  if in_table:
    formatted_lines.append(convert_markdown_table_to_html(table_lines))
  return "\n".join(formatted_lines)


def show_quiz_page(q_data):
  st.subheader(
      f"📝 {st.session_state.exam_name}"
  )
  
  q_key = (
      f"{st.session_state.exam_code}_{q_data.get('year', '不明')}_{q_data.get('id', 0)}"
  )
  is_bookmarked = q_key in st.session_state.bookmarks

  c1, c2 = st.columns([3, 1])
  with c1:
      st.caption(
          f"🗂️ カテゴリ: {q_data.get('category_large', '未分類')} | 🏛️ 出題タイプ:"
          f" {q_data.get('question_type', '知識問題')}"
      )
  with c2:
      bm_label = "🔖 ブックマーク済" if is_bookmarked else "🔖 ブックマークする"
      if st.button(bm_label, use_container_width=True, key=f"btn_bm_{q_key}"):
          if is_bookmarked:
              st.session_state.bookmarks.discard(q_key)
              remove_bookmark_from_aws(st.session_state.user_name, q_key)
          else:
              st.session_state.bookmarks.add(q_key)
              add_bookmark_to_aws(st.session_state.user_name, q_key)
          st.rerun()

  if 'start_time' not in st.session_state or st.session_state.start_time is None:
    st.session_state.start_time = time.time()

  st.markdown(
      format_markdown(q_data.get('question', '')), unsafe_allow_html=True
  )

  image_paths = []
  raw_list = []

  def natural_sort_key(s):
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split(r'(\d+)', str(s))
    ]

  image_keys = [k for k in q_data.keys() if 'image' in str(k).lower()]
  image_keys.sort(key=natural_sort_key)

  for key in image_keys:
    val = q_data.get(key)
    if val:
      if isinstance(val, list):
        raw_list.extend(val)
      elif isinstance(val, str) and val.strip():
        raw_list.extend([x.strip() for x in val.split(',') if x.strip()])

  def resolve_path(p_str):
    p_str = str(p_str).strip()
    if not p_str:
      return None
    if os.path.exists(p_str):
      return p_str
    p = (
        p_str
        if p_str.startswith('images/')
        else os.path.join('images', p_str)
    )
    return p if os.path.exists(p) else None

  for item in raw_list:
    resolved = resolve_path(item)
    if resolved and resolved not in image_paths:
      image_paths.append(resolved)

      base_no_ext, ext = os.path.splitext(resolved)
      for sep in ['_', '-']:
        if base_no_ext.endswith(f'{sep}1'):
          prefix_base = base_no_ext[:-2]
          for num in range(2, 10):
            next_path = f'{prefix_base}{sep}{num}{ext}'
            if os.path.exists(next_path) and next_path not in image_paths:
              image_paths.append(next_path)
            else:
              break

  if not image_paths:
    exam_code = st.session_state.get('exam_code', '')
    year = q_data.get('year', '不明')
    q_id = q_data.get('id', 0)

    possible_base_names = [
        f'{exam_code}_{year}_{q_id}',
        f'{year}_{q_id}',
        f'{exam_code}_{q_id}',
        f'{q_id}',
    ]
    extensions = ['.png', '.jpg', '.jpeg', '.PNG', '.JPG']

    for base in possible_base_names:
      if not base or base.startswith('_'):
        continue

      suffix_patterns = [
          [f'_{i}' for i in range(1, 10)],
          [f'-{i}' for i in range(1, 10)],
          [f'_{i:02d}' for i in range(1, 10)],
          [f'_{chr(96+i)}' for i in range(1, 10)],
      ]

      for pattern in suffix_patterns:
        found_in_pattern = []
        for suf in pattern:
          found_suf = False
          for ext in extensions:
            test_path = os.path.join('images', f'{base}{suf}{ext}')
            if os.path.exists(test_path):
              found_in_pattern.append(test_path)
              found_suf = True
              break
          if not found_suf:
            break
        if found_in_pattern:
          image_paths.extend(found_in_pattern)
          break

      if image_paths:
        break

      for ext in extensions:
        test_path = os.path.join('images', f'{base}{ext}')
        if os.path.exists(test_path):
          image_paths.append(test_path)
          break

      if image_paths:
        break

  unique_image_paths = list(dict.fromkeys(image_paths))

  for img_path in unique_image_paths:
    st.image(img_path, use_container_width=True)

  st.write('')

  confidence = st.radio(
      '🧠 この問題への現在の解答自信度を選択してください：',
      ['自信あり', '少し自信あり', '自信なし（勘）'],
      index=None,
      horizontal=True,
      disabled=st.session_state.answered,
      key=f"conf_{q_key}_{st.session_state.current_index}"
  )

  options = q_data.get('options', {})
  extended_sequence = [
      'ア',
      'イ',
      'ウ',
      'エ',
      'オ',
      'カ',
      'キ',
      'ク',
      'A',
      'B',
      'C',
      'D',
      'E',
      'F',
      '1',
      '2',
      '3',
      '4',
      '5',
  ]
  labels = [l for l in extended_sequence if l in options]
  for k in options.keys():
    if k not in labels:
      labels.append(k)

  q_type = q_data.get('question_type', '知識問題')
  MAX_COLS = (
      2 if q_type in ['アルゴリズム問題', '計算問題', '図表計算問題'] else 4
  )

  st.write("")
  for label in labels:
      st.markdown(
          f"**（{label}）** {format_markdown(options.get(label, ''))}",
          unsafe_allow_html=True,
      )

  st.write('')

  for i in range(0, len(labels), MAX_COLS):
    chunk = labels[i : i + MAX_COLS]
    button_cols = st.columns(len(chunk))
    for j, label in enumerate(chunk):
      with button_cols[j]:
        if st.button(
            f'{label} を選択',
            key=f'b_{label}',
            disabled=st.session_state.answered,
            use_container_width=True,
        ):
          if confidence is None:
              st.warning("回答する前に自信度を選択してください。")
              st.stop()

          elapsed = round(time.time() - st.session_state.start_time, 2)
          is_correct = label == q_data.get('answer')
          st.session_state.current_time_taken = elapsed
          st.session_state.answered = True
          
          st.session_state.is_over_time = elapsed >= 900.0
          valid_for_time_analysis = not st.session_state.is_over_time
          time_exclusion_reason = "over_900sec" if st.session_state.is_over_time else ""

          predicted_retention_before_answer = None
          if q_key in st.session_state.history:
              prev = st.session_state.history[q_key]
              predicted_retention_before_answer, _ = calculate_retention_and_days(
                  prev.get('last_timestamp'),
                  prev.get('streak', 0),
                  prev.get('last_correct', False),
                  prev.get('last_confidence', '少し自信あり')
              )

          if is_correct:
              st.session_state.score += 1
          now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

          if q_key not in st.session_state.history:
              st.session_state.history[q_key] = {
                  'solve_count': 0,
                  'first_time': elapsed if valid_for_time_analysis else 0.0,
                  'times': [],
                  'last_correct': is_correct,
                  'last_confidence': confidence,
                  'streak': 1 if is_correct else 0,
                  'last_timestamp': now_str,
              }
          else:
              st.session_state.history[q_key]['last_correct'] = is_correct
              st.session_state.history[q_key]['last_confidence'] = confidence
              st.session_state.history[q_key]['last_timestamp'] = now_str
              st.session_state.history[q_key]['streak'] = (
                  st.session_state.history[q_key]['streak'] + 1
                  if is_correct
                  else 0
              )

          st.session_state.history[q_key]['solve_count'] += 1
          
          if valid_for_time_analysis:
              st.session_state.history[q_key]['times'].append(elapsed)

          st.session_state.history = evaluate_history_retention(
              st.session_state.history
          )

          times_list = st.session_state.history[q_key]['times']
          avg_time = sum(times_list) / len(times_list) if times_list else 0.0

          metrics = {
              'solve_count': st.session_state.history[q_key]['solve_count'],
              'first_time': round(
                  st.session_state.history[q_key]['first_time'], 2
              ),
              'avg_time': round(avg_time, 2),
          }
          
          send_result_to_aws(
              q_data,
              label,
              options.get(label, ''),
              is_correct,
              elapsed,
              confidence,
              metrics,
              activity_type=st.session_state.get('activity_type', 'free_learning'),
              test_run_id=st.session_state.get('test_run_id', ''),
              test_set_id=st.session_state.get('test_set_id', ''),
              test_attempt_no=st.session_state.get('test_attempt_no', 1),
              raw_response_time=elapsed,
              valid_for_time_analysis=valid_for_time_analysis,
              time_exclusion_reason=time_exclusion_reason,
              predicted_retention_before_answer=predicted_retention_before_answer,
              retention_model_version="v1"
          )

          is_initial = st.session_state.get('activity_type') == 'initial_check'
          st.session_state.feedback = (
              '✅ **回答を記録しました。** '
              if is_initial
              else ('✅ **正解！** ' if is_correct else f"❌ **不正解...** 正解は **{q_data.get('answer')}**")
          )
          st.rerun()

  source_text = q_data.get('Source') or q_data.get('source')
  if source_text:
    st.markdown(
        f"<div style='text-align: right; font-size: 0.82em; color: #777777; margin-top: 8px; margin-bottom: 5px;'>"
        f"{source_text}"
        f"</div>",
        unsafe_allow_html=True
    )

  if st.session_state.answered:
    st.write('---')
    
    is_initial = st.session_state.get('activity_type') == 'initial_check'
    
    if is_initial:
        st.success(st.session_state.feedback)
        st.info("※ 初回確認テスト中のため、詳細な解説や正誤は全問終了後に得点としてまとめて確認できます。下のボタンから次の問題へお進みください。")
        
        if getattr(st.session_state, 'is_over_time', False):
            st.warning("⚠️ この問題で15分以上経過したため時間分析からは除外されます（回答履歴は保存されました）。")
    else:
        if '✅' in st.session_state.feedback:
          st.success(st.session_state.feedback)
        else:
          st.error(st.session_state.feedback)

        explanation_text = (
            q_data.get('comment')
            or q_data.get('explanation')
            or q_data.get('commentary')
            or q_data.get('解説')
            or 'この問題の解説は現在準備中です。'
        )
        
        col_title, col_btn = st.columns([3, 1])
        with col_title:
            st.markdown('#### 📖 解説')
        with col_btn:
            if unique_image_paths:
                with st.popover("🖼️ 問題画像を確認", use_container_width=True):
                    for img_path in unique_image_paths:
                        st.image(img_path, use_container_width=True)

        st.markdown(
            format_markdown(explanation_text),
            unsafe_allow_html=True,
        )

        st.write('---')
        st.markdown('#### ⏱️ クラス統計とのリアルタイム比較ベンチマーク')

        with st.spinner('☁️ AWSからこの問題のクラス解答データを解析中...'):
          global_stats = load_global_statistics_from_aws(st.session_state.exam_code)

        g_questions = global_stats.get('questions', {})

        if q_key in g_questions:
          q_stat = g_questions[q_key]
          my_time = st.session_state.current_time_taken
          avg_time = q_stat['global_avg_time']

          c_m1, c_m2 = st.columns(2)
          with c_m1:
            st.metric('あなたの解答時間', f'{my_time:.2f} 秒')
          with c_m2:
            diff = avg_time - my_time
            delta_str = (
                f'全体平均より {abs(diff):.1f}秒 高速！🚀'
                if diff > 0
                else f'全体平均より {abs(diff):.1f}秒 じっくり🐢'
            )
            st.metric(
                'クラス全体の平均解答時間',
                f'{avg_time:.2f} 秒',
                delta=delta_str,
                delta_color='normal' if diff > 0 else 'inverse',
            )

          st.write('**👥 ピアグループ（ユーザーレベル）別の正解率**')
          lvl_rates = q_stat.get('level_rates', {})
          ldf = pd.DataFrame({
              'ユーザーレベル': ['初学者', '中級者', '上級者'],
              '正解率 (%)': [
                  lvl_rates.get('初学者', 0.0),
                  lvl_rates.get('中級者', 0.0),
                  lvl_rates.get('上級者', 0.0),
              ],
          })

          fig_bar = px.bar(
              ldf,
              x='正解率 (%)',
              y='ユーザーレベル',
              orientation='h',
              text='正解率 (%)',
              color='ユーザーレベル',
              color_discrete_map={
                  '初学者': '#90A4AE',
                  '中級者': '#4DB6AC',
                  '上級者': '#81C784',
              },
          )
          fig_bar.update_layout(
              xaxis_range=[0, 105],
              height=180,
              showlegend=False,
              margin=dict(t=5, b=5, l=5, r=5),
          )
          fig_bar.update_traces(
              texttemplate='%{text:.1f}%', textposition='inside'
          )
          st.plotly_chart(fig_bar, use_container_width=True)
        else:
          st.info(
              'ℹ️'
              ' あなたがクラスで初めてこの問題に解答しました！データが蓄積されると統計が表示されます。'
          )

        if getattr(st.session_state, 'is_over_time', False):
            st.warning("⚠️ この問題で15分以上経過したため時間分析からは除外されます（正誤と解答履歴は保存されました）。")

    st.write("---")
    
    hide_suspend_btn = st.session_state.get('activity_type') in ['initial_check', 'ability_test']
    
    if hide_suspend_btn:
        if st.button('次の問題へ進む ➡️', use_container_width=True, type='primary'):
            st.session_state.answered = False
            st.session_state.is_over_time = False 
            st.session_state.current_index += 1
            st.session_state.start_time = time.time()
            st.rerun()
    else:
        c_btn1, c_btn2 = st.columns(2)
        with c_btn1:
            if st.button("⏸️ 中断して設定に戻る", use_container_width=True):
                st.session_state.current_index += 1
                st.session_state.answered = False
                st.session_state.is_over_time = False 
                
                # 💡 中断時に、現在の活動状態（activity_type等）もセットで保存する
                save_suspend_state_to_aws(
                    st.session_state.user_name, 
                    st.session_state.exam_code, 
                    st.session_state.quiz_questions, 
                    st.session_state.current_index,
                    st.session_state.get('activity_type', 'free_learning'),
                    st.session_state.get('test_run_id', ''),
                    st.session_state.get('test_set_id', ''),
                    st.session_state.get('test_attempt_no', 1)
                )
                
                st.session_state.suspended = True
                st.session_state.saved_session = {
                    'exam_code': st.session_state.exam_code,
                    'q_keys': [{"year": str(q.get("year", "")), "id": int(q.get("id", 0))} for q in st.session_state.quiz_questions],
                    'current_index': st.session_state.current_index,
                    'activity_type': st.session_state.get('activity_type', 'free_learning'),
                    'test_run_id': st.session_state.get('test_run_id', ''),
                    'test_set_id': st.session_state.get('test_set_id', ''),
                    'test_attempt_no': st.session_state.get('test_attempt_no', 1)
                }
                st.session_state.config_done = False
                st.rerun()
                
        with c_btn2:
            if st.button('次の問題へ進む ➡', use_container_width=True, type='primary'):
                st.session_state.answered = False
                st.session_state.is_over_time = False 
                st.session_state.current_index += 1
                st.session_state.start_time = time.time()
                st.rerun()

  if not st.session_state.answered:
      hide_suspend_btn = st.session_state.get('activity_type') in ['initial_check', 'ability_test']
      if not hide_suspend_btn:
          st.write("---")
          if st.button("⏸️ クイズを中断して設定に戻る", use_container_width=True):
              
              # 💡 中断時に、現在の活動状態（activity_type等）もセットで保存する
              save_suspend_state_to_aws(
                  st.session_state.user_name, 
                  st.session_state.exam_code, 
                  st.session_state.quiz_questions, 
                  st.session_state.current_index,
                  st.session_state.get('activity_type', 'free_learning'),
                  st.session_state.get('test_run_id', ''),
                  st.session_state.get('test_set_id', ''),
                  st.session_state.get('test_attempt_no', 1)
              )
              
              st.session_state.suspended = True
              st.session_state.saved_session = {
                  'exam_code': st.session_state.exam_code,
                  'q_keys': [{"year": str(q.get("year", "")), "id": int(q.get("id", 0))} for q in st.session_state.quiz_questions],
                  'current_index': st.session_state.current_index,
                  'activity_type': st.session_state.get('activity_type', 'free_learning'),
                  'test_run_id': st.session_state.get('test_run_id', ''),
                  'test_set_id': st.session_state.get('test_set_id', ''),
                  'test_attempt_no': st.session_state.get('test_attempt_no', 1)
              }
              st.session_state.config_done = False
              st.rerun()