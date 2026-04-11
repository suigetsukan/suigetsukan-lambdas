"""
Sanity tests for common.constants (ensure refactors don't break expected values).
"""

from common.constants import (
    CE_GRANULARITY_MONTHLY,
    COGNITO_GROUP_ADMIN,
    DEFAULT_REGION,
    DDB_INDEX_NAME,
    HTTP_BAD_REQUEST,
    HTTP_NO_CONTENT,
    HTTP_OK,
    HTTP_UNAUTHORIZED,
)


def test_http_constants():
    assert HTTP_OK == 200
    assert HTTP_NO_CONTENT == 204
    assert HTTP_BAD_REQUEST == 400
    assert HTTP_UNAUTHORIZED == 401


def test_cognito_group_admin():
    assert COGNITO_GROUP_ADMIN == "admin"


def test_default_region():
    assert DEFAULT_REGION == "us-west-1"


def test_ce_granularity_monthly():
    assert CE_GRANULARITY_MONTHLY == "MONTHLY"


def test_ddb_index_name():
    assert DDB_INDEX_NAME == "Name-index"
