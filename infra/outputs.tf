output "ecr_repository_url" {
  value       = aws_ecr_repository.app.repository_url
  description = "Push target for the CD pipeline."
}

output "ecs_cluster" {
  value = aws_ecs_cluster.this.name
}

output "agent_service_name" {
  value = aws_ecs_service.agent.name
}

output "agent_task_family" {
  value = aws_ecs_task_definition.agent.family
}

output "github_deploy_role_arn" {
  value       = aws_iam_role.github_deploy.arn
  description = "Set as the AWS_DEPLOY_ROLE_ARN secret in GitHub."
}

output "repair_topic_arn" {
  value = aws_sns_topic.repair.arn
}

output "processing_topic_arn" {
  value = aws_sns_topic.processing.arn
}

output "repair_queue_url" {
  value = aws_sqs_queue.repair.url
}
