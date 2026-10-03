"""Streamlit Clinical Document Reviewer UI.

Authority: PROJECT_SPEC.md §7.8, §21; DEC-016
Demonstration and reviewer interface for verifying multimodal clinical document conversion to FHIR R4B.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Optional

import pandas as pd
import streamlit as st

from clindoc.cli import ProcessResult, process_document
from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict
from clindoc.ui.render import (
    format_confidence,
    get_stage_badge_html,
    get_verdict_badge_html,
    render_document_page_with_bboxes,
)


def _create_sample_lab_document() -> DocumentText:
    """Create a sample born-digital lab report DocumentText for quick UI demonstration."""
    tokens = [
        Token(text="METROPOLITAN", bbox=BBoxDict(x0=50, y0=40, x1=160, y1=55), line_id="L0", confidence=0.99),
        Token(text="CLINICAL", bbox=BBoxDict(x0=165, y0=40, x1=240, y1=55), line_id="L0", confidence=0.99),
        Token(text="LABORATORY", bbox=BBoxDict(x0=245, y0=40, x1=350, y1=55), line_id="L0", confidence=0.99),
        # Patient Header
        Token(text="Patient:", bbox=BBoxDict(x0=50, y0=70, x1=100, y1=85), line_id="L1", confidence=0.98),
        Token(text="John", bbox=BBoxDict(x0=105, y0=70, x1=140, y1=85), line_id="L1", confidence=0.98),
        Token(text="Doe", bbox=BBoxDict(x0=145, y0=70, x1=175, y1=85), line_id="L1", confidence=0.98),
        Token(text="MRN:", bbox=BBoxDict(x0=300, y0=70, x1=340, y1=85), line_id="L1", confidence=0.97),
        Token(text="984512", bbox=BBoxDict(x0=345, y0=70, x1=395, y1=85), line_id="L1", confidence=0.96),
        # Table Header
        Token(text="Test", bbox=BBoxDict(x0=50, y0=120, x1=85, y1=135), line_id="L2", confidence=0.99),
        Token(text="Name", bbox=BBoxDict(x0=90, y0=120, x1=130, y1=135), line_id="L2", confidence=0.99),
        Token(text="Result", bbox=BBoxDict(x0=180, y0=120, x1=225, y1=135), line_id="L2", confidence=0.99),
        Token(text="Units", bbox=BBoxDict(x0=260, y0=120, x1=300, y1=135), line_id="L2", confidence=0.99),
        Token(text="Reference", bbox=BBoxDict(x0=340, y0=120, x1=405, y1=135), line_id="L2", confidence=0.99),
        Token(text="Interval", bbox=BBoxDict(x0=410, y0=120, x1=460, y1=135), line_id="L2", confidence=0.99),
        Token(text="Flag", bbox=BBoxDict(x0=500, y0=120, x1=530, y1=135), line_id="L2", confidence=0.99),
        # Row 1: Glucose (supported)
        Token(text="Glucose,", bbox=BBoxDict(x0=50, y0=150, x1=105, y1=165), line_id="L3", confidence=0.95),
        Token(text="Serum", bbox=BBoxDict(x0=110, y0=150, x1=150, y1=165), line_id="L3", confidence=0.94),
        Token(text="115", bbox=BBoxDict(x0=180, y0=150, x1=205, y1=165), line_id="L3", confidence=0.97),
        Token(text="mg/dL", bbox=BBoxDict(x0=260, y0=150, x1=300, y1=165), line_id="L3", confidence=0.96),
        Token(text="70-99", bbox=BBoxDict(x0=340, y0=150, x1=380, y1=165), line_id="L3", confidence=0.95),
        Token(text="High", bbox=BBoxDict(x0=500, y0=150, x1=530, y1=165), line_id="L3", confidence=0.92),
        # Row 2: Creatinine (supported)
        Token(text="Creatinine", bbox=BBoxDict(x0=50, y0=180, x1=120, y1=195), line_id="L4", confidence=0.95),
        Token(text="1.1", bbox=BBoxDict(x0=180, y0=180, x1=200, y1=195), line_id="L4", confidence=0.96),
        Token(text="mg/dL", bbox=BBoxDict(x0=260, y0=180, x1=300, y1=195), line_id="L4", confidence=0.95),
        Token(text="0.7-1.3", bbox=BBoxDict(x0=340, y0=180, x1=390, y1=195), line_id="L4", confidence=0.94),
        Token(text="Normal", bbox=BBoxDict(x0=500, y0=180, x1=545, y1=195), line_id="L4", confidence=0.93),
        # Row 3: Potassium (supported)
        Token(text="Potassium", bbox=BBoxDict(x0=50, y0=210, x1=115, y1=225), line_id="L5", confidence=0.96),
        Token(text="4.2", bbox=BBoxDict(x0=180, y0=210, x1=200, y1=225), line_id="L5", confidence=0.97),
        Token(text="mmol/L", bbox=BBoxDict(x0=260, y0=210, x1=310, y1=225), line_id="L5", confidence=0.95),
        Token(text="3.5-5.0", bbox=BBoxDict(x0=340, y0=210, x1=390, y1=225), line_id="L5", confidence=0.94),
        Token(text="Normal", bbox=BBoxDict(x0=500, y0=210, x1=545, y1=225), line_id="L5", confidence=0.93),
    ]
    page = DocumentPage(page_number=1, width_px=612, height_px=792, tokens=tokens)
    return DocumentText(document_id="SAMPLE_LAB_REPORT_01", source_type="born_digital", pages=[page])


def main() -> None:
    st.set_page_config(
        page_title="Clindoc2FHIR Reviewer UI",
        page_icon="🏥",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # ─── HEADER ─────────────────────────────────────────────────────────────
    st.markdown(
        """
        <div style="display: flex; align-items: center; justify-content: space-between;
                    border-bottom: 2px solid #e9ecef; padding-bottom: 12px; margin-bottom: 20px;">
            <div>
                <h1 style="margin: 0; padding: 0; font-size: 2.1rem; color: #1e293b;">
                    🏥 Clindoc2FHIR Reviewer
                </h1>
                <p style="margin: 4px 0 0 0; color: #64748b; font-size: 1rem;">
                    Multimodal Clinical Document-to-FHIR Verification & Audit Trail Interface (§7.8, §21)
                </p>
            </div>
            <div style="text-align: right;">
                <span style="background-color: #e0f2fe; color: #0369a1; padding: 6px 12px;
                             border-radius: 6px; font-weight: 600; font-size: 0.85rem;">
                    FHIR R4B • ModernBERT Cascade • DEC-016
                </span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ─── SIDEBAR: INPUT & CONTROLS ──────────────────────────────────────────
    with st.sidebar:
        st.header("📥 Document Input")

        uploaded_file = st.file_uploader(
            "Upload Clinical Document",
            type=["pdf", "png", "jpg", "jpeg", "tiff", "tif", "json"],
            help="Upload a clinical PDF, scanned image, or DocumentText JSON.",
        )

        use_sample = st.button("📄 Load Demo Lab Report", use_container_width=True)

        st.markdown("---")
        st.subheader("⚙️ Verification Filter")
        verdict_filter = st.selectbox(
            "Filter Claims by Verdict",
            options=["ALL", "SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE"],
            index=0,
        )
        escalated_only = st.checkbox("Show Only Escalated to Stage C", value=False)

        st.markdown("---")
        st.subheader("🎨 Bounding Box Legend")
        st.markdown(
            """
            - <span style="color: #28a745; font-weight: bold;">■ Green:</span> **SUPPORTED**
            - <span style="color: #dc3545; font-weight: bold;">■ Red:</span> **DISCREPANCY**
            - <span style="color: #fd7e14; font-weight: bold;">■ Orange:</span> **INSUFFICIENT_EVIDENCE**
            """,
            unsafe_allow_html=True,
        )

        st.markdown("---")
        st.info(
            "ℹ️ **Confidence Metrics Distinction**\n\n"
            "- **OCR Confidence**: Token optical extraction fidelity (null for born-digital).\n"
            "- **Verification Confidence**: Calibrated semantic agreement probability from the verification cascade."
        )

    # ─── EXECUTE PIPELINE ───────────────────────────────────────────────────
    doc_path: Optional[Path] = None
    temp_dir_obj = tempfile.TemporaryDirectory()
    temp_dir = Path(temp_dir_obj.name)

    if uploaded_file is not None:
        doc_path = temp_dir / uploaded_file.name
        with open(doc_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
    elif use_sample or "sample_active" in st.session_state:
        st.session_state["sample_active"] = True
        sample_doc = _create_sample_lab_document()
        doc_path = temp_dir / "SAMPLE_LAB_REPORT_01.json"
        sample_doc.save(doc_path)

    if doc_path is None:
        st.info(
            "👋 Welcome! Please upload a clinical document in the sidebar or click "
            "**'Load Demo Lab Report'** to start the interactive review demonstration."
        )
        return

    # Run the unified pipeline
    output_dir = temp_dir / "pipeline_output"
    try:
        with st.spinner("⚡ Running multimodal verification pipeline (Ingestion → Extraction → Normalization → Cascade → FHIR → Audit)..."):
            res: ProcessResult = process_document(
                input_file=doc_path,
                output_dir=output_dir,
            )
    except Exception as err:
        st.error(f"❌ Pipeline Execution Error: {err}")
        return

    # ─── TOP KPI SUMMARY CARDS ──────────────────────────────────────────────
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Total Claims", res.n_claims)
    with col2:
        pct = (res.n_supported / res.n_claims * 100.0) if res.n_claims else 0.0
        st.metric("Supported", f"{res.n_supported} ({pct:.0f}%)")
    with col3:
        st.metric("Discrepancies", res.n_discrepancy)
    with col4:
        st.metric("Insufficient Evidence", res.n_insufficient_evidence)
    with col5:
        st.metric("Escalated to Stage C", res.n_escalated_to_llm)

    st.markdown("---")

    # Match evidence and verifications to claims
    audit_dict = res.audit_report.to_dict()
    claims_data = audit_dict.get("claims", [])
    evidence_lookup = {e["evidence_id"]: e for e in audit_dict.get("evidence", [])}
    verifications_lookup = {v["claim_id"]: v for v in audit_dict.get("verifications", [])}

    # Build annotations for bounding box visualization
    evidence_annotations: list[tuple[Evidence, str, str]] = []
    for c in res.audit_report.claims:
        vr = next((v for v in res.audit_report.verifications if v.claim_id == c.claim_id), None)
        ev = next((e for e in res.audit_report.evidence if e.evidence_id == c.evidence_id), None)
        if vr and ev:
            evidence_annotations.append((ev, vr.label, c.field_type))

    # ─── SPLIT VIEW: SOURCE DOCUMENT VS EXTRACTED CLAIMS ────────────────────
    view_col1, view_col2 = st.columns([1.1, 1.4])

    with view_col1:
        st.subheader("📄 Grounding Visualizer")
        st.caption("Visual inspection of source document with verified evidence bounding boxes.")

        # Determine available pages
        max_page = max([e.page_number for e in res.audit_report.evidence], default=1)
        selected_page = 1
        if max_page > 1:
            selected_page = st.selectbox(
                "Select Document Page",
                options=list(range(1, max_page + 1)),
                index=0,
            )

        rendered_img = render_document_page_with_bboxes(
            file_path=doc_path,
            page_number=selected_page,
            evidence_annotations=evidence_annotations,
        )
        st.image(
            rendered_img,
            caption=f"Source Document (Page {selected_page}) with Overlaid Grounding Bounding Boxes",
            use_container_width=True,
        )

    with view_col2:
        st.subheader("🔬 Extracted Claims & Verification Verdicts")
        st.caption("Per-claim atomic verification verdicts, calibrated confidence, and provenance.")

        # Filter claims
        filtered_claims: list[dict] = []
        for c in claims_data:
            vr = verifications_lookup.get(c["claim_id"], {})
            label = vr.get("label", "UNKNOWN")
            is_escalated = vr.get("escalated", False) or vr.get("stage_reached") == "C"

            if verdict_filter != "ALL" and label != verdict_filter:
                continue
            if escalated_only and not is_escalated:
                continue

            filtered_claims.append(c)

        if not filtered_claims:
            st.warning(f"No claims match the selected filter (Verdict: {verdict_filter}, Escalated: {escalated_only}).")
        else:
            for idx, claim in enumerate(filtered_claims):
                vr = verifications_lookup.get(claim["claim_id"], {})
                ev = evidence_lookup.get(claim["evidence_id"], {})

                verdict_label = vr.get("label", "UNKNOWN")
                cal_conf = vr.get("confidence")
                stage_reached = vr.get("stage_reached", "A")
                contradiction_type = vr.get("contradiction_type")
                rationale = vr.get("rationale")
                ocr_conf = ev.get("ocr_confidence")

                with st.container(border=True):
                    # Top Row: Field type, badges, and verdict
                    h_col1, h_col2 = st.columns([1.5, 1])
                    with h_col1:
                        st.markdown(
                            f"**`{claim.get('field_type')}`**: "
                            f"<span style='font-size: 1.15rem; font-weight: 700; color: #0f172a;'>"
                            f"{claim.get('value')}</span> "
                            f"<span style='color: #64748b;'>{claim.get('unit_raw') or ''}</span>",
                            unsafe_allow_html=True,
                        )
                    with h_col2:
                        st.markdown(
                            f"<div style='text-align: right;'>"
                            f"{get_verdict_badge_html(verdict_label)} &nbsp; "
                            f"{get_stage_badge_html(stage_reached)}"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                    # Middle Row: Terminology normalization and confidence breakdown
                    m_col1, m_col2, m_col3 = st.columns(3)
                    with m_col1:
                        code = claim.get("normalized_code")
                        system = claim.get("code_system")
                        code_conf = claim.get("code_confidence", 0.0)
                        if code:
                            st.markdown(f"**Code:** `{code}` ({system})")
                            st.caption(f"Code Conf: {code_conf * 100.0:.1f}%")
                        else:
                            st.markdown("**Code:** _Unmapped / Below threshold_")
                            st.caption("Code Conf: N/A")

                    with m_col2:
                        st.markdown(f"**Verification Conf:** `{format_confidence(cal_conf)}`")
                        st.caption("Calibrated cascade score")

                    with m_col3:
                        if ocr_conf is not None:
                            st.markdown(f"**OCR Conf:** `{format_confidence(ocr_conf)}`")
                            st.caption("Optical engine score")
                        else:
                            st.markdown("**OCR Conf:** `Born-digital`")
                            st.caption("Perfect digital fidelity")

                    # Discrepancy taxonomy warning
                    if verdict_label == "DISCREPANCY" and contradiction_type:
                        st.error(f"⚠️ **Contradiction Taxonomy:** `{contradiction_type}`")

                    # Grounding Evidence span
                    ev_text = ev.get("text", "")
                    page_num = ev.get("page_number", 1)
                    st.markdown(
                        f"""
                        <div style="background-color: #f8fafc; border-left: 3px solid #cbd5e1;
                                    padding: 6px 12px; margin-top: 6px; font-size: 0.88rem; color: #334155;">
                            <strong>Source Evidence (Page {page_num}):</strong> <em>"{ev_text}"</em>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    # Stage C Rationale if present
                    if rationale:
                        st.markdown(
                            f"""
                            <div style="background-color: #f5f3ff; border-left: 3px solid #8b5cf6;
                                        padding: 8px 12px; margin-top: 6px; font-size: 0.88rem; color: #4c1d95;">
                                <strong>🧠 Stage C LLM Judge Rationale:</strong> {rationale}
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

    st.markdown("---")

    # ─── EXPORT & RAW ARTIFACT INSPECTOR ────────────────────────────────────
    st.subheader("📦 Generated Artifacts & Export")

    d_col1, d_col2 = st.columns(2)
    with d_col1:
        with open(res.fhir_bundle_path, "r", encoding="utf-8") as f:
            fhir_bundle_str = f.read()
        st.download_button(
            label="⬇️ Download FHIR R4B Bundle (JSON)",
            data=fhir_bundle_str,
            file_name=f"{res.document_id}_fhir_bundle.json",
            mime="application/json",
            use_container_width=True,
        )

    with d_col2:
        with open(res.audit_report_path, "r", encoding="utf-8") as f:
            audit_report_str = f.read()
        st.download_button(
            label="⬇️ Download Machine-Readable Audit Report (JSON)",
            data=audit_report_str,
            file_name=f"{res.document_id}_audit_report.json",
            mime="application/json",
            use_container_width=True,
        )

    with st.expander("📄 View Raw FHIR R4B Bundle (JSON)"):
        st.json(json.loads(fhir_bundle_str))

    with st.expander("🔍 View Machine-Readable Audit Report (JSON)"):
        st.json(json.loads(audit_report_str))


if __name__ == "__main__":
    main()
