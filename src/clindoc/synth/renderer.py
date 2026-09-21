"""PDF renderer for synthetic clinical laboratory reports using ReportLab."""

from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from clindoc.synth.schema import LabReportGroundTruth


def _render_template_a(doc: SimpleDocTemplate, record: LabReportGroundTruth, story: list) -> None:
    """Template A: Memorial General Hospital (Navy Blue corporate style)."""
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        "TitleA",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#1A365D"),
    )
    subtitle_style = ParagraphStyle(
        "SubtitleA",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#4A5568"),
    )
    meta_style = ParagraphStyle(
        "MetaA",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#2D3748"),
    )
    meta_bold = ParagraphStyle(
        "MetaABold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#1A202C"),
    )
    panel_header_style = ParagraphStyle(
        "PanelA",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#1A365D"),
    )
    
    # Facility Header
    story.append(Paragraph(record.facility_name, title_style))
    story.append(Paragraph("100 Hospital Drive, Healthcare City | CLIA ID: 99D0876543 | Phone: (555) 019-2831", subtitle_style))
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1A365D"), spaceAfter=10))
    
    # Patient Demographics Box
    patient_data = [
        [
            Paragraph("<b>Patient:</b> " + record.patient.name, meta_style),
            Paragraph("<b>Patient ID:</b> " + record.patient.patient_id, meta_style),
            Paragraph("<b>DOB:</b> " + record.patient.dob, meta_style),
        ],
        [
            Paragraph("<b>Gender:</b> " + ("Male" if record.patient.gender == "M" else "Female"), meta_style),
            Paragraph("<b>Ordering MD:</b> " + record.ordering_provider, meta_style),
            Paragraph("<b>Report Date:</b> " + record.report_date, meta_style),
        ],
    ]
    p_table = Table(patient_data, colWidths=[180, 180, 160])
    p_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(p_table)
    story.append(Spacer(1, 14))
    
    # Group tests by panel
    panels: dict[str, list] = {}
    for t in record.tests:
        panels.setdefault(t.panel_name, []).append(t)
        
    for panel_name, tests in panels.items():
        story.append(Paragraph(panel_name.upper(), panel_header_style))
        story.append(Spacer(1, 4))
        
        table_rows = [
            [
                Paragraph("<b>Test Name</b>", meta_bold),
                Paragraph("<b>Result</b>", meta_bold),
                Paragraph("<b>Flag</b>", meta_bold),
                Paragraph("<b>Reference Range</b>", meta_bold),
                Paragraph("<b>Units</b>", meta_bold),
            ]
        ]
        
        for t in tests:
            flag_text = "" if t.flag == "NORMAL" else t.flag
            flag_style = ParagraphStyle(
                "FlagStyleA",
                parent=meta_bold,
                textColor=colors.HexColor("#C53030") if flag_text else colors.HexColor("#2D3748"),
            )
            table_rows.append([
                Paragraph(t.test_name, meta_style),
                Paragraph(str(t.value), meta_bold if flag_text else meta_style),
                Paragraph(flag_text, flag_style),
                Paragraph(t.ref_range_text, meta_style),
                Paragraph(t.unit, meta_style),
            ])
            
        t_table = Table(table_rows, colWidths=[180, 80, 70, 110, 80])
        t_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
                ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#1A365D")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ])
        )
        story.append(t_table)
        story.append(Spacer(1, 12))


def _render_template_b(doc: SimpleDocTemplate, record: LabReportGroundTruth, story: list) -> None:
    """Template B: Metro Health Diagnostic Center (Teal/modern slate layout)."""
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        "TitleB",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#0D9488"),
    )
    subtitle_style = ParagraphStyle(
        "SubtitleB",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#64748B"),
    )
    meta_style = ParagraphStyle(
        "MetaB",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#334155"),
    )
    meta_bold = ParagraphStyle(
        "MetaBBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#0F172A"),
    )
    panel_header_style = ParagraphStyle(
        "PanelB",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#0F766E"),
    )
    
    # Facility Header
    story.append(Paragraph(record.facility_name, title_style))
    story.append(Paragraph("Outpatient Pathology & Clinical Analytics | Accreditations: CAP / CLIA", subtitle_style))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#0D9488"), spaceAfter=10))
    
    # Patient Block in 2-column key-value format
    patient_data = [
        [
            Paragraph("<b>Patient Name:</b> " + record.patient.name, meta_style),
            Paragraph("<b>Requisition ID:</b> " + record.doc_id, meta_style),
        ],
        [
            Paragraph("<b>MRN / ID:</b> " + record.patient.patient_id, meta_style),
            Paragraph("<b>Collection Date:</b> " + record.report_date, meta_style),
        ],
        [
            Paragraph("<b>Date of Birth:</b> " + record.patient.dob + f" ({record.patient.gender})", meta_style),
            Paragraph("<b>Ordering Clinician:</b> " + record.ordering_provider, meta_style),
        ],
    ]
    p_table = Table(patient_data, colWidths=[260, 260])
    p_table.setStyle(
        TableStyle([
            ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ])
    )
    story.append(p_table)
    story.append(Spacer(1, 14))
    
    # Panels
    panels: dict[str, list] = {}
    for t in record.tests:
        panels.setdefault(t.panel_name, []).append(t)
        
    for panel_name, tests in panels.items():
        story.append(Paragraph(f"<b>Panel:</b> {panel_name}", panel_header_style))
        story.append(Spacer(1, 4))
        
        table_rows = [
            [
                Paragraph("<b>Test Description</b>", meta_bold),
                Paragraph("<b>Observed Value</b>", meta_bold),
                Paragraph("<b>Status</b>", meta_bold),
                Paragraph("<b>Reference Interval</b>", meta_bold),
                Paragraph("<b>Units</b>", meta_bold),
            ]
        ]
        
        for t in tests:
            flag_text = "NORMAL" if t.flag == "NORMAL" else t.flag
            flag_color = colors.HexColor("#B91C1C") if t.flag != "NORMAL" else colors.HexColor("#15803D")
            flag_style = ParagraphStyle("FlagB", parent=meta_bold, textColor=flag_color)
            
            table_rows.append([
                Paragraph(t.test_name, meta_style),
                Paragraph(str(t.value), meta_bold if t.flag != "NORMAL" else meta_style),
                Paragraph(flag_text, flag_style),
                Paragraph(t.ref_range_text, meta_style),
                Paragraph(t.unit, meta_style),
            ])
            
        t_table = Table(table_rows, colWidths=[180, 85, 75, 100, 80])
        t_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#CCFBF1")),
                ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#0D9488")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ])
        )
        story.append(t_table)
        story.append(Spacer(1, 12))


