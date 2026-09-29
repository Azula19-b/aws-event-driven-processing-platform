variable "aws_region" {
  description = "AWS region in which to create the platform."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name prefix used for AWS resources and tags."
  type        = string
  default     = "event-processing-platform"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.project_name))
    error_message = "project_name must be 3-31 lowercase letters, numbers, or hyphens."
  }
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging, or prod."
  }
}

variable "lambda_runtime" {
  description = "Python runtime used by both Lambda functions."
  type        = string
  default     = "python3.12"
}

variable "upload_url_expiration_seconds" {
  description = "Lifetime of generated presigned upload URLs."
  type        = number
  default     = 900

  validation {
    condition     = var.upload_url_expiration_seconds >= 60 && var.upload_url_expiration_seconds <= 3600
    error_message = "Upload URL expiration must be between 60 and 3600 seconds."
  }
}

variable "processor_timeout_seconds" {
  description = "Maximum processor Lambda execution time."
  type        = number
  default     = 30
}

variable "queue_visibility_timeout_seconds" {
  description = "Time an in-flight SQS message remains hidden. Must exceed Lambda timeout."
  type        = number
  default     = 180
}

variable "max_receive_count" {
  description = "Number of processing attempts before a message moves to the DLQ."
  type        = number
  default     = 5
}

variable "log_retention_days" {
  description = "Number of days Lambda application logs are retained."
  type        = number
  default     = 30
}

variable "alarm_notification_arns" {
  description = "SNS topic ARNs notified by CloudWatch alarms. Empty disables actions."
  type        = list(string)
  default     = []
}

variable "api_authorization_type" {
  description = "Authorization enforced by the upload route. AWS_IAM is the secure default."
  type        = string
  default     = "AWS_IAM"

  validation {
    condition     = contains(["AWS_IAM", "NONE"], var.api_authorization_type)
    error_message = "api_authorization_type must be AWS_IAM or NONE."
  }
}
