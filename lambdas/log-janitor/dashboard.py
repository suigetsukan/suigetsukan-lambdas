"""
CloudWatch dashboard widget builders for log-janitor.
Extracted to keep app.py under 1000 lines.
"""


def build_dashboard_widgets(
    config: dict,
    region: str,
    lambdas_list: list,
    ddb_tables: list,
    _sns_topic_names: list,
) -> list:
    """Build CloudWatch dashboard body (widgets list).

    Every metric widget must declare `region` in its properties or
    `PutDashboard` rejects the body with InvalidParameterInput.
    """
    widgets = []
    for i, fname in enumerate(lambdas_list[:12]):
        widgets.append(
            {
                "type": "metric",
                "x": (i % 6) * 4,
                "y": (i // 6) * 4,
                "width": 4,
                "height": 4,
                "properties": {
                    "region": region,
                    "title": f"Lambda {fname}",
                    "metrics": [
                        ["AWS/Lambda", "Errors", "FunctionName", fname],
                        [".", "Throttles", ".", "."],
                    ],
                    "view": "timeSeries",
                    "period": 300,
                },
            }
        )
    for i, tname in enumerate(ddb_tables[:6]):
        widgets.append(
            {
                "type": "metric",
                "x": (i % 6) * 4,
                "y": (12 // 6 + 1) * 4 + (i // 6) * 4,
                "width": 4,
                "height": 4,
                "properties": {
                    "region": region,
                    "title": f"DynamoDB {tname}",
                    "metrics": [
                        ["AWS/DynamoDB", "ThrottledRequests", "TableName", tname],
                        [".", "SystemErrors", ".", "."],
                    ],
                    "view": "timeSeries",
                    "period": 300,
                },
            }
        )
    ns = config.get("cloudtrail_metric_namespace", "Security/CloudTrail")
    widgets.append(
        {
            "type": "metric",
            "x": 0,
            "y": 20,
            "width": 12,
            "height": 6,
            "properties": {
                "region": region,
                "title": "CloudTrail Tripwires",
                "metrics": [
                    [ns, "RootLogin"],
                    [".", "StopLoggingOrDeleteTrail"],
                    [".", "IAMPolicyChange"],
                    [".", "IoTPolicyChange"],
                    [".", "DeleteLogGroup"],
                ],
                "view": "timeSeries",
                "period": 300,
            },
        }
    )
    return widgets
