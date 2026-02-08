"""
Shared constants for Suigetsukan Lambdas.
Extracted from magic numbers and strings across lambdas.
"""

# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------
DEFAULT_REGION = "us-west-1"

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401

# ---------------------------------------------------------------------------
# CORS (use CORS_ALLOWED_ORIGIN env var to restrict; default allows all)
# ---------------------------------------------------------------------------
CORS_ORIGIN_ALL = "*"
CORS_HEADERS_ALL = "*"
CORS_METHODS_GET_OPTIONS = "GET, OPTIONS"
CORS_METHODS_GET_POST_OPTIONS = "GET, POST, OPTIONS"

# ---------------------------------------------------------------------------
# Cognito
# ---------------------------------------------------------------------------
COGNITO_GROUP_ADMIN = "admin"
COGNITO_GROUP_APPROVED = "approved"
COGNITO_GROUP_UNAPPROVED = "unapproved"
COGNITO_TRIGGER_POST_CONFIRMATION = "PostConfirmation_ConfirmSignUp"

# ---------------------------------------------------------------------------
# SES / Email
# ---------------------------------------------------------------------------
CHARSET_UTF8 = "UTF-8"

# ---------------------------------------------------------------------------
# Cost Explorer (billing-rest-api)
# ---------------------------------------------------------------------------
CE_GRANULARITY_MONTHLY = "MONTHLY"
CE_METRIC_BLENDED_COST = "BlendedCost"
CE_METRIC_BLENDED_COST_FORECAST = "BLENDED_COST"
CE_PREDICTION_INTERVAL_LEVEL = 90

# ---------------------------------------------------------------------------
# DynamoDB (file-name-decipher)
# ---------------------------------------------------------------------------
DDB_INDEX_NAME = "Name-index"
DDB_MAP_KEY = "map"
DDB_ITEMS_KEY = "Items"
DDB_VARIATIONS_KEY = "Variations"

# ---------------------------------------------------------------------------
# file-name-decipher
# ---------------------------------------------------------------------------
REMOVE_ALL_TECHNIQUE_VARIATIONS = "z"
