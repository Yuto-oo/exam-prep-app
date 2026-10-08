# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from aws_db import load_global_statistics_from_aws, load_global_bookmark_counts

def show_dashboard(questions, history, exam_code):
    st.title("📊 拡張学習分析ダッシュボード")
    
    exam_options = {"FEA": "基本情報技術者試験A", "FEB": "基本情報技術者試験B", "IP": "ITパスポート"}
    current_exam_name = exam_options.get(exam_code, "基本情報技術者試験A")
    
    col_ui1, col_ui2 = st.columns(2)
    with col_ui1:
        selected_exam_name = st.selectbox("🔍 分析表示する試験を切り替え", options=list(exam_options.values()), index=list(exam_options.values()).index(current_exam_name))
    
    selected_exam_code = [k for k, v in exam_options.items() if v == selected_exam_name][0]
    
    if selected_exam_code != exam_code:
        st.session_state.exam_code = selected_exam_code
        st.session_state.exam_name = selected_exam_name
        st.session_state.config_done = False
        st.session_state.quiz_questions = []
        st.session_state.current_index = 0
        st.session_state.answered = False
        st.rerun()

    with st.spinner("☁️ AWSからベンチマーク＆全体統計を解析中..."):
        global_stats = load_global_statistics_from_aws(selected_exam_code)
        global_bookmarks = load_global_bookmark_counts()

    my_raw_level = st.session_state.get('user_level', '')
    default_bench = "全体"
    if "初学者" in my_raw_level: default_bench = "初学者"
    elif "中級者" in my_raw_level: default_bench = "中級者"
    elif "上級者" in my_raw_level: default_bench = "上級者"

    with col_ui2:
        selected_bench = st.selectbox("👥 比較するベンチマーク層を選択", options=["全体", "初学者", "中級者", "上級者"], index=["全体", "初学者", "中級者", "上級者"].index(default_bench))

    st.write("---")
    if not history:
        st.info(f"💡 まだ学習データがありません。クイズに回答すると高度な分析結果が表示されます。")
        return

    # --- 📊 データ加工処理 ---
    data_list = []
    total_q = len(questions)
    unanswered_count, review_count, mastered_count = 0, 0, 0

    for q in questions:
        q_key = f"{selected_exam_code}_{q.get('year', '不明')}_{q.get('id', 0)}"
        hist = history.get(q_key, {})
        solve_count = hist.get('solve_count', 0)
        retention = hist.get('retention', None)
        days_until_review = hist.get('days_until_review', 0.0) 
        retention_pct = retention if retention is not None else None  
        
        needs_review = hist.get('needs_review', False)
        last_correct = hist.get('last_correct', None)
        last_confidence = str(hist.get('last_confidence', '少し自信あり'))
        
        times = hist.get('times', [])
        avg_time = sum(times) / len(times) if times else None
        
        times_1_2 = times[:2]
        times_3_plus = times[2:]
        
        avg_time_1_2 = sum(times_1_2) / len(times_1_2) if times_1_2 else None
        avg_time_3_plus = sum(times_3_plus) / len(times_3_plus) if times_3_plus else None

        if solve_count == 0:
            unanswered_count += 1
            status_label = "未解答"
        elif needs_review:
            review_count += 1
            status_label = "推定保持率40%以下"
        else:
            mastered_count += 1
            status_label = "推定保持率40%超"

        if solve_count > 0:
            data_list.append({
                "question_key": q_key, "category": q.get("category_large", "未分類"),
                "solve_count": solve_count, "retention_rate": retention_pct,
                "days_until_review": days_until_review, "last_correct": 1 if last_correct is True else 0,
                "last_confidence": last_confidence, "avg_time": avg_time, 
                "avg_time_1_2": avg_time_1_2, "avg_time_3_plus": avg_time_3_plus,
                "status": status_label
            })

    df = pd.DataFrame(data_list)

    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("総出題対象問題数", f"{total_q} 問")
    with c2: st.metric("解答済み問題数", f"{len(df)} 問", f"{(len(df)/total_q)*100:.1f}% 着手")
    with c3: st.metric("推定要復習問題数", f"{review_count} 問", delta=f"{review_count}問 該当" if review_count > 0 else "該当なし", delta_color="inverse")
    with c4: st.metric("平均推定記憶保持率", f"{df['retention_rate'].mean():.1f}%" if not df.empty else "0%", f"総ログ: {global_stats.get('total_logs', 0)} 件")

    st.write("---")

    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("📊 学習進捗ステータス")
        status_df = pd.DataFrame({"ステータス": ["未解答", "推定保持率40%以下", "推定保持率40%超"], "問題数": [unanswered_count, review_count, mastered_count]})
        fig_pie = px.pie(status_df, names="ステータス", values="問題数", hole=0.45, color="ステータス", color_discrete_map={"未解答": "#9E9E9E", "推定保持率40%以下": "#FF7043", "推定保持率40%超": "#66BB6A"})
        fig_pie.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=280, legend=dict(orientation="h", y=-0.1))
        st.plotly_chart(fig_pie, use_container_width=True)

    with col_right:
        st.subheader("🧠 推定記憶保持率の分布")
        if not df.empty and df["retention_rate"].notna().any():
            fig_hist = px.histogram(df, x="retention_rate", nbins=10, color_discrete_sequence=["#26A69A"])
            fig_hist.update_layout(xaxis_title="推定記憶保持率 (%)", yaxis_title="問題数", xaxis_range=[0, 100], margin=dict(t=20, b=20, l=20, r=20), height=280)
            st.plotly_chart(fig_hist, use_container_width=True)
        else:
            st.info("データが十分にありません。")

    st.write("---")
    st.subheader("🔍 🧠 「自信度 × 正誤」のギャップ分析 (メタ認知の可視化)")
    if not df.empty:
        def classify_gap(row):
            is_corr = row["last_correct"] == 1
            conf_str = str(row["last_confidence"])
            # 💡 「少し自信あり」を除外し、「自信あり」の完全一致のみに変更
            is_confident = conf_str == "自信あり"
            
            if is_confident and is_corr: return "🟢 高確信正答"
            elif not is_confident and is_corr: return "🟡 低確信正答"
            elif not is_confident and not is_corr: return "🔵 低確信誤答"
            else: return "🚨 高確信誤答"

        df["gap_status"] = df.apply(classify_gap, axis=1)
        gap_counts = df["gap_status"].value_counts().reset_index()
        gap_counts.columns = ["ステータス", "問題数"]
        
        col_gap1, col_gap2 = st.columns([3, 2])
        with col_gap1:
            fig_gap = px.pie(gap_counts, names="ステータス", values="問題数", hole=0.4, color="ステータス",
                             color_discrete_map={"🟢 高確信正答": "#66BB6A", "🟡 低確信正答": "#FFCA28", "🔵 低確信誤答": "#42A5F5", "🚨 高確信誤答": "#EF5350"})
            fig_gap.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=280)
            st.plotly_chart(fig_gap, use_container_width=True)
        with col_gap2:
            trick_cnt = df[df["gap_status"] == "🚨 高確信誤答"].shape[0]
            st.markdown("##### 💡 メタ認知アドバイス")
            if trick_cnt > 0:
                st.warning(f"⚠️ **高確信誤答が {trick_cnt} 問あります。**\n解説や関連知識を再確認する候補として活用してください。")
            else:
                st.success("✨ 現在、高確信誤答はありません。")

    st.write("---")
    st.subheader("🔥 試験全体の要注意問題ランキング (クラス統計)")
    g_questions = global_stats.get("questions", {})
    
    if g_questions or global_bookmarks:
        rank_data = []
        for q in questions:
            q_key = f"{selected_exam_code}_{q.get('year', '不明')}_{q.get('id', 0)}"
            if q_key in g_questions or global_bookmarks.get(q_key, 0) > 0:
                q_stat = g_questions.get(q_key, {"total_count": 0, "global_correct_rate": 0.0, "trick_count": 0})
                rank_data.append({
                    "問題キー": f"{q.get('year')} 問{q.get('id')}", 
                    "分野": q.get("category_large", "未分類"),
                    "全解答数": q_stat["total_count"], 
                    "正解率": q_stat["global_correct_rate"],
                    "おとり度(自信誤答)": q_stat["trick_count"],
                    "ブックマーク数": global_bookmarks.get(q_key, 0)
                })
                
        if rank_data:
            rdf = pd.DataFrame(rank_data)
            
            st.markdown("💀 **正答率が低い問題 Top 5**")
            diff_df = rdf[rdf["全解答数"] > 0].sort_values(by="正解率", ascending=True).head(5)
            if not diff_df.empty:
                st.dataframe(diff_df[["問題キー", "分野", "正解率"]].style.format({"正解率": "{:.1f}%"}), hide_index=True, use_container_width=True)
            else:
                st.info("データなし")
                
            st.write("")
            st.markdown("🪤 **高確信誤答が多い問題 Top 5**")
            trick_df = rdf[rdf["全解答数"] > 0].sort_values(by="おとり度(自信誤答)", ascending=False).head(5)
            if not trick_df.empty:
                st.dataframe(trick_df[["問題キー", "分野", "おとり度(自信誤答)"]], hide_index=True, use_container_width=True)
            else:
                st.info("データなし")
                
            st.write("")
            st.markdown("🔖 **皆の「ブックマーク」Top 5**")
            bm_df = rdf[rdf["ブックマーク数"] > 0].sort_values(by="ブックマーク数", ascending=False).head(5)
            if not bm_df.empty:
                st.dataframe(bm_df[["問題キー", "分野", "ブックマーク数"]], hide_index=True, use_container_width=True)
            else:
                st.info("データなし")
        else:
            st.info("ランキングデータがありません。")
    else:
        st.info("ランキングデータがありません。")

    st.write("---")
    st.subheader(f"🗂️ カテゴリ別分析 ({selected_bench}ベンチマーク比較)")
    if not df.empty:
        cat_summary = df.groupby("category").agg(
            correct_rate=("last_correct", "mean"),
            mean_time_1_2=("avg_time_1_2", "mean"),      
            mean_time_3_plus=("avg_time_3_plus", "mean")   
        ).reset_index()
        cat_summary["correct_rate"] = cat_summary["correct_rate"] * 100
        g_levels = global_stats.get("levels", {})
        target_stats = g_levels.get(selected_bench, {})
        
        cat_summary["global_correct_rate"] = cat_summary["category"].apply(lambda x: target_stats.get(x, {}).get("avg_correct_rate", None))
        cat_summary["global_mean_time"] = cat_summary["category"].apply(lambda x: target_stats.get(x, {}).get("avg_time", None))

        fig_cat = make_subplots(specs=[[{"secondary_y": True}]])
        
        fig_cat.add_trace(go.Bar(x=cat_summary["category"], y=cat_summary["correct_rate"], name="自分の正解率 (%)", marker_color="#42A5F5"), secondary_y=False)
        fig_cat.add_trace(go.Bar(x=cat_summary["category"], y=cat_summary["global_correct_rate"], name=f"{selected_bench}平均 (%)", marker_color="#B0BEC5", opacity=0.5), secondary_y=False)
        
        fig_cat.add_trace(go.Scatter(x=cat_summary["category"], y=cat_summary["mean_time_1_2"], name="自分の時間 (1・2回目)", mode="lines+markers", line=dict(color="#AB47BC", width=3)), secondary_y=True)
        fig_cat.add_trace(go.Scatter(x=cat_summary["category"], y=cat_summary["mean_time_3_plus"], name="自分の時間 (3回目以降)", mode="lines+markers", line=dict(color="#26A69A", width=3, dash="dash")), secondary_y=True)
        
        fig_cat.add_trace(go.Scatter(x=cat_summary["category"], y=cat_summary["global_mean_time"], name=f"{selected_bench}平均時間 (秒)", mode="lines+markers", line=dict(color="#FF9800", dash="dot")), secondary_y=True)
        
        fig_cat.update_layout(barmode='group', height=350, margin=dict(t=40, b=20, l=20, r=20), legend=dict(orientation="h", y=1.2))
        fig_cat.update_yaxes(range=[0, 100], secondary_y=False)
        
        st.plotly_chart(fig_cat, use_container_width=True)