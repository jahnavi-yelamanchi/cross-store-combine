# Single-table design matching xstore.stores.dynamodb (TABLE = "xstore", hash
# key `pk` = "<ENTITY>#<key>"). The stream feeds the CDC Lambda.

variable "kms_key_arn" {
  type = string
}

variable "deletion_protection" {
  type = bool
}

variable "create_ledger_table" {
  type = bool
}

variable "ledger_table_name" {
  type = string
}

resource "aws_dynamodb_table" "xstore" {
  name                        = "xstore"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  stream_enabled              = true
  stream_view_type            = "NEW_AND_OLD_IMAGES"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "pk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }
}

# Optional append-only ledger: pk = chain id, seq = position in the hash chain.
resource "aws_dynamodb_table" "ledger" {
  count = var.create_ledger_table ? 1 : 0

  name                        = var.ledger_table_name
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "seq"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "seq"
    type = "N"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }
}

output "table_name" {
  value = aws_dynamodb_table.xstore.name
}

output "table_arn" {
  value = aws_dynamodb_table.xstore.arn
}

output "stream_arn" {
  value = aws_dynamodb_table.xstore.stream_arn
}

output "table_arns" {
  value = concat([aws_dynamodb_table.xstore.arn], aws_dynamodb_table.ledger[*].arn)
}
