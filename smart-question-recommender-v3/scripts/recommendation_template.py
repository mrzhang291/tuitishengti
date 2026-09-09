"""HTML 模板常量模块。

将推荐题 HTML 输出的文档骨架、全局 CSS、MathJax 配置、全局 JavaScript
统一集中在此，避免 recommend.py 内字符串拼接散落各处。

设计原则：
- 本文件只存放**纯字符串常量**，不含数据依赖。
- 需要数据的模块渲染函数（_render_student_profile 等）仍留在 recommend.py。
- CSS 类前缀统一为 `.sq-*`（smart-question 缩写），避免与旧版
  `.smart-question-recommendation .xxx` 冲突。
- 公式渲染统一走 MathJax 浏览器端渲染，保留题源原始 LaTeX `\\(...\\)` / `\\[...\\]`。
"""

# ============================================================
# 文档骨架
# ============================================================

HTML_HEAD_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    /* 全局基础样式 */
    body {{
      font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      line-height: 1.6;
      color: #1a1a1a;
      max-width: 960px;
      margin: 0 auto;
      padding: 20px;
      background: #f8f9fa;
    }}
    .sq-container {{
      background: #fff;
      padding: 24px;
      border-radius: 12px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    }}
    h3 {{ color: #0f5e9c; border-bottom: 2px solid #e3f2fd; padding-bottom: 8px; margin-top: 0; }}
    h4 {{ color: #1565c0; margin-top: 24px; }}
    h5 {{ color: #1976d2; margin: 0 0 8px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 14px; }}
    th, td {{ border: 1px solid #d6d9df; padding: 8px 12px; text-align: left; }}
    th {{ background: #f1f5f9; font-weight: 600; color: #334155; }}
    tr:nth-child(even) {{ background: #fafbfc; }}
    code {{ background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 12px; color: #0f5e9c; }}

    /* 使用说明 */
    .sq-usage-note {{
      background: #e3f2fd;
      border: 1px solid #90caf9;
      border-radius: 8px;
      padding: 14px 18px;
      margin-bottom: 20px;
      font-size: 14px;
      color: #0d47a1;
    }}
    .sq-usage-note strong {{ color: #0d47a1; }}

    /* 学生画像 */
    .sq-profile {{ margin: 16px 0; }}
    .sq-profile-table {{ width: auto; margin: 8px 0; }}
    .sq-profile-table th, .sq-profile-table td {{ text-align: center; font-size: 13px; }}

    /* 薄弱点 */
    .sq-weak-points ol {{ margin: 8px 0; padding-left: 24px; }}
    .sq-weak-points li {{ margin: 4px 0; }}

    /* 覆盖度警告 */
    .sq-coverage {{
      margin: 16px 0;
      padding: 12px 16px;
      background: #fff3cd;
      border: 1px solid #ffeaa7;
      border-radius: 8px;
      color: #856404;
    }}

    /* 题目卡片 */
    .sq-card {{
      border: 1px solid #d6d9df;
      border-radius: 8px;
      padding: 16px;
      margin: 16px 0;
      background: #fff;
    }}
    .sq-card-meta {{
      color: #5b6472;
      font-size: 13px;
      margin: 4px 0;
      line-height: 1.7;
    }}
    .sq-card-meta strong {{
      color: #334155;
      display: inline-block;
      min-width: 5em;
    }}
    .sq-card-section {{
      margin-top: 12px;
      padding-top: 8px;
      border-top: 1px dashed #e0e0e0;
    }}
    .sq-card-section strong {{ color: #0f5e9c; }}
    .sq-card-section p {{ margin: 6px 0; }}

    /* 反馈表单 */
    .sq-feedback {{
      margin-top: 12px;
      padding-top: 10px;
      border-top: 1px dashed #e0e0e0;
    }}
    .sq-feedback details > summary {{ font-weight: 600; cursor: pointer; font-size: 13px; color: #666; }}
    .sq-feedback-panel {{
      margin-top: 8px;
      padding: 10px;
      background: #f9fafb;
      border-radius: 6px;
    }}
    .sq-feedback-quick {{ margin-bottom: 12px; }}
    .sq-feedback-quick-label {{ font-size: 13px; color: #64748b; margin-bottom: 8px; }}
    .sq-feedback-quick-options {{ display: flex; flex-wrap: wrap; gap: 12px; font-size: 13px; }}
    .sq-feedback-quick-options label {{ cursor: pointer; user-select: none; }}
    .sq-feedback-detail {{
      display: none;
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px dashed #cbd5e1;
    }}
    .sq-feedback-detail .fb-section {{ display: none; }}
    .sq-feedback-detail-label {{ font-size: 13px; color: #64748b; margin-bottom: 8px; }}
    .sq-feedback-detail-options {{ display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; }}
    .sq-feedback-detail-options label {{ cursor: pointer; }}
    .sq-feedback-current-tag {{
      font-size: 12px;
      color: #0f5e9c;
      background: #e3f2fd;
      padding: 6px 10px;
      border-radius: 4px;
      margin-bottom: 8px;
    }}
    .sq-feedback-text {{
      width: 100%;
      padding: 6px 10px;
      border: 1px solid #d6d9df;
      border-radius: 4px;
      font-size: 13px;
      box-sizing: border-box;
      font-family: inherit;
    }}

    /* 总体建议 */
    .sq-overall {{
      margin: 24px 0;
      padding: 18px 20px;
      background: #e3f2fd;
      border: 1px solid #90caf9;
      border-radius: 8px;
    }}
    .sq-overall h4 {{ margin: 0 0 6px; color: #0d47a1; }}
    .sq-overall-quality-label {{ font-size: 14px; color: #333; margin-bottom: 8px; font-weight: 600; }}
    .sq-overall-quality-options {{ display: flex; flex-wrap: wrap; gap: 20px; font-size: 14px; }}
    .sq-overall-quality-options label {{ cursor: pointer; }}
    .sq-overall-text-label {{ font-size: 14px; color: #333; margin-bottom: 8px; font-weight: 600; }}
    .sq-overall-text {{
      width: 100%;
      padding: 8px 12px;
      border: 1px solid #d6d9df;
      border-radius: 4px;
      font-size: 13px;
      box-sizing: border-box;
      line-height: 1.5;
      font-family: inherit;
    }}

    /* 导出栏 */
    .sq-export {{
      margin: 24px 0;
      padding: 16px;
      background: #e8f5e9;
      border: 2px solid #166534;
      border-radius: 8px;
      text-align: center;
    }}
    .sq-export h4 {{ margin: 0 0 8px; color: #166534; }}
    .sq-export-button {{
      padding: 10px 24px;
      font-size: 15px;
      border: none;
      border-radius: 6px;
      background: #166534;
      color: #fff;
      cursor: pointer;
      font-weight: 600;
    }}
    .sq-export-button:hover {{ background: #14532d; }}
  </style>
  <script>
    window.MathJax = {{
      tex: {{
        inlineMath: [['\\\\(', '\\\\)'], ['$', '$']],
        displayMath: [['\\\\[', '\\\\]'], ['$$', '$$']],
        processEscapes: true,
        tags: 'none'
      }},
      svg: {{ fontCache: 'global' }},
      options: {{ skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'] }}
    }};
  </script>
  <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js" id="MathJax-script" async></script>
</head>
<body>
  <div class="sq-container">
"""

HTML_FOOTER = """  </div>
  <script>
  // 反馈详情展开/收起
  function updateFeedbackDetail(shortId) {
    var detailPanel = document.getElementById('feedback-detail-' + shortId);
    if (!detailPanel) return;
    var radios = document.querySelectorAll('input[name="feedback-main-' + shortId + '"]');
    var selectedValue = 'accept';
    radios.forEach(function(r) { if (r.checked) selectedValue = r.value; });
    if (selectedValue === 'accept') {
      detailPanel.style.display = 'none';
    } else {
      detailPanel.style.display = 'block';
      detailPanel.querySelectorAll('.fb-section').forEach(function(s) { s.style.display = 'none'; });
      var target = detailPanel.querySelector('.fb-section[data-for="' + selectedValue + '"]');
      if (target) target.style.display = 'block';
    }
  }

  // 一键导出全部反馈
  function exportAllFeedback() {
    var allFeedback = [];
    var containers = document.querySelectorAll('div[id^="fb-"]');
    var seen = new Set();
    containers.forEach(function(c) {
      var id = c.id;
      if (id.startsWith('fb-knowledge-') || id.startsWith('fb-other-')) return;
      var qid = id.replace('fb-', '');
      if (seen.has(qid)) return;
      seen.add(qid);
      var shortId = qid.replace(/-/g, '_');
      var radios = document.querySelectorAll('input[name="feedback-main-' + shortId + '"]');
      var mainFeedback = 'accept';
      radios.forEach(function(r) { if (r.checked) mainFeedback = r.value; });
      var result = {
        "feedback_id": "fb_" + Math.random().toString(36).substr(2, 8),
        "timestamp": new Date().toISOString(),
        "skill_source": "recommender",
        "feedback_type": "recommendation_review",
        "question_id": qid,
        "is_generated": false,
        "disposition": mainFeedback,
        "used_in_class": false,
        "context": {"feedback_main": mainFeedback}
      };
      if (mainFeedback === 'difficulty') {
        var diffRadios = document.querySelectorAll('input[name="difficulty-" + shortId]');
        var diffVal = '';
        diffRadios.forEach(function(r) { if (r.checked) diffVal = r.value; });
        result["ratings"] = diffVal;
        result["context"]["difficulty_level"] = diffVal;
        result["teacher_notes"] = '';
      } else if (mainFeedback === 'knowledge') {
        var knInput = document.getElementById('fb-knowledge-' + shortId);
        var knVal = knInput ? knInput.value : '';
        result["ratings"] = null;
        result["context"]["knowledge_tag_suggested"] = knVal;
        result["teacher_notes"] = knVal;
      } else if (mainFeedback === 'other') {
        var otInput = document.getElementById('fb-other-' + shortId);
        var otVal = otInput ? otInput.value : '';
        result["ratings"] = null;
        result["context"]["other_notes"] = otVal;
        result["teacher_notes"] = otVal;
      } else {
        result["ratings"] = null;
        result["teacher_notes"] = '';
      }
      allFeedback.push(result);
    });
    var studentName = '';
    var h3 = document.querySelector('h3');
    if (h3) studentName = h3.textContent.replace(/[^\\u4e00-\\u9fa5a-zA-Z0-9]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
    var qualityRadios = document.querySelectorAll('input[name="overall-quality"]');
    var overallQuality = '';
    qualityRadios.forEach(function(r) { if (r.checked) overallQuality = r.value; });
    var textFeedback = '';
    var textArea = document.getElementById('overall-text-feedback');
    if (textArea) textFeedback = textArea.value;
    var overallFeedback = {
      "recommendation_quality": overallQuality,
      "text_feedback": textFeedback
    };
    var exportData = {
      "student": h3 ? h3.textContent : 'unknown',
      "export_time": new Date().toISOString(),
      "overall_feedback": overallFeedback,
      "feedback_count": allFeedback.length,
      "feedbacks": allFeedback
    };
    var dataStr = JSON.stringify(exportData, null, 2);
    var blob = new Blob([dataStr], {type: "application/json"});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'feedback_' + studentName + '_' + new Date().toISOString().slice(0,10) + '.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    alert('已导出 ' + allFeedback.length + ' 道题的反馈数据！\\n文件已下载，请发回给我们。');
  }
  </script>
</body>
</html>
"""

# ============================================================
# 使用说明（固定文案）
# ============================================================

USAGE_NOTE_HTML = """    <div class="sq-usage-note">
      <strong>📌 使用说明：</strong>请逐题审阅完整题面与答案解析，在每道题底部的"教师反馈"区域标记反馈。
      完成全部题目后，可在页面下方"💬 总体建议"区域填写整体感受，
      最后点击 <strong>"📤 导出全部反馈"</strong> 按钮，将下载的 JSON 文件发回给我们即可。
    </div>"""

# ============================================================
# 总体建议区（固定结构）
# ============================================================

OVERALL_FEEDBACK_HTML = """    <div class="sq-overall">
      <h4>💬 总体建议（可选）</h4>
      <p style="font-size: 13px; color: #555; margin: 0 0 14px;">请填写您对本次推荐的整体感受，帮助我们持续优化推荐质量。</p>
      <div style="margin-bottom: 14px;">
        <div class="sq-overall-quality-label">推荐质量：</div>
        <div class="sq-overall-quality-options">
          <label><input type="radio" name="overall-quality" value="needs_improvement" style="margin-right: 4px;"> 待提升</label>
          <label><input type="radio" name="overall-quality" value="basically_satisfied" style="margin-right: 4px;"> 基本满意</label>
          <label><input type="radio" name="overall-quality" value="satisfied" style="margin-right: 4px;"> 满意</label>
          <label><input type="radio" name="overall-quality" value="excellent" style="margin-right: 4px;"> 优秀</label>
        </div>
      </div>
      <div>
        <div class="sq-overall-text-label">文字建议：</div>
        <textarea id="overall-text-feedback" rows="4" class="sq-overall-text" placeholder="例如：题型搭配是否合理、题量是否合适、难度是否恰当、是否覆盖学生主要薄弱点、与教学进度的匹配度、是否出现重复题目、推荐顺序是否合理 等"></textarea>
      </div>
    </div>"""

# ============================================================
# 导出栏（固定结构）
# ============================================================

EXPORT_BAR_HTML = """    <div class="sq-export">
      <h4>📊 一键导出全部反馈</h4>
      <p style="font-size: 13px; color: #555; margin: 0 0 12px;">完成所有题目的审阅后，点击下方按钮导出完整的反馈数据文件，发回给我们即可。</p>
      <button type="button" class="sq-export-button" onclick="exportAllFeedback()">📤 导出全部反馈</button>
    </div>"""
