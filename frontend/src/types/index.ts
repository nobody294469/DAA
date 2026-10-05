/**
 * Clindoc2FHIR Frontend Type Definitions
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §1.2, §4.3, §7.2, §7.3, §9.3
 *   - PRODUCTIZATION_DECISIONS.md PD-009, PD-010, PD-013, PD-015
 */

export type CanonicalRole =
  | 'clinical_reviewer'
  | 'approver'
  | 'document_operator'
  | 'system_admin'
  | 'auditor'
  | 'integration_service';

export interface AuthenticatedUser {
  userId: string;
  username: string;
  fullName: string;
  email: string;
  roles: CanonicalRole[];
}

export type DocumentStatus =
  | 'INGESTED'
  | 'PROCESSING'
  | 'PROCESSED'
  | 'PENDING_REVIEW'
  | 'IN_REVIEW'
  | 'APPROVED'
  | 'REJECTED'
  | 'EXPORTED'
  | 'FAILED';

export type IdentityVerificationStatus =
  | 'UNVERIFIED'
  | 'CONFIRMED'
  | 'MISMATCH'
  | 'MISMATCH_ACCEPTED'
  | 'NOT_APPLICABLE';

export type VerificationLabel =
  | 'SUPPORTED'
  | 'DISCREPANCY'
  | 'INSUFFICIENT_EVIDENCE';

export type VerificationStage = 'A' | 'B' | 'C';

export type ClaimReviewState =
  | 'PENDING'
  | 'ACCEPTED'
  | 'REJECTED'
  | 'EDITED'
  | 'ESCALATED';

export interface BoundingBoxCoords {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export type BoundingBox = BoundingBoxCoords | [number, number, number, number];

export interface EvidenceSpan {
  evidenceId: string;
  documentId: string;
  pageNumber: number;
  text: string;
  charStart?: number;
  charEnd?: number;
  bbox?: BoundingBox;
  ocrConfidence?: number;
}

export interface DocumentClaimGrounding {
  page_number: number;
  bounding_box?: BoundingBox | null;
  ocr_confidence?: number | null;
  text_snippet?: string | null;
}

export interface DocumentClaimVerification {
  label: VerificationLabel | string;
  stage?: string | null;
  calibrated_confidence?: number | null;
  contradiction_type?: string | null;
  rationale?: string | null;
  second_check_performed?: boolean | null;
  is_fallback?: boolean | null;
  config_status?: string | null;
}

export interface ClaimActionPayload {
  action: 'ACCEPT' | 'EDIT' | 'REJECT' | 'ESCALATE' | 'REOPEN';
  expected_version: number;
  reason_code?: string;
  notes?: string;
  new_value?: string;
  new_normalized_code?: string;
  new_code_system?: string;
  page_text?: string;
}

export interface ClaimActionResult {
  action: string;
  claim_id: string;
  row_id: string;
  version: number;
  review_state: string;
  origin: string;
  previous_version?: number;
  supersedes_claim_id?: string | null;
  verification_label?: string;
  triage_category?: string;
  triage_priority_rank?: number;
}

export interface DocumentApprovalPayload {
  identity_notes?: string;
}

export interface DocumentApprovalResponse {
  approval_id: string;
  document_id: string;
  status: string;
  approved_by: string;
  approver_id: string;
  approved_at?: string | null;
  is_export_eligible: boolean;
  policy_version: string;
}

export interface DocumentClaimTriage {
  category: string;
  risk_tier: string;
  priority_rank: number;
  primary_reason?: string | null;
  is_bulk_confirmable?: boolean;
}

export interface DocumentClaimItem {
  id: string;
  claim_id: string;
  document_id: string;
  group_id?: string | null;
  field_type: string;
  value: string;
  normalized_code?: string | null;
  code_system?: string | null;
  version: number;
  supersedes_claim_id?: string | null;
  origin: string;
  review_state: ClaimReviewState | string;
  created_by?: string | null;
  created_at?: string | null;
  evidence_id?: string | null;
  grounding?: DocumentClaimGrounding | null;
  verification?: DocumentClaimVerification | null;
  triage?: DocumentClaimTriage | null;
}

export interface DocumentClaimsResponse {
  document_id: string;
  total: number;
  claims: DocumentClaimItem[];
}

export interface NormalizedConcept {
  code: string;
  system: string;
  displayName: string;
  confidence?: number;
}

export interface VerificationResult {
  label: VerificationLabel;
  calibratedConfidence: number;
  stage: VerificationStage;
  contradictionType?: string;
  rationale?: string;
  isFallback: boolean;
  providerKind: string;
  secondCheckPerformed?: boolean;
}

export interface ClaimItem {
  id: string;
  documentId: string;
  groupId: string;
  fieldType: string;
  rawText: string;
  parsedValue?: string | number | null;
  unit?: string | null;
  evidence: EvidenceSpan;
  normalization?: NormalizedConcept;
  verification: VerificationResult;
  reviewState: ClaimReviewState;
  version: number;
  origin: 'pipeline' | 'reviewer';
  riskFlags: string[];
}

export interface ReviewQueueClaimItem {
  claim_id: string;
  field_type: string;
  category: string;
  risk_tier: string;
  priority_rank: number;
  review_state: string;
}

export interface ReviewQueueItem {
  document_id: string;
  original_filename: string;
  status: DocumentStatus;
  uploaded_at: string | null;
  patient_context_id?: string | null;
  identity_verification_status: IdentityVerificationStatus;
  total_claims: number;
  auto_processed_count: number;
  human_review_required_count: number;
  discrepancy_count: number;
  highest_risk_rank: number;
  can_bulk_confirm_agreed: boolean;
  claims?: ReviewQueueClaimItem[];
}

export interface ReviewQueueResponse {
  total: number;
  limit: number;
  offset: number;
  items: ReviewQueueItem[];
}

export interface PatientDemographics {
  name?: string | null;
  mrn?: string | null;
  dob?: string | null;
  gender?: string | null;
}

export interface IdentityConfirmationPayload {
  action: 'CONFIRMED' | 'MISMATCH_ACCEPTED';
  reason_code?: string;
  notes?: string;
  context_demographics?: PatientDemographics;
  extracted_demographics?: PatientDemographics;
}

export interface IdentityConfirmationResponse {
  document_id: string;
  identity_verification_status: IdentityVerificationStatus;
  identity_confirmed_by?: string | null;
  identity_confirmed_at?: string | null;
}

export interface DocumentReviewSummary {
  document_id: string;
  original_filename: string;
  status: DocumentStatus;
  patient_context_id?: string | null;
  identity_verification_status: IdentityVerificationStatus;
  identity_confirmed_by?: string | null;
  identity_confirmed_at?: string | null;
  total_claims: number;
  auto_processed_count: number;
  human_review_required_count: number;
  discrepancy_count: number;
  can_bulk_confirm_agreed: boolean;
  is_approved: boolean;
  is_export_eligible: boolean;
}

export interface ApiErrorDetail {
  detail: string;
  status?: number;
  correlationId?: string;
}

