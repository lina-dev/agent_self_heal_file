variable "project" {
  type        = string
  default     = "audio-repair"
  description = "Name prefix for all resources."
}

variable "region" {
  type        = string
  default     = "us-east-1"
  description = "AWS region."
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Container image tag to run (CD overrides with the git SHA)."
}

# --- Networking (Fargate tasks run in these subnets) ---
variable "private_subnet_ids" {
  type        = list(string)
  description = "Private subnets with NAT egress for the Fargate tasks."
}

variable "vpc_id" {
  type        = string
  description = "VPC for the service security group."
}

# --- Task sizing ---
variable "task_cpu" {
  type        = number
  default     = 1024 # 1 vCPU
  description = "Fargate task CPU units."
}

variable "task_memory" {
  type        = number
  default     = 2048 # 2 GB
  description = "Fargate task memory (MiB)."
}

variable "agent_min_tasks" {
  type    = number
  default = 0
}

variable "agent_max_tasks" {
  type    = number
  default = 10
}

# --- LLM backend (non-secret config; key comes from Secrets Manager) ---
variable "llm_base_url" {
  type        = string
  description = "OpenAI-compatible endpoint (e.g. internal vLLM service URL)."
}

variable "llm_model" {
  type        = string
  description = "Served model id."
}

variable "llm_api_key_secret_arn" {
  type        = string
  description = "Secrets Manager ARN holding the LLM API key (injected as LLM_API_KEY)."
}

# --- CI/CD (GitHub OIDC) ---
variable "github_owner_repo" {
  type        = string
  description = "GitHub <owner>/<repo> allowed to assume the deploy role (OIDC)."
}
