---
name: invoice-agent-desktop
description: Operate a backend-free invoice agent in Claude Desktop using connector apps for Gmail/Slack/QBO/Sheets and local SQLite MCP for persistent state, audit logs, vendor master, and purchase orders.
---

# Invoice Agent Desktop

Run this skill whenever executing the invoice workflow in Claude Desktop without a backend.

## Non-Technical Mode

Treat end users as non-technical. Never require MCP/tool/database language in user prompts.

- Do not ask users to run `init_storage`, `seed_master_data`, or other tool names.
- Interpret plain-English requests and execute MCP + connector steps internally.
- Reply in business language (invoice, approval, posting, report), not system language.

## Mandatory Rules

1. Use MCP SQLite as source of truth for state.
2. Persist every decision/action to `audit_logs`.
3. Enforce statuses in order:
   - `INGESTED` — just pulled from email, not yet validated
   - `FLAGGED` — validation failed, needs human review
   - `READY_FOR_APPROVAL` — validation passed, awaiting approval decision
   - `APPROVED` — explicitly approved by operator in chat
   - `REJECTED` — explicitly rejected by operator in chat
   - `POSTED` — successfully sent to QBO
   - `POST_FAILED` — QBO post attempted but failed
4. Use one operator identity: `finance_admin`.
5. Never mark an invoice APPROVED or POSTED without explicit operator confirmation in chat.
6. Never skip a status — INGESTED must precede READY_FOR_APPROVAL, APPROVED must precede POSTED.

## Status Transition Rules

```
INGESTED ──(validation pass)──► READY_FOR_APPROVAL ──(chat: approve)──► APPROVED ──(chat: post)──► POSTED
         ──(validation fail)──► FLAGGED             ──(chat: reject)──► REJECTED
                                                     ──(chat: approve flagged with note)──► APPROVED
```

- Approval in chat moves status to `APPROVED`.
- Posting to QBO from chat moves status to `POSTED` or `POST_FAILED`.
- Rejection in chat moves status to `REJECTED`.
- A `FLAGGED` invoice can be approved with an explicit override note, but the note is mandatory.

## PDF Extraction on Ingest

When pulling invoices from Gmail:

