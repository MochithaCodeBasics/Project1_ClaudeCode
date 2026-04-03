# Project 1 - Claude Desktop Only (Skills + SQLite MCP Persistence)

This project has **no backend service**.  
Everything runs from Claude Desktop Code mode using:

- skills for operational rules/workflow
- a local SQLite MCP server for persistent storage
- connector apps (Gmail, Slack, QBO, Google Sheets) from Claude Desktop

## Architecture

1. Claude Desktop is the UI/orchestrator.
2. Skills define all business rules and required behavior.
3. SQLite MCP server stores invoices, vendor master, PO register, and audit logs.
4. External actions (Gmail/Slack/QBO/Sheets) are executed via Claude Desktop connectors.

## Folder Map

- `.claude/skills/invoice-agent-desktop/SKILL.md` - core rules/workflow
- `mcp/sqlite_store_server.py` - local SQLite MCP server
- `storage/schema.sql` - DB schema
- `scripts/init_storage.py` - initialize/reset local DB
- `.mcp.json` - MCP server registration
- `seed_data.json` - vendor/PO seed source

## One-Time Setup

### 1) Python deps for MCP server

```powershell
pip install mcp
```

### 2) Initialize local SQLite storage

```powershell
python scripts/init_storage.py
```

This creates:

- `storage/invoice_agent.db`

### 3) Load MCP server in Claude Desktop

`.mcp.json` is already included.  
Restart Claude Desktop after opening this folder so MCP tools load.

## Desktop-Only Execution Flow

1. Open Claude Desktop -> **Code**.
2. Select folder: `Project_1_ClaudeCode`.
3. Use skill instructions from `invoice-agent-desktop`.
4. Run workflow conversationally:
   - pull/process invoice emails
   - validate against vendor/PO rules
   - classify `READY_FOR_APPROVAL` or `FLAGGED`
   - approve/reject (single super-user)
   - post approved invoices to QBO
   - write audit rows
   - generate/share reports

## End-User Prompt Examples (Non-Technical)

- `Start my invoice workspace for today.`
- `Load my vendor and PO records.`
- `Get latest invoices from mail.`
- `Show invoices waiting for review in a table.`
- `Approve invoice 2 with note Verified.`
- `Reject invoice 3 with note Duplicate.`
- `Post approved invoices to accounting.`
- `Generate month-end summary for 2026-04.`
- `Share month-end summary to Slack and email.`

## Connector Integrations (Desktop side)

Use Claude Desktop connectors for:

- Gmail (inbox read + email send)
- Slack (alerts/notifications)
- QuickBooks (bill posting)
- Google Sheets (audit/report rows)

No backend integration code is required in this project.

Detailed setup guide:

- `.claude/skills/invoice-agent-desktop/references/integration-setup.md`

## Single User Policy

- One operator only: `finance_admin`
- Full rights for all steps (ingest, approval, posting, reporting)
- Authorization and guardrails are defined in the skill and enforced in workflow prompts
