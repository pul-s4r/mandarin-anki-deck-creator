#!/usr/bin/env bash
# Deploy the Mandarin Anki Deck Creator serverless stack to AWS Lambda.
#
# Prerequisites:
#   - AWS CLI configured with appropriate permissions
#   - SAM CLI installed
#   - CEDICT dictionary file (cedict_ts.u8) in project root or layers/cedict/
#   - Source set YAML config (sources.yaml) ready
#
# Usage:
#   ./scripts/deploy-lambda.sh [--profile <aws-profile>] [--region <aws-region>]

set -euo pipefail

PROFILE="${1:---profile}"
REGION="${2:-us-east-1}"
STACK_NAME="anki-pipeline-serverless"
BUCKET_NAME="anki-pipeline-config-$(aws sts get-caller-identity --query Account --output text)"

echo "=== Mandarin Anki Deck Creator - Lambda Deployment ==="
echo "Stack: $STACK_NAME"
echo "Region: $REGION"
echo "Config Bucket: $BUCKET_NAME"
echo ""

# Check AWS credentials
if ! aws sts get-caller-identity &>/dev/null; then
    echo "ERROR: AWS credentials not configured or expired."
    echo "Run: aws sso login --profile <profile>"
    exit 1
fi

# Build SAM application
echo "Building SAM application..."
sam build --template-file template.yaml --use-container

# Deploy SAM application
echo "Deploying SAM application..."
sam deploy \
    --stack-name "$STACK_NAME" \
    --s3-bucket "$BUCKET_NAME" \
    --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
    --no-fail-on-empty-changeset \
    --region "$REGION"

# Get stack outputs
echo ""
echo "=== Deployment Complete ==="
WEBHOOK_URL=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='WebhookUrl'].OutputValue" --output text --region "$REGION")
TABLE_NAME=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='StateTableName'].OutputValue" --output text --region "$REGION")

echo "Webhook URL: $WEBHOOK_URL"
echo "DynamoDB Table: $TABLE_NAME"
echo ""
echo "Next steps:"
echo "1. Upload CEDICT dictionary to S3:"
echo "   aws s3 cp cedict_ts.u8 s3://$BUCKET_NAME/cedict_ts.u8"
echo ""
echo "2. Upload source config to S3:"
echo "   aws s3 cp sources.yaml s3://$BUCKET_NAME/sources.yaml"
echo ""
echo "3. Store Drive OAuth token in Secrets Manager:"
echo "   aws secretsmanager put-secret-value --secret-id anki-pipeline/drive-credentials --secret-string file://~/.config/anki-notes-pipeline/google-drive-token.json"
echo ""
echo "4. Register Drive watch channel with webhook URL:"
echo "   $WEBHOOK_URL"
