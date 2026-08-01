# Plan: AWS Lambda Serverless Deployment

Enable the pipeline to run fully serverless on AWS Lambda with DynamoDB state,
API Gateway for webhooks, and EventBridge for scheduling. File upload endpoint
(`POST /api/sync/run`) is **out of scope** — serverless will support Drive
sources only.

## Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ Google Drive │────▶│ API Gateway      │────▶│ Lambda: webhook │
│ push notif.  │     │ /api/drive/      │     │ (Mode A)        │
└──────────────┘     └──────────────────┘     └────────┬────────┘
                                                       │
                                        ┌──────────────▼──────────────┐
                                        │  DynamoDB (state table)     │
                                        │  pending_edits queue        │
                                        └──────────────┬──────────────┘
                                                       │
                          ┌────────────────────────────┼────────────────────────────┐
                          │                            │                            │
              ┌───────────▼───────────┐   ┌────────────▼────────────┐  ┌───────────▼───────────┐
              │ EventBridge: cron     │   │ EventBridge: cron       │  │ Lambda: watch renew   │
              │ Lambda: sync (Mode B) │   │ Lambda: watch renewal   │  │ (Drive channels)      │
              └───────────┬───────────┘   └─────────────────────────┘  └───────────────────────┘
                          │
              ┌───────────▼───────────┐
              │ Bedrock (LLM)         │
              │ S3 (CEDICT, config)   │
              │ Secrets Manager       │
              │ (Drive OAuth token)   │
              └───────────────────────┘
```

## Existing Foundation

Already implemented and Lambda-ready:

- **Lambda handler stubs** in `src/anki_deck_generator/lambda_handlers/`:
  - `handler_sync.py` — Mode B scheduled sync
  - `handler_drive_webhook.py` — Drive webhook (API Gateway proxy)
  - `handler_watch_renewal.py` — EventBridge-scheduled channel renewal
- **DynamoDB state store** (`state/dynamo_store.py`) — fully implemented
- **DynamoDB table definition** (`state/dynamo_table.py`) — single-table with GSI
- **State store protocol** (`state/store.py`) — abstraction both stores implement
- **Store factory** (`state/__init__.py:get_store()`) — switches on `state_backend`
- **Core pipeline** is store-agnostic (`run_incremental_sync`)

## Work Items

### 1. Lambda Deployment Package (IaC template)

Create a SAM template (or CDK/CloudFormation equivalent) defining:

- **3 Lambda functions**: `SyncFunction`, `WebhookFunction`, `WatchRenewalFunction`
- **API Gateway** (HTTP API): `POST /api/drive/notifications` → `WebhookFunction`
- **DynamoDB table**: single-table with `card_by_key` GSI (use existing
  `dynamo_table_definition()`)
- **EventBridge rules**:
  - `SyncScheduleRule` — cron (e.g. hourly) → `SyncFunction`
  - `WatchRenewalRule` — cron (daily) → `WatchRenewalFunction`
- **IAM roles**: least-privilege per function (DynamoDB, Bedrock, S3, Secrets
  Manager, CloudWatch Logs)
- **Lambda Layers**: PyMuPDF + python-docx + langchain stack as separate layers
  to stay under 250MB unpacked limit

### 2. Mangum Adapter for FastAPI Webhook

- Add `mangum` as an optional dependency (`[serverless]` extra in
  `pyproject.toml`)
- Create `src/anki_deck_generator/lambda_handlers/handler_api_gateway.py`:
  ```python
  from mangum import Mangum
  from anki_deck_generator.web.app import create_app
  _app = create_app()
  handler = Mangum(_app, lifespan="off")
  ```
- This handles `POST /api/drive/notifications` through the existing FastAPI
  route, keeping one code path for both `serve` and Lambda

### 3. CEDICT Dictionary via S3 or Layer

CEDICT file (`cedict_ts.u8`, ~5MB) needs to be accessible in Lambda's `/tmp`.

Options:

- **Lambda Layer** (simpler): package CEDICT as a layer, mount at
  `/opt/cedict/cedict_ts.u8`
- **S3 download** (flexible): store in S3 bucket, download to `/tmp` at cold
  start

Update `Settings.cedict_path` to default to `/opt/cedict/cedict_ts.u8` when
running in Lambda (detect via `AWS_LAMBDA_FUNCTION_NAME` env var).

### 4. Source Set YAML in Serverless

YAML config currently lives on filesystem. For Lambda:

- Store in **SSM Parameter Store** (string parameter) or **S3** (single object)
- At Lambda init, download/resolve to `/tmp/sources.yaml`
- Set `ANKI_PIPELINE_SOURCE_SET_CONFIG=/tmp/sources.yaml` in Lambda env vars
- Alternatively, embed a small YAML directly in the Lambda deployment package if
  config is static

### 5. Google Drive OAuth Credentials

`google-drive-token.json` currently stored at
`~/.config/anki-notes-pipeline/`. For Lambda:

- Store in **AWS Secrets Manager** as a JSON secret
- At Lambda init, fetch secret and write to `/tmp/google-drive-token.json`
- Update `Settings` or source-set YAML to reference
  `/tmp/google-drive-token.json`

### 6. Environment Variable Configuration

Lambda functions need these env vars (set in IaC template):

| Variable | Value | Notes |
|----------|-------|-------|
| `ANKI_PIPELINE_STATE_BACKEND` | `dynamodb` | All functions |
| `ANKI_PIPELINE_DYNAMODB_TABLE_NAME` | `!Ref StateTable` | CloudFormation ref |
| `ANKI_PIPELINE_BEDROCK_MODEL_ID` | `us.meta.llama4-scout-17b-instruct-v1:0` | Or override |
| `ANKI_PIPELINE_SOURCE_SET_CONFIG` | `/tmp/sources.yaml` | Downloaded at init |
| `AWS_REGION` | `!Ref AWS::Region` | Auto |
| `GOOGLE_DRIVE_CREDENTIALS_FILE` | `/tmp/google-drive-token.json` | Secrets Manager |
| `DRIVE_WEBHOOK_URL` | `!Sub https://${ApiGateway}.../api/drive/notifications` | WatchRenewal only |