def _render_template_c(doc: SimpleDocTemplate, record: LabReportGroundTruth, story: list) -> None:
    """Template C: BioPath Reference Laboratory (Burgundy/formal pathology layout)."""
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        "TitleC",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#831843"),
    )
    subtitle_style = ParagraphStyle(
        "SubtitleC",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#71717A"),
    )
    meta_style = ParagraphStyle(
        "MetaC",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#27272A"),
    )
    meta_bold = ParagraphStyle(
        "MetaCBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#18181B"),
    )
    panel_header_style = ParagraphStyle(
        "PanelC",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#831843"),
    )
    
    # Facility Header
    story.append(Paragraph(record.facility_name, title_style))
    story.append(Paragraph("Division of Laboratory Medicine | Clinical Report", subtitle_style))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#831843"), spaceAfter=8))
    
    # 4-Column Patient Box
    patient_data = [
        [
            Paragraph("<b>Patient Name:</b>", meta_bold),
            Paragraph(record.patient.name, meta_style),
            Paragraph("<b>Report Date:</b>", meta_bold),
            Paragraph(record.report_date, meta_style),
        ],
        [
            Paragraph("<b>Patient ID:</b>", meta_bold),
            Paragraph(record.patient.patient_id, meta_style),
            Paragraph("<b>Physician:</b>", meta_bold),
            Paragraph(record.ordering_provider, meta_style),
        ],
        [
            Paragraph("<b>DOB / Gender:</b>", meta_bold),
            Paragraph(f"{record.patient.dob} ({record.patient.gender})", meta_style),
            Paragraph("<b>Document ID:</b>", meta_bold),
            Paragraph(record.doc_id, meta_style),
        ],
    ]
    p_table = Table(patient_data, colWidths=[90, 170, 90, 170])
    p_table.setStyle(
        TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E4E4E7")),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAFAFA")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#F4F4F5")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ])
    )
    story.append(p_table)
    story.append(Spacer(1, 12))
    
    # Panels
    panels: dict[str, list] = {}
    for t in record.tests:
        panels.setdefault(t.panel_name, []).append(t)
        
    for panel_name, tests in panels.items():
        story.append(Paragraph(f"<b>SECTION: {panel_name.upper()}</b>", panel_header_style))
        story.append(Spacer(1, 4))
        
        table_rows = [
            [
                Paragraph("<b>Analyte</b>", meta_bold),
                Paragraph("<b>Result</b>", meta_bold),
                Paragraph("<b>Units</b>", meta_bold),
                Paragraph("<b>Reference Range</b>", meta_bold),
                Paragraph("<b>Abnormal</b>", meta_bold),
            ]
        ]
        
        for t in tests:
            flag_text = "" if t.flag == "NORMAL" else ("H" if t.flag == "HIGH" else "L")
            flag_color = colors.HexColor("#991B1B") if flag_text else colors.HexColor("#27272A")
            flag_style = ParagraphStyle("FlagC", parent=meta_bold, textColor=flag_color)
            
            table_rows.append([
                Paragraph(t.test_name, meta_style),
                Paragraph(str(t.value), meta_bold if flag_text else meta_style),
                Paragraph(t.unit, meta_style),
                Paragraph(t.ref_range_text, meta_style),
                Paragraph(flag_text, flag_style),
            ])
            
        t_table = Table(table_rows, colWidths=[180, 80, 80, 110, 70])
        t_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FCE7F3")),
                ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#831843")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#F4F4F5")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ])
        )
        story.append(t_table)
        story.append(Spacer(1, 10))


def render_report_to_pdf(record: LabReportGroundTruth, output_path: str | Path) -> Path:
    """Render a single LabReportGroundTruth record into a styled PDF file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    
    story = []
    if record.template_id == "template_b":
        _render_template_b(doc, record, story)
    elif record.template_id == "template_c":
        _render_template_c(doc, record, story)
    else:
        _render_template_a(doc, record, story)
        
    doc.build(story)
    return output_path
