"""Generate a paginated A4 PDF from a saved structured teaching report."""

from __future__ import annotations

from html import escape
from io import BytesIO
import os
from pathlib import Path


def _markup(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, list):
        value = "；".join(str(item) for item in value)
    return escape(str(value), quote=False).replace("\n", "<br/>")


def render_report_pdf(result: dict) -> bytes:
    """Render the structured report with tables whose long rows can split across pages."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import CondPageBreak, HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as error:
        raise RuntimeError("PDF 导出依赖未安装，请安装项目 requirements.txt 中的 reportlab") from error

    windows_fonts = Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts"
    font_paths = [
        os.getenv("PDF_CJK_FONT_PATH", ""),
        windows_fonts / "simsun.ttc",
        windows_fonts / "simhei.ttf",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    font = "STSong-Light"
    for path in font_paths:
        if not path or not Path(path).is_file():
            continue
        try:
            candidate = "TeachingReportCJK"
            if candidate not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(candidate, str(path), subfontIndex=0))
            font = candidate
            break
        except Exception:
            # Some Noto TTC files use CFF outlines unsupported by ReportLab.
            # Try the next installed font rather than failing the export.
            continue
    if font == "STSong-Light":
        pdfmetrics.registerFont(UnicodeCIDFont(font))
    ink = colors.HexColor("#1D302E")
    teal = colors.HexColor("#16574E")
    muted = colors.HexColor("#607470")
    line = colors.HexColor("#D5E2DC")
    pale = colors.HexColor("#F1F7F4")
    styles = {
        "title": ParagraphStyle("ReportTitle", fontName=font, fontSize=20, leading=28, textColor=teal, spaceAfter=7, wordWrap="CJK"),
        "subtitle": ParagraphStyle("ReportSubtitle", fontName=font, fontSize=9, leading=14, textColor=muted, spaceAfter=8, wordWrap="CJK"),
        "section": ParagraphStyle("ReportSection", fontName=font, fontSize=13, leading=19, textColor=teal, spaceBefore=14, spaceAfter=7, wordWrap="CJK"),
        "subheading": ParagraphStyle("ReportSubheading", fontName=font, fontSize=10, leading=16, textColor=teal, spaceBefore=8, spaceAfter=3, keepWithNext=True, wordWrap="CJK"),
        "body": ParagraphStyle("ReportBody", fontName=font, fontSize=9, leading=15, textColor=ink, spaceAfter=5, wordWrap="CJK", splitLongWords=True),
        "small": ParagraphStyle("ReportSmall", fontName=font, fontSize=8, leading=12.5, textColor=ink, wordWrap="CJK", splitLongWords=True),
        "label": ParagraphStyle("ReportLabel", fontName=font, fontSize=8.5, leading=13, textColor=teal, wordWrap="CJK"),
    }
    plan = result["integrated_plan"]
    basic = plan["basic_information"]
    diagnosis = plan["diagnosis_summary"]
    context = plan["teaching_context_and_conditions"]
    goals = plan["goal_design"]
    audit = result["audit_summary"]
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=16 * mm,
        title=str(basic["plan_title"]), author=str(basic.get("teacher_name") or ""),
    )
    flow = []

    def paragraph(value: object, style: str = "body"):
        return Paragraph(_markup(value), styles[style])

    def heading(number: int, title: str):
        flow.append(CondPageBreak(85))
        flow.append(paragraph(f"{number}. {title}", "section"))
        flow.append(HRFlowable(width="100%", thickness=.5, color=line, spaceAfter=5))

    def field(label: str, value: object):
        flow.append(Paragraph(f'<font color="#16574E">{escape(label)}：</font>{_markup(value)}', styles["body"]))

    def table(headers: list[str] | None, rows: list[list[object]], widths: list[float]):
        cells = [[paragraph(label, "label") for label in headers]] if headers else []
        cells.extend([[paragraph(item, "small") for item in row] for row in rows])
        report_table = Table(cells, colWidths=widths, repeatRows=1 if headers else 0, splitInRow=1, hAlign="LEFT")
        commands = [
            ("GRID", (0, 0), (-1, -1), .35, line),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
        commands.append(("BACKGROUND", (0, 0), (-1, 0), pale) if headers else ("BACKGROUND", (0, 0), (0, -1), pale))
        report_table.setStyle(TableStyle(commands))
        flow.extend([report_table, Spacer(1, 5)])

    flow.append(paragraph("精准干预教学方案", "subheading"))
    flow.append(paragraph(basic["plan_title"], "title"))
    flow.append(paragraph(f'{basic["class_name"]} · {basic["subject"]} · {basic["total_duration_minutes"]} 分钟', "subtitle"))
    field("审核结论", audit["overall_conclusion"])

    heading(1, "精准教学基本信息")
    table(["项目", "内容", "项目", "内容"], [
        ["学科", basic["subject"], "年级", basic["grade"]],
        ["教材版本与章节", basic["textbook_version_and_chapter"], "班级", basic["class_name"]],
        ["精准教学主题", basic["precision_teaching_topic"], "教师", basic["teacher_name"]],
    ], [29 * mm, 62 * mm, 29 * mm, 62 * mm])
    table(None, [
        ["精准教学目标", basic["precision_teaching_goals"]],
        ["精准教学内容", basic["precision_teaching_content"]],
    ], [42 * mm, 140 * mm])

    heading(2, "学生诊断结果")
    flow.append(paragraph("诊断任务", "subheading"))
    flow.append(paragraph(diagnosis["diagnostic_task"]["task_text"]))
    table(["SOLO 层级", "人数及比例", "典型表现", "主要进阶障碍"], [
        [f'{row["level"]} {row["level_name"]}',
         f'{row["count"]} 人 · {round(row["proportion"] * 100)}%',
         row.get("typical_performance") or "—", row.get("main_obstacles") or "—"]
        for row in diagnosis["distribution_rows"]
    ], [26 * mm, 29 * mm, 63 * mm, 64 * mm])
    field("班级诊断总述", diagnosis["overall_summary"]["teacher_facing_paragraph"])

    heading(3, "教学情境与条件")
    table(["项目", "教师填写内容"], [
        ["教学内容与课标分析", context["teaching_content_and_curriculum_analysis"]],
        ["教学重点与难点分析", context["teaching_focus_and_difficulty_analysis"]],
        ["教学时长", f'{context["planned_duration_minutes"]} 分钟'],
        ["教学环境与 AI 支持条件", context["teaching_environment_and_ai_support_conditions"]],
    ], [42 * mm, 140 * mm])

    heading(4, "精准干预目标")
    flow.append(paragraph("共同核心目标", "subheading"))
    flow.append(paragraph(goals["common_core_goal"]["goal_statement"]))
    table(["目标认知结构与理由", "可观察的达成表现"], [[
        goals["common_core_goal"]["structure_and_rationale"],
        goals["common_core_goal"]["observable_achievement"],
    ]], [91 * mm, 91 * mm])
    flow.append(paragraph("分层进阶目标", "subheading"))
    table(["目标层级", "面向学生", "具体目标内容", "可观察的达成表现"], [
        [goal["target_level"], goal["students_display"], goal["goal_statement"], goal["observable_achievement"]]
        for goal in goals["progression_goals"]
    ], [25 * mm, 31 * mm, 63 * mm, 63 * mm])
    field("说明", goals["progression_goal_note"])

    heading(5, "学习活动设计")
    for index, activity in enumerate(plan["activities"], 1):
        flow.append(paragraph(f'活动 {index}：{activity["activity_name"]}（{activity["duration_minutes"]} 分钟）', "subheading"))
        rows = [
            ["组织形式", activity["organization_forms"]],
            ["活动目标", activity["activity_objective"]],
            ["教师活动", activity["teacher_activities"]],
            ["学生活动", activity["student_activities"]],
        ]
        if activity.get("ai_support"):
            rows.append(["AI 辅助", activity["ai_support"]])
        rows.extend([
            ["学习材料与资源", activity["learning_materials_and_resources"]],
            ["学习产出", activity["learning_product"]],
        ])
        table(None, rows, [42 * mm, 140 * mm])

    heading(6, "形成性评价")
    table(["评价时机", "评价目标", "学习证据与判断标准", "后续调整", "判断主体"], [
        [item["timing"], item["evaluation_goal"], item["evidence_and_criteria"], item["adjustment"], item["decision_by"]]
        for item in plan["formative_evaluations"]
    ], [24 * mm, 36 * mm, 52 * mm, 49 * mm, 21 * mm])

    heading(7, "方案审核结果")
    table(["检查项目", "状态", "发现的问题", "修改建议"], [
        [item["check_item"], item["status"], item["issue"], item["suggestion"]]
        for item in audit["review_items"]
    ], [35 * mm, 25 * mm, 61 * mm, 61 * mm])
    field("审核结论", audit["overall_conclusion"])

    def page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(muted)
        canvas.drawCentredString(A4[0] / 2, 9 * mm, str(doc.page))
        canvas.restoreState()

    try:
        document.build(flow, onFirstPage=page_number, onLaterPages=page_number)
    except Exception as error:
        raise RuntimeError("PDF 排版失败，请检查报告内容") from error
    content = buffer.getvalue()
    if not content.startswith(b"%PDF-"):
        raise RuntimeError("PDF 生成失败")
    return content
