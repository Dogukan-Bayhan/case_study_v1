"""Quality report and data quality API routes."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_clickhouse, get_current_user, get_db, get_settings
from app.core.config import Settings
from app.db.clickhouse import issues_table, raw_table
from app.db.models import QualityFinding, QualityReport, User
from app.quality.schemas import (
    QualityFindingOut,
    QualityIssueDetail,
    QualityIssueItem,
    QualityIssuesPage,
    QualityOverviewOut,
    QualityReportOut,
    QualityRuleCount,
    QualityRuleImpact,
    QualitySeverityImpact,
)

router = APIRouter(prefix="/quality", tags=["quality"])


@router.get("/latest", response_model=QualityReportOut)
def latest_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> QualityReportOut:
    """Return the most recent quality report for the current tenant.

    Business purpose:
        Expose the latest data quality report for dashboard summaries.
    Why it exists:
        Ensures tenant-scoped access to the newest quality report.
    Where used:
        Quality dashboard header and summary tiles.
    Inputs:
        db: SQLAlchemy session for report lookup.
        current_user: Authenticated user for tenant scoping.
    Returns:
        QualityReportOut for the latest report or an empty placeholder.
    """
    # Query the most recent report for the tenant.
    report = (
        db.query(QualityReport)
        .filter(QualityReport.tenant_id == current_user.tenant_id)
        .order_by(QualityReport.created_at.desc())
        .first()
    )
    if report is None:
        # Return an empty payload when no report has been generated.
        return QualityReportOut(id=0, created_at="", summary={})
    # Serialize report timestamps and summary JSON.
    return QualityReportOut(
        id=report.id,
        created_at=report.created_at.isoformat(),
        summary=report.summary_json,
    )


@router.get("/findings", response_model=list[QualityFindingOut])
def list_findings(
    severity: str | None = Query(default=None),
    column: str | None = Query(default=None),
    check: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[QualityFindingOut]:
    """List quality findings for the latest tenant report.

    Business purpose:
        Surface validation issues detected by the ETL quality checks.
    Why it exists:
        Allows filtering of findings by severity, column, and rule.
    Where used:
        Quality dashboard findings table.
    Inputs:
        severity: Optional severity filter.
        column: Optional column filter.
        check: Optional rule/check filter.
        db: SQLAlchemy session for report and findings lookup.
        current_user: Authenticated user for tenant scoping.
    Returns:
        List of QualityFindingOut records.
    """
    # Load the latest report to scope findings to a single run.
    report = (
        db.query(QualityReport)
        .filter(QualityReport.tenant_id == current_user.tenant_id)
        .order_by(QualityReport.created_at.desc())
        .first()
    )
    if report is None:
        return []

    # Start with all findings for the latest report and apply optional filters.
    query = db.query(QualityFinding).filter(QualityFinding.report_id == report.id)
    if severity:
        query = query.filter(QualityFinding.severity == severity)
    if column:
        query = query.filter(QualityFinding.column == column)
    if check:
        query = query.filter(QualityFinding.check == check)

    findings = query.all()
    return [
        QualityFindingOut(
            id=f.id,
            severity=f.severity,
            column=f.column,
            check=f.check,
            message=f.message,
            examples=f.examples,
        )
        for f in findings
    ]


def _isoformat(value: object) -> str:
    """Normalize datetime values to ISO strings for JSON payloads.

    Business purpose:
        Provide consistent datetime formatting in API responses.
    Why it exists:
        Keeps serialization logic centralized for quality endpoints.
    Where used:
        Quality issues list and detail endpoints.
    Inputs:
        value: Value that may be a datetime or other type.
    Returns:
        ISO formatted datetime string or stringified fallback value.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@router.get("/overview", response_model=QualityOverviewOut)