1. Use `gmail_read_message` to get the full message including attachment metadata.
2. For each attachment with a PDF mimeType:
   - Inspect filename for clues (vendor name, invoice number patterns).
   - Read the email body — senders often include key invoice fields (invoice #, amount, PO #, due date) in the body text.
   - Extract all structured fields visible in the email body or snippet: `invoice_number`, `vendor_name`, `po_number`, `subtotal`, `tax`, `total`, `due_date`, `bank_account`.
   - If a field cannot be extracted from body/snippet, mark it as UNCONFIRMED in notes.
3. If the Gmail MCP returns attachment content (base64 or text), parse it directly.
4. If attachment content is not accessible, record `notes: "PDF content not readable via connector — fields extracted from email body only"` and flag `AMOUNT_UNCONFIRMED` if amounts could not be verified.
5. Cross-reference every extracted field against vendor master and PO register before setting status.
6. An email with no attachment and no invoice fields in the body is NOT an invoice — skip it with a log entry.

## Validation Rules

1. Vendor name must match vendor master (exact, alias, or fuzzy match).
2. PO number must exist in PO register for normal path.
3. Amount mismatch threshold: 5% of PO total. Flag `AMOUNT_MISMATCH` if exceeded.
4. If bank account on invoice differs from vendor master, flag `BANK_ACCOUNT_CHANGED`.
5. Flag `UNKNOWN_VENDOR` if vendor not in master.
6. Flag `PO_NOT_FOUND` if PO missing or unmatched.
7. Flag `DUPLICATE` if same invoice number already exists in storage for the same vendor.
8. Flag `SUSPICIOUS_PATTERN` for: single round-amount line, no line-item detail, "due on receipt" with unknown vendor, or urgency pressure language.
9. Any flag → status `FLAGGED`. No flags → status `READY_FOR_APPROVAL`.

## Slack Alert Flow

When prompted `send slack alert of invoice #X` or `notify team about invoice #X`:

1. Load invoice #X from storage.
2. Find the finance/AP Slack channel (search for channels named `#finance`, `#ap`, `#invoices`, or ask user once and remember).
3. Send a Slack message in this exact format:

```
*Invoice Alert — Action Required*

*Invoice #:* <invoice_number>
*Vendor:* <vendor_name>
*Amount:* <total> <currency>
*PO:* <po_number>
*Status:* <status>
*Flags:* <flags or "None">

To approve: reply in this channel with: approve invoice <id>
To reject: reply in this channel with: reject invoice <id> — <reason>

Then confirm your decision back in Claude to update the record.
```

4. Log the alert to audit_logs with action `SLACK_ALERT_SENT`.
5. Confirm to the user in chat that the alert was sent, and remind them to return to Claude after the team responds to confirm the decision.

## Email Alert Flow

When prompted `send email alert of invoice #X` or `email the team about invoice #X`:

1. Load invoice #X from storage.
2. Identify the recipient (ask user once if not previously set).
3. Create and send a Gmail draft or direct email in this format:

**Subject:** `Invoice Alert: <invoice_number> from <vendor_name> — <status>`

**Body:**
```
Hi,

The following invoice requires your review:

Invoice #:   <invoice_number>
Vendor:      <vendor_name>
Amount:      <total> <currency>
PO:          <po_number>
Status:      <status>
Flags:       <flags or "None">

To approve: reply to this email with: approve invoice <id>
To reject:  reply to this email with: reject invoice <id> — <reason>

Then confirm your decision in Claude to update the record.
```

4. Log the alert to audit_logs with action `EMAIL_ALERT_SENT`.
5. Confirm to user in chat that the email was sent.

## Approval Flow (Chat)

When operator says `approve invoice #X` (optionally with a note):

1. If status is `FLAGGED`, a note is mandatory — ask for it if not provided.
2. Transition status to `APPROVED`.
3. Log to audit_logs: action `APPROVED`, actor `finance_admin`, details include note if provided.
4. Confirm in chat: "Invoice #X (vendor, amount) has been approved. Say 'post invoice #X to QBO' when ready to send to accounting."

## Rejection Flow (Chat)

When operator says `reject invoice #X` with a reason:

1. Transition status to `REJECTED`.
2. Log to audit_logs: action `REJECTED`, actor `finance_admin`, details include reason.
3. Confirm in chat: "Invoice #X has been rejected."
4. If a Slack alert was previously sent for this invoice, send a follow-up Slack message noting the rejection (only if user requests notification).

## QBO Posting Flow (Chat)

When operator says `post invoice #X to QBO` or `post approved invoices to accounting`:

1. Only post invoices with status `APPROVED`.
2. For each invoice, call the QBO connector to create a bill.
3. On success: transition to `POSTED`, log `POSTED` to audit_logs.
4. On failure: transition to `POST_FAILED`, log `POST_FAILED` with error details.
5. Report results to user in a summary table.

## Plain-English Intent Mapping

| What user says | What to do |
|---|---|
| `Start my invoice workspace for today` | init_storage + seed master data if missing, confirm ready |
| `Load my vendor and PO records` | seed/refresh vendor + PO master tables |
| `Get latest invoices from mail` / `Ingest from mail` | Pull Gmail, extract fields from body + attachment metadata, validate, persist |
| `Show invoices waiting for review` | List FLAGGED + READY_FOR_APPROVAL in a table |
| `Approve invoice <id>` | Transition to APPROVED, write audit |
| `Reject invoice <id> with note ...` | Transition to REJECTED, write audit |
| `Post invoice <id> to QBO` / `Post approved invoices` | Post to QBO, mark POSTED or POST_FAILED |
| `Send slack alert of invoice <id>` | Send Slack message with invoice details + approve/reject instructions |
| `Send email alert of invoice <id>` | Send email with invoice details + approve/reject instructions |
| `Share month-end summary` | Compute report, send Slack + email, append sheet audit row |

## Storage Tools

- `init_storage`
- `seed_master_data`
- `upsert_invoice`
- `list_invoices`
- `transition_invoice_status`
- `add_audit_log`
- `get_report_summary`

## References

- `references/workflow.md`
- `references/prompt-pack.md`
- `references/integration-setup.md`
