# Serverless Lambda Deployment

Deploy the Mandarin Anki Deck Creator pipeline to AWS Lambda with DynamoDB state,
API Gateway webhooks, and EventBridge scheduling.

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

## Prerequisites

1. **AWS Account** with permissions for:
   - Lambda, API Gateway, DynamoDB, EventBridge
   - S3, Secrets Manager, IAM, CloudFormation
   - Bedrock (for LLM inference)

2. **AWS CLI** configured with SSO or access keys:
   ```bash
   aws sso login --profile <profile>
   ```

3. **SAM CLI** installed:
   ```bash
   # macOS
   brew install aws-sam-cli
   
   # Linux
   pip install aws-sam-cli
   ```

4. **Bedrock Model Access** — enable Llama 4 Scout in AWS Console → Bedrock → Model access

5. **Google Drive OAuth Token** — generate via:
   ```bash
   anki-notes-pipeline auth google-drive --client-secrets client_secret.json
   ```

## Deployment

### 1. Build and Deploy

```bash
./scripts/deploy-lambda.sh
```

Or manually:

```bash
sam build --template-file template.yaml --use-container
sam deploy \
    --stack-name anki-pipeline-serverless \
    --s3-bucket anki-pipeline-config-<account-id> \
    --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
    --region us-east-1
```

### 2. Upload Resources

**CEDICT Dictionary:**
```bash
aws s3 cp cedict_ts.u8 s3://anki-pipeline-config-<account-id>/cedict_ts.u8
```

**Source Config YAML:**
```bash
aws s3 cp sources.yaml s3://anki-pipeline-config-<account-id>/sources.yaml
```

**Drive OAuth Token:**
```bash
aws secretsmanager put-secret-value \
    --secret-id anki-pipeline/drive-credentials \
    --secret-string file://~/.config/anki-notes-pipeline/google-drive-token.json
```

### 3. Register Drive Watch Channel

After deployment, register your Drive folder with the webhook URL:

```bash
WEBHOOK_URL=$(aws cloudformation describe-stacks \
    --stack-name anki-pipeline-serverless \
    --query "Stacks[0].Outputs[?OutputKey=='WebhookUrl'].OutputValue" \
    --output text)

anki-notes-pipeline drive watch register \
    --folder-id <YOUR_FOLDER_ID> \
    --webhook-url "$WEBHOOK_URL"
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ANKI_PIPELINE_STATE_BACKEND` | State backend type | `dynamodb` |
| `ANKI_PIPELINE_DYNAMODB_TABLE_NAME` | DynamoDB table name | `anki-pipeline-state` |
| `ANKI_PIPELINE_BEDROCK_MODEL_ID` | Bedrock model ID | `us.meta.llama4-scout-17b-instruct-v1:0` |
| `ANKI_PIPELINE_SOURCE_SET_CONFIG` | Path to source config YAML | `/tmp/sources.yaml` |
| `ANKI_PIPELINE_CEDICT_PATH` | Path to CEDICT dictionary | `/opt/cedict/cedict_ts.u8` |
| `GOOGLE_DRIVE_CREDENTIALS_FILE` | Path to Drive OAuth token | `/tmp/google-drive-token.json` |
| `DRIVE_CREDENTIALS_SECRET_ARN` | Secrets Manager ARN for Drive token | (set by SAM) |
| `SOURCE_CONFIG_BUCKET` | S3 bucket for config files | (set by SAM) |
| `CEDICT_BUCKET` | S3 bucket for CEDICT | (set by SAM) |

## Lambda Functions

| Function | Purpose | Timeout | Memory |
|----------|---------|---------|--------|
| `SyncFunction` | Mode B: Process pending edits, run LLM pipeline | 15 min | 2048 MB |
| `WebhookFunction` | Mode A: Receive Drive push notifications | 30 sec | 512 MB |
| `WatchRenewalFunction` | Renew expiring Drive watch channels | 60 sec | 256 MB |

## Schedules

- **Sync**: Runs hourly via EventBridge (`rate(1 hour)`)
- **Watch Renewal**: Runs daily via EventBridge (`rate(1 day)`)

## Monitoring

**CloudWatch Logs:**
```bash
aws logs tail /aws/lambda/anki-pipeline-serverless-SyncFunction --follow
aws logs tail /aws/lambda/anki-pipeline-serverless-WebhookFunction --follow
```

**DynamoDB Table:**
```bash
aws dynamodb scan --table-name anki-pipeline-state --select COUNT
```

## Cleanup

```bash
sam delete --stack-name anki-pipeline-serverless
```

## Troubleshooting

### Lambda Timeout
Increase timeout in `template.yaml` under `Globals.Function.Timeout` or per-function.

### Memory Errors
Increase memory in `template.yaml` under `Globals.Function.MemorySize` or per-function.

### Bedrock Throttling
Add reserved concurrency to `SyncFunction` in `template.yaml`:
```yaml
ReservedConcurrentExecutions: 5
```

### CEDICT Not Found
Ensure CEDICT is uploaded to S3 or packaged in the Lambda Layer.

### Drive OAuth Expired
Re-run `anki-notes-pipeline auth google-drive` and update Secrets Manager.

## Cost Estimate

Monthly costs (us-east-1, moderate usage):
- **Lambda**: ~$5-20 (depends on invocations and duration)
- **DynamoDB**: ~$1-5 (PAY_PER_REQUEST)
- **API Gateway**: ~$1-3 (HTTP API)
- **S3**: ~$0.01 (config storage)
- **Secrets Manager**: ~$0.40 (1 secret)
- **Bedrock**: ~$10-50 (depends on LLM usage)

**Total**: ~$20-80/month for moderate usage

## Limitations

- **File upload endpoint** (`POST /api/sync/run`) not supported — use Drive sources only
- **Max payload size**: 10MB for API Gateway (Drive webhooks are small)
- **Lambda execution limit**: 15 minutes (sufficient for most sync jobs)
- **Cold starts**: ~2-5 seconds for first invocation after idle period