def overview(
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings: Settings = Depends(get_settings),
) -> QualityOverviewOut:
    """Aggregate high-level quality metrics from ClickHouse layers.

    Business purpose:
        Summarize data quality health across raw, clean, and issue layers.
    Why it exists:
        Provides a fast, tenant-scoped overview for the quality dashboard.
    Where used:
        Quality overview panel in the UI.
    Inputs:
        current_user: Authenticated user for tenant scoping.
        client: ClickHouse client for analytics queries.
        settings: App settings used to resolve table names.
    Returns:
        QualityOverviewOut with counts and impacts by severity and rule.
    """
    # Resolve tenant and table names once for reuse in multiple queries.
    tenant_id = current_user.tenant_id
    raw_table_name = raw_table(settings)
    issues_table_name = issues_table(settings)

    # Aggregate counts by layer using distinct transaction identifiers.
    # Query computes distinct transaction count in the raw ingestion layer.
    # Using uniqExact prevents double-counting duplicated transaction ids.
    # Tenant filter keeps the scan scoped and supports partition pruning.
    total_raw_tx = client.execute(
        f"""
        SELECT uniqExact(transaction_id) AS total_raw_tx
        FROM {raw_table_name}
        WHERE tenant_id = %(tenant_id)s
        """,
        {"tenant_id": tenant_id},
    )[0][0]
    # Query computes distinct transactions with issues for the tenant.
    # Scoped by tenant to avoid scanning other tenants' issue rows.
    # Distinct aggregation keeps the result set minimal for performance.
    issue_tx = client.execute(
        f"""
        SELECT uniqExact(transaction_id) AS issue_tx
        FROM {issues_table_name}
        WHERE tenant_id = %(tenant_id)s
        """,
        {"tenant_id": tenant_id},
    )[0][0]
    # Clean transactions are derived by subtracting issue transactions.
    clean_tx = max(int(total_raw_tx) - int(issue_tx), 0)
    # Compute clean/issue percentages for the overview widgets.
    pct_clean = float(clean_tx) / float(total_raw_tx) if total_raw_tx else 0.0
    pct_issue = float(issue_tx) / float(total_raw_tx) if total_raw_tx else 0.0

    # Break down issues by severity using distinct transactions per severity.
    # Query computes affected transactions per severity using arrayJoin.
    # arrayDistinct reduces double-counting when multiple rules share severity.
    # Tenant filter keeps the expansion limited to relevant issue rows.
    severity_rows = client.execute(
        f"""
        SELECT severity, uniqExact(transaction_id) AS affected_tx
        FROM (
            SELECT transaction_id, arrayJoin(arrayDistinct(severity)) AS severity
            FROM {issues_table_name}
            WHERE tenant_id = %(tenant_id)s
        )
        GROUP BY severity
        """,
        {"tenant_id": tenant_id},
    )
    severity_lookup = {"error": 0, "warn": 0, "info": 0}
    for severity, count in severity_rows:
        severity_lookup[str(severity)] = int(count)
    # Build response objects with percentage impact by severity.
    issues_by_severity = [
        QualitySeverityImpact(
            severity=severity,
            affected_tx=severity_lookup[severity],
            affected_pct=float(severity_lookup[severity]) / float(total_raw_tx) if total_raw_tx else 0.0,
        )
        for severity in ("error", "warn", "info")
    ]

    # Break down issues by rule code using arrayJoin for rule arrays (violation counts).
    # Query counts total rule occurrences across all issue rows.
    # Tenant filter limits the array expansion and scan cost.
    rule_rows = client.execute(
        f"""
        SELECT rule_code, count() AS count
        FROM (
            SELECT arrayJoin(issues) AS rule_code
            FROM {issues_table_name}
            WHERE tenant_id = %(tenant_id)s
        )
        GROUP BY rule_code
        ORDER BY count DESC
        """,
        {"tenant_id": tenant_id},
    )
    by_rule = [QualityRuleCount(rule_code=row[0], count=int(row[1])) for row in rule_rows]
    # Query computes distinct transactions affected by each rule.
    # arrayDistinct prevents duplicate transactions within the same row.
    # Tenant filter keeps the scan scoped to the current tenant.
    rule_impact_rows = client.execute(
        f"""
        SELECT rule_code, uniqExact(transaction_id) AS affected_tx
        FROM (
            SELECT transaction_id, arrayJoin(arrayDistinct(issues)) AS rule_code
            FROM {issues_table_name}
            WHERE tenant_id = %(tenant_id)s
        )
        GROUP BY rule_code
        ORDER BY affected_tx DESC
        """,
        {"tenant_id": tenant_id},
    )
    issues_by_rule = [
        QualityRuleImpact(
            rule=row[0],
            affected_tx=int(row[1]),
            affected_pct=float(row[1]) / float(total_raw_tx) if total_raw_tx else 0.0,
        )
        for row in rule_impact_rows
    ]

    # Return a summary with both old and new field names for compatibility.
    return QualityOverviewOut(
        tenant_id=tenant_id,
        as_of=datetime.utcnow().isoformat(),
        total_raw_tx=int(total_raw_tx),
        issue_tx=int(issue_tx),
        clean_tx=int(clean_tx),
        pct_clean=pct_clean,
        pct_issue=pct_issue,
        issues_by_rule=issues_by_rule,
        issues_by_severity=issues_by_severity,
        by_rule=by_rule,
        total_raw_rows=int(total_raw_tx),
        total_clean_rows=int(clean_tx),
        total_issue_rows=int(issue_tx),
        by_severity=severity_lookup,
    )


