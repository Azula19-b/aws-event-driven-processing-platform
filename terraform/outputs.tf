output "api_endpoint" {
  description = "Base URL for the HTTP upload API."
  value       = aws_apigatewayv2_api.uploads.api_endpoint
}

output "upload_bucket_name" {
  description = "Private bucket that receives uploaded files."
  value       = aws_s3_bucket.uploads.id
}

output "file_events_queue_url" {
  description = "URL of the SQS file-events queue."
  value       = aws_sqs_queue.file_events.id
}

output "dead_letter_queue_url" {
  description = "URL of the dead-letter queue."
  value       = aws_sqs_queue.dead_letter.id
}

output "metadata_table_name" {
  description = "DynamoDB table containing processed file metadata."
  value       = aws_dynamodb_table.file_metadata.name
}

output "upload_url_log_group" {
  description = "CloudWatch log group for the upload URL Lambda."
  value       = aws_cloudwatch_log_group.upload_url.name
}

output "processor_log_group" {
  description = "CloudWatch log group for the processor Lambda."
  value       = aws_cloudwatch_log_group.processor.name
}
