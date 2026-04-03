# Workflow

## 1. Ingest from Gmail
- Search inbox for emails with invoice-related subjects or PDF attachments.
- For each email: read full message body + attachment metadata.
- Extract invoice fields from the email body text (many senders include key fields there).
- If attachment content is accessible, parse it directly.
- Skip emails with no attachment and no invoice fields in the body.

## 2. Validate Each Invoice
- Vendor match against vendor master (exact / alias / fuzzy).
- PO number exists in PO register.
- Amount within 5% of PO total.
- Bank account matches vendor master.
- No duplicate invoice number for same vendor.
- Suspicious pattern check.

## 3. Persist
- `upsert_invoice` with extracted fields, flags, and status.
- `add_audit_log` with action `INGESTED`.
- Status: `READY_FOR_APPROVAL` (clean) or `FLAGGED` (any flag).

## 4. Alert (on request)
- `send slack alert of invoice #X` → Slack message with approve/reject reply instructions.
- `send email alert of invoice #X` → Email with approve/reject reply instructions.
- Both are text-based (no interactive buttons — backend required for that).
- Log `SLACK_ALERT_SENT` or `EMAIL_ALERT_SENT` to audit.

## 5. Human Decision (in Claude chat)
- `approve invoice #X` → status `APPROVED`, audit logged.
- `reject invoice #X with note` → status `REJECTED`, audit logged.
- Approving a FLAGGED invoice requires a mandatory override note.

## 6. Post to QBO (on request)
- `post invoice #X to QBO` or `post approved invoices to accounting`.
- Only APPROVED invoices are eligible.
- On success → `POSTED`. On failure → `POST_FAILED`.
- Log outcome to audit.

## 7. Report (on request)
- `get_report_summary` from storage.
- Share to Slack / email / Google Sheets on request.

## Status Flow
```
INGESTED → READY_FOR_APPROVAL → APPROVED → POSTED
         → FLAGGED            → APPROVED → POSTED  (override note required)
                              → REJECTED
```