@router.get("/issues", response_model=QualityIssuesPage)
def list_issues(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    severity: str | None = Query(default=None),
    rule_code: str | None = Query(default=None),
    transaction_id: str | None = Query(default=None),
    sort_by: str = Query("detected_at"),
    sort_dir: str = Query("desc"),
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings: Settings = Depends(get_settings),
) -> QualityIssuesPage:
    """Return a filtered, paginated issues list scoped to the tenant.

    Business purpose:
        Enable analysts to explore data quality issues with filters and paging.
    Why it exists:
        Provides a single endpoint for issue search and pagination.
    Where used:
        Quality issues explorer UI.
    Inputs:
        page: 1-based page index.
        page_size: Number of rows per page.
        severity: Optional severity filter.
        rule_code: Optional rule code filter.
        transaction_id: Optional exact transaction id filter.
        sort_by: Sort column key.
        sort_dir: Sort direction (asc/desc).
        current_user: Authenticated user for tenant scoping.
        client: ClickHouse client for analytics queries.
        settings: App settings used to resolve table names.
    Returns:
        QualityIssuesPage with items and total count.
    """
    # Restrict sorting to safe, indexed columns.
    allowed_sort = {"detected_at": "detected_at", "transaction_id": "transaction_id"}
    if sort_by not in allowed_sort:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported sort column")
    if sort_dir.lower() not in {"asc", "desc"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported sort direction")

    tenant_id = current_user.tenant_id
    issues_table_name = issues_table(settings)
    # Build tenant-scoped WHERE clause with optional filters.
    conditions = ["tenant_id = %(tenant_id)s"]
    params = {
        "tenant_id": tenant_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    if severity:
        # severity is stored as an array; has() checks membership.
        conditions.append("has(severity, %(severity)s)")
        params["severity"] = severity
    if rule_code:
        # issues is stored as an array of rule codes.
        conditions.append("has(issues, %(rule_code)s)")
        params["rule_code"] = rule_code
    if transaction_id:
        conditions.append("transaction_id = %(transaction_id)s")
        params["transaction_id"] = transaction_id

    where_clause = " AND ".join(conditions)

    # Count query must match filters to keep pagination accurate.
    # Query computes total matching rows for pagination metadata.
    # Count uses tenant-scoped filters to minimize scan scope.
    total = client.execute(
        f"""
        SELECT count() AS total
        FROM {issues_table_name}
        WHERE {where_clause}
        """,
        params,
    )[0][0]

    # Query fetches the current page of issue rows with ordering.
    # LIMIT/OFFSET keeps response size bounded for UI pagination.
    # ORDER BY uses whitelisted columns to avoid expensive sorts.
    rows = client.execute(
        f"""
        SELECT transaction_id, issues, severity, detected_at
        FROM {issues_table_name}
        WHERE {where_clause}
        ORDER BY {allowed_sort[sort_by]} {sort_dir.upper()}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    )
    items = [
        QualityIssueItem(
            transaction_id=row[0],
            issues=list(row[1] or []),
            severity=list(row[2] or []),
            detected_at=_isoformat(row[3]),
        )
        for row in rows
    ]
    return QualityIssuesPage(
        page=page,
        page_size=page_size,
        total=int(total),
        items=items,
    )


@router.get("/issues/{transaction_id}", response_model=QualityIssueDetail)
def issue_detail(
    transaction_id: str,
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings: Settings = Depends(get_settings),
) -> QualityIssueDetail:
    """Fetch the most recent issue record for a transaction.

    Business purpose:
        Provide detailed issue context and raw values for a transaction.
    Why it exists:
        Supports drill-down views in the quality explorer UI.
    Where used:
        GET /quality/issues/{transaction_id} from the issue detail panel.
    Inputs:
        transaction_id: Transaction identifier to look up.
        current_user: Authenticated user for tenant scoping.
        client: ClickHouse client for analytics queries.
        settings: App settings used to resolve table names.
    Returns:
        QualityIssueDetail for the most recent matching issue row.
    """
    tenant_id = current_user.tenant_id
    issues_table_name = issues_table(settings)

    # Query selects the latest issue record for the transaction within the tenant.
    # ORDER BY + LIMIT 1 keeps the lookup fast and bounded.
    # Tenant filter aligns with partitioning for pruning.
    row = client.execute(
        f"""
        SELECT tenant_id, transaction_id, issues, severity, raw_columns, detected_at
        FROM {issues_table_name}
        WHERE tenant_id = %(tenant_id)s AND transaction_id = %(transaction_id)s
        ORDER BY detected_at DESC
        LIMIT 1
        """,
        {"tenant_id": tenant_id, "transaction_id": transaction_id},
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")

    tenant_id, transaction_id, issues, severity, raw_columns, detected_at = row[0]
    return QualityIssueDetail(
        tenant_id=int(tenant_id),
        transaction_id=transaction_id,
        issues=list(issues or []),
        severity=list(severity or []),
        raw_columns=raw_columns or {},
        detected_at=_isoformat(detected_at),
    )
