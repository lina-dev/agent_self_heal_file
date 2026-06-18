resource "aws_ecs_cluster" "this" {
  name = var.project
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "agent" {
  name              = "/ecs/${var.project}/agent"
  retention_in_days = 30
}

resource "aws_security_group" "agent" {
  name        = "${var.project}-agent"
  description = "Egress-only SG for the agent service tasks"
  vpc_id      = var.vpc_id

  egress {
    description = "all egress (ECR, SQS/SNS/S3 via NAT or endpoints, LLM)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

locals {
  image = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
}

resource "aws_ecs_task_definition" "agent" {
  family                   = "${var.project}-agent"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "agent"
    image     = local.image
    essential = true
    command   = ["serve", "--mode", "agent"]

    environment = [
      { name = "AWS_REGION", value = var.region },
      { name = "SQS_QUEUE_URL", value = aws_sqs_queue.repair.url },
      { name = "PROCESSING_TOPIC_ARN", value = aws_sns_topic.processing.arn },
      { name = "REPAIR_TOPIC_ARN", value = aws_sns_topic.repair.arn },
      { name = "S3_OUTPUT_BUCKET", value = "${var.project}-output" },
      { name = "LLM_BASE_URL", value = var.llm_base_url },
      { name = "LLM_MODEL", value = var.llm_model },
    ]

    # Secret injected from Secrets Manager, never baked into the image/task def.
    secrets = [
      { name = "LLM_API_KEY", valueFrom = var.llm_api_key_secret_arn },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.agent.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "agent"
      }
    }
  }])
}

resource "aws_ecs_service" "agent" {
  name            = "${var.project}-agent"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.agent.arn
  desired_count   = var.agent_min_tasks
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.agent.id]
    assign_public_ip = false
  }

  # CD updates the image via a new task-def revision; ignore so Terraform and
  # the pipeline don't fight over the running revision.
  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }
}

# --- Autoscaling on queue depth (ECS is not push-triggered by SQS) ---
resource "aws_appautoscaling_target" "agent" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.agent.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.agent_min_tasks
  max_capacity       = var.agent_max_tasks
}

# Target-track ~5 visible messages per running task.
resource "aws_appautoscaling_policy" "agent_queue_depth" {
  name               = "${var.project}-agent-queue-depth"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.agent.service_namespace
  resource_id        = aws_appautoscaling_target.agent.resource_id
  scalable_dimension = aws_appautoscaling_target.agent.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = 5
    scale_in_cooldown  = 120
    scale_out_cooldown = 30

    customized_metric_specification {
      metric_name = "ApproximateNumberOfMessagesVisible"
      namespace   = "AWS/SQS"
      statistic   = "Average"
      dimensions {
        name  = "QueueName"
        value = aws_sqs_queue.repair.name
      }
    }
  }
}
