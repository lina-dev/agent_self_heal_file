# SNS topics: validation publishes status to "processing" and hand-off to "repair".
resource "aws_sns_topic" "processing" {
  name = "${var.project}-processing"
}

resource "aws_sns_topic" "repair" {
  name = "${var.project}-repair"
}

# Dead-letter queue for messages the agent service can't process.
resource "aws_sqs_queue" "repair_dlq" {
  name                      = "${var.project}-repair-dlq"
  message_retention_seconds = 1209600 # 14 days
}

# Main repair queue. Visibility timeout > worst-case repair time so a message
# isn't redelivered while still being worked. Redrive to DLQ after 5 attempts.
resource "aws_sqs_queue" "repair" {
  name                       = "${var.project}-repair"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 345600 # 4 days
  receive_wait_time_seconds  = 20     # long polling

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.repair_dlq.arn
    maxReceiveCount     = 5
  })
}

# Subscribe the repair queue to the repair topic (raw delivery: body == payload).
resource "aws_sns_topic_subscription" "repair_to_queue" {
  topic_arn            = aws_sns_topic.repair.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.repair.arn
  raw_message_delivery = true
}

# Allow the repair topic to enqueue into the repair queue.
resource "aws_sqs_queue_policy" "repair" {
  queue_url = aws_sqs_queue.repair.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sns.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.repair.arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_sns_topic.repair.arn } }
    }]
  })
}