### 7. Sync Lambda Handler Enhancement

Current `handler_sync.py` uses `SqliteStateStore` directly. Update to:

- Use `get_store()` factory with `state_backend=dynamodb`
- Accept source set name from event or env var
- Support being triggered by EventBridge cron (no event body needed)
- Return proper sync report in response body

### 8. Watch Renewal Lambda Handler

Current `handler_watch_renewal.py` is mostly correct. Needs:

- `DRIVE_WEBHOOK_URL` from env var (API Gateway endpoint URL)
- Credentials from Secrets Manager → `/tmp`
- Use `get_store()` with DynamoDB backend

### 9. Function Configuration

| Function | Memory | Timeout | Purpose |
|----------|--------|---------|---------|
| `SyncFunction` | 2048 MB | 15 min | Mode B: process pending edits, run LLM pipeline |
| `WebhookFunction` | 512 MB | 30 sec | Mode A: receive Drive push, enqueue pending edits |
| `WatchRenewalFunction` | 256 MB | 60 sec | Renew expiring Drive watch channels |

### 10. Testing

- Add `tests/test_lambda_handlers.py` — unit tests with mocked boto3/moto
- Test DynamoDB-backed `run_incremental_sync` end-to-end with moto
- Test Mangum-wrapped FastAPI with `TestClient` for webhook route

### 11. Documentation

- Add `docs/serverless-deployment.md` with:
  - Prerequisites (AWS account, Bedrock model access, Drive API setup)
  - Deploy steps (`sam build && sam deploy`)
  - Post-deploy setup (Drive OAuth, watch channel registration)
  - Environment variable reference
  - Cost estimate

## Dependencies to Add

| Package | Extra | Purpose |
|---------|-------|---------|
| `mangum>=0.17` | `[serverless]` | FastAPI → Lambda adapter |
| `aws-lambda-powertools[all]>=2` | `[serverless]` | Structured logging, tracing, env var parsing |

## Out of Scope (Explicitly)

- `POST /api/sync/run` file upload endpoint (Drive sources only)
- Multi-tenant user isolation (single `user_id="default"`)
- VPC deployment (Lambda stays in public AWS service network)
- Custom domain / HTTPS certificate (uses API Gateway default URL)
