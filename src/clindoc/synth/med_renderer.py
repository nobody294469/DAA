"""PDF renderer for synthetic medication documents and prescriptions using ReportLab."""

from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from clindoc.synth.med_schema import PrescriptionGroundTruth


def _render_clinic_rx_pad(record: PrescriptionGroundTruth, story: list) -> None:
    """Template 1: Outpatient Clinic Prescription Pad."""
    styles = getSampleStyleSheet()

    header_style = ParagraphStyle(
        "RxHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#1A365D"),
    )
    sub_style = ParagraphStyle(
        "RxSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#4A5568"),
    )
    bold_style = ParagraphStyle(
        "RxBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#1A202C"),
    )
    normal_style = ParagraphStyle(
        "RxNorm",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#2D3748"),
    )
    rx_symbol_style = ParagraphStyle(
        "RxSymbol",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=26,
        textColor=colors.HexColor("#2B6CB0"),
    )
    sig_style = ParagraphStyle(
        "RxSig",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=15,
        textColor=colors.HexColor("#1A202C"),
    )

    # Header
    story.append(Paragraph(record.facility_name, header_style))
    story.append(Paragraph("1200 Healthcare Parkway, Suite 400 | Ph: (555) 321-7890 | Fax: (555) 321-7899", sub_style))
    story.append(Paragraph(f"Prescriber: {record.prescriber} | DEA: BV9284719 | NPI: 1982740192", sub_style))
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1A365D"), spaceAfter=10))

    # Patient info
    p = record.patient
    pat_data = [
        [
            Paragraph(f"<b>Patient Name:</b> {p.name}", normal_style),
            Paragraph(f"<b>DOB:</b> {p.dob}", normal_style),
            Paragraph(f"<b>Date:</b> {record.prescription_date}", normal_style),
        ],
        [
            Paragraph(f"<b>MRN:</b> {p.patient_id}", normal_style),
            Paragraph(f"<b>Gender:</b> {p.gender}", normal_style),
            Paragraph(f"<b>Doc ID:</b> {record.doc_id}", normal_style),
        ],
    ]
    pat_table = Table(pat_data, colWidths=[200, 160, 150])
    pat_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
        ])
    )
    story.append(pat_table)
    story.append(Spacer(1, 15))

    # Rx Symbol & Prescriptions
    story.append(Paragraph("Rx", rx_symbol_style))
    story.append(Spacer(1, 8))

    for med in record.medications:
        # Drug header line
        drug_line = f"<b>{med.medication_name}</b> {med.dose:g} {med.dose_unit} {med.dosage_form}"
        story.append(Paragraph(drug_line, sig_style))
        story.append(Spacer(1, 4))
        # Sig line
        story.append(Paragraph(f"<b>Sig:</b> {med.sig_text}", normal_style))
        story.append(Spacer(1, 4))
        # Dispense & Refill details
        disp_line = f"<b>Dispense Quantity:</b> #{med.quantity} &nbsp;&nbsp;&nbsp;&nbsp; <b>Refills:</b> {med.refills}"
        story.append(Paragraph(disp_line, normal_style))
        story.append(Spacer(1, 12))

    story.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#CBD5E0"), spaceAfter=15))
    story.append(Spacer(1, 15))
    story.append(Paragraph(f"Signature: <i>{record.prescriber}</i> (Electronically Signed)", bold_style))
    story.append(Paragraph("Substitution Permitted [ ] &nbsp;&nbsp;&nbsp;&nbsp; Dispense as Written [X]", sub_style))


