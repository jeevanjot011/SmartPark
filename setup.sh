#!/bin/bash
# AWS IoT Rule and DynamoDB Setup for SmartPark
# Run this in AWS CloudShell or with AWS CLI configured

echo "🚀 Setting up AWS infrastructure for SmartPark..."

# 1. Create DynamoDB Table
echo "Creating DynamoDB table..."
aws dynamodb create-table \
    --table-name SmartParkData \
    --attribute-definitions AttributeName=deviceId,AttributeType=S AttributeName=timestamp,AttributeType=S \
    --key-schema AttributeName=deviceId,KeyType=HASH AttributeName=timestamp,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --region us-east-1

echo "✅ DynamoDB table created"

# 2. Create IoT Rule to forward data to DynamoDB
echo "Creating IoT Rule..."

# First, create IAM role for IoT
ROLE_NAME="SmartParkIoTRole"
TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "iot.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
)

# Create role (you may need to attach policy manually in console)
echo "Creating IAM role: $ROLE_NAME"
echo "Note: Attach 'AmazonDynamoDBFullAccess' policy to this role in AWS Console"

# 3. Create IoT Rule
RULE_NAME="SmartParkToDynamoDB"
SQL="SELECT * FROM 'sensors/+/processed'"

aws iot create-topic-rule \
    --rule-name $RULE_NAME \
    --topic-rule-payload file://rule.json

echo "✅ IoT Rule created"
echo ""
echo "📋 Next Steps:"
echo "   1. Go to AWS IoT Console → Act → Rules"
echo "   2. Find '$RULE_NAME'"
echo "   3. Edit the rule and add DynamoDB action"
echo "   4. Select table: SmartParkData"
echo "   5. Hash key: deviceId, Range key: timestamp"
echo "   6. Enable the rule"