def _render_discharge_summary(record: PrescriptionGroundTruth, story: list) -> None:
    """Template 2: Hospital Discharge Medication Summary."""
    styles = getSampleStyleSheet()

    header_style = ParagraphStyle(
        "DischHead",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=17,
        textColor=colors.HexColor("#2C5282"),
    )
    sub_style = ParagraphStyle(
        "DischSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#4A5568"),
    )
    table_cell = ParagraphStyle(
        "DischCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
    )
    table_cell_bold = ParagraphStyle(
        "DischCellBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
    )

    story.append(Paragraph(record.facility_name, header_style))
    story.append(Paragraph("DISCHARGE INSTRUCTIONS & POST-ACUTE MEDICATION RECONCILIATION", sub_style))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#2C5282"), spaceAfter=8))

    p = record.patient
    pat_line = f"<b>Patient:</b> {p.name} | <b>MRN:</b> {p.patient_id} | <b>DOB:</b> {p.dob} | <b>Discharge Date:</b> {record.prescription_date}"
    story.append(Paragraph(pat_line, sub_style))
    story.append(Paragraph(f"<b>Attending Clinician:</b> {record.prescriber} | <b>Doc Ref:</b> {record.doc_id}", sub_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>Prescribed Discharge Medications</b>", table_cell_bold))
    story.append(Spacer(1, 6))

    table_data = [
        [
            Paragraph("<b>Medication</b>", table_cell_bold),
            Paragraph("<b>Strength / Dose</b>", table_cell_bold),
            Paragraph("<b>Route & Frequency</b>", table_cell_bold),
            Paragraph("<b>Instructions (Sig)</b>", table_cell_bold),
            Paragraph("<b>Qty / Refills</b>", table_cell_bold),
        ]
    ]

    for med in record.medications:
        table_data.append([
            Paragraph(f"<b>{med.medication_name}</b>", table_cell),
            Paragraph(f"{med.dose:g} {med.dose_unit} {med.dosage_form}", table_cell),
            Paragraph(f"{med.route.upper() if len(med.route) <= 3 else med.route}<br/>{med.frequency}", table_cell),
            Paragraph(med.sig_text, table_cell),
            Paragraph(f"#{med.quantity} (Ref: {med.refills})", table_cell),
        ])

    table = Table(table_data, colWidths=[110, 85, 100, 150, 70])
    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EBF8FF")),
            ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.HexColor("#2B6CB0")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )
    story.append(table)
    story.append(Spacer(1, 15))
    story.append(Paragraph("<i>Important: Discontinue any previous dosages of these medications. Follow up with your primary physician in 7 to 14 days.</i>", sub_style))


def _render_pharmacy_label(record: PrescriptionGroundTruth, story: list) -> None:
    """Template 3: Retail Pharmacy Prescription Container Label."""
    styles = getSampleStyleSheet()

    pharm_style = ParagraphStyle(
        "PharmName",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#9B2C2C"),
    )
    sub_style = ParagraphStyle(
        "PharmSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#718096"),
    )
    sig_style = ParagraphStyle(
        "PharmSig",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#1A202C"),
    )
    normal_style = ParagraphStyle(
        "PharmNorm",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#2D3748"),
    )

    story.append(Paragraph(record.facility_name, pharm_style))
    story.append(Paragraph("800 Main Street | Store Ph: (555) 789-0123 | Pharmacy Lic # 882741", sub_style))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#9B2C2C"), spaceAfter=8))

    doc_num = int(record.doc_id.split("_")[1]) if "_" in record.doc_id else 1000
    rx_num = f"RX#{7000000 + doc_num}"

    med = record.medications[0]
    p = record.patient

    meta_line = f"<b>{rx_num}</b> &nbsp;&nbsp;&nbsp;&nbsp; Date Filled: {record.prescription_date} &nbsp;&nbsp;&nbsp;&nbsp; Discard After: 1 Year"
    story.append(Paragraph(meta_line, normal_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"<b>PATIENT:</b> {p.name.upper()} &nbsp;&nbsp; (MRN: {p.patient_id})", normal_style))
    story.append(Spacer(1, 10))

    # Prominent Sig Box
    sig_content = [
        [Paragraph(f"<b>DIRECTIONS:</b><br/>{med.sig_text.upper()}", sig_style)],
        [Paragraph(f"<b>MEDICATION:</b> {med.medication_name.upper()} {med.dose:g} {med.dose_unit.upper()} {med.dosage_form.upper()}", normal_style)],
        [Paragraph(f"<b>QTY:</b> {med.quantity} &nbsp;&nbsp;|&nbsp;&nbsp; <b>REFILLS LEFT:</b> {med.refills} &nbsp;&nbsp;|&nbsp;&nbsp; <b>PRESCRIBER:</b> {record.prescriber}", normal_style)],
    ]
    sig_table = Table(sig_content, colWidths=[510])
    sig_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF5F5")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#E53E3E")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ])
    )
    story.append(sig_table)
    story.append(Spacer(1, 10))
    story.append(Paragraph("<b>CAUTION:</b> Federal law prohibits transfer of this drug to any person other than the patient for whom it was prescribed.", sub_style))


def _render_mar_order_sheet(record: PrescriptionGroundTruth, story: list) -> None:
    """Template 4: Medication Administration Record / Order Sheet."""
    styles = getSampleStyleSheet()

    header_style = ParagraphStyle(
        "MARHead",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#234E52"),
    )
    sub_style = ParagraphStyle(
        "MARSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#4A5568"),
    )
    order_title = ParagraphStyle(
        "MARTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#1D4044"),
    )
    order_body = ParagraphStyle(
        "MARBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#2D3748"),
    )

    story.append(Paragraph(record.facility_name, header_style))
    story.append(Paragraph("PHYSICIAN MEDICATION ORDERS & CLINICAL ADMINISTRATION RECORD", sub_style))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#234E52"), spaceAfter=8))

    p = record.patient
    pat_line = f"<b>Patient:</b> {p.name} | <b>DOB:</b> {p.dob} | <b>MRN:</b> {p.patient_id} | <b>Order Date:</b> {record.prescription_date}"
    story.append(Paragraph(pat_line, sub_style))
    story.append(Paragraph(f"<b>Ordering Provider:</b> {record.prescriber} | <b>Doc ID:</b> {record.doc_id}", sub_style))
    story.append(Spacer(1, 10))

    for idx, med in enumerate(record.medications, 1):
        order_box = [
            [
                Paragraph(f"<b>Order #{idx}: {med.medication_name}</b> {med.dose:g} {med.dose_unit}", order_title),
                Paragraph(f"<b>Route:</b> {med.route} | <b>Form:</b> {med.dosage_form}", order_body),
            ],
            [
                Paragraph(f"<b>Schedule/Frequency:</b> {med.frequency}", order_body),
                Paragraph(f"<b>Duration:</b> {med.duration or 'Ongoing'} | <b>Dispense:</b> #{med.quantity}", order_body),
            ],
            [
                Paragraph(f"<b>Instructions:</b> {med.sig_text}", order_body),
                Paragraph(f"<b>Refills:</b> {med.refills}", order_body),
            ],
        ]
        t = Table(order_box, colWidths=[270, 240])
        t.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E6FFFA")),
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#319795")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#B2F5EA")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ])
        )
        story.append(t)
        story.append(Spacer(1, 8))


def render_prescription_pdf(record: PrescriptionGroundTruth, output_path: Path) -> Path:
    """Render a synthetic medication ground-truth record into a PDF document."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    story: list = []

    if record.template_type == "clinic_rx_pad":
        _render_clinic_rx_pad(record, story)
    elif record.template_type == "discharge_summary":
        _render_discharge_summary(record, story)
    elif record.template_type == "pharmacy_label":
        _render_pharmacy_label(record, story)
    elif record.template_type == "mar_order_sheet":
        _render_mar_order_sheet(record, story)
    else:
        _render_clinic_rx_pad(record, story)

    doc.build(story)
    return output_path
