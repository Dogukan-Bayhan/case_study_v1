"""Schemas for quality reports."""

from pydantic import BaseModel


class QualityReportOut(BaseModel):
    """Serialized summary of a quality report for the UI."""
    id: int
    created_at: str
    summary: dict


class QualityFindingOut(BaseModel):
    """Individual quality finding exposed via API."""
    id: int
    severity: str
    column: str | None
    check: str
    message: str
    examples: dict | None


class QualityRuleCount(BaseModel):
    """Rule histogram entry for dashboard breakdowns."""
    rule_code: str
    count: int


class QualityRuleImpact(BaseModel):
    """Transaction-level rule impact with percent of total."""
    rule: str
    affected_tx: int
    affected_pct: float


class QualitySeverityImpact(BaseModel):
    """Transaction-level severity impact with percent of total."""
    severity: str
    affected_tx: int
    affected_pct: float


class QualityOverviewOut(BaseModel):
    """Aggregated quality metrics for the overview dashboard."""
    tenant_id: int
    as_of: str
    total_raw_tx: int
    issue_tx: int
    clean_tx: int
    pct_clean: float
    pct_issue: float
    issues_by_rule: list[QualityRuleImpact]
    issues_by_severity: list[QualitySeverityImpact]
    by_rule: list[QualityRuleCount] | None = None
    total_raw_rows: int | None = None
    total_clean_rows: int | None = None
    total_issue_rows: int | None = None
    by_severity: dict[str, int] | None = None


class QualityIssueItem(BaseModel):
    """Row summary for the issues table."""
    transaction_id: str
    issues: list[str]
    severity: list[str]
    detected_at: str


class QualityIssuesPage(BaseModel):
    """Paginated issue listing response."""
    page: int
    page_size: int
    total: int
    items: list[QualityIssueItem]


class QualityIssueDetail(BaseModel):
    """Detailed issue payload for drill-down views."""
    transaction_id: str
    tenant_id: int
    issues: list[str]
    severity: list[str]
    raw_columns: dict[str, str]
    detected_at: str


class IssuesAnalyticsKpis(BaseModel):
    """Top-level KPIs for the issues analytics panel."""
    issue_tx: int
    issue_rows: int
    top_severity: str | None = None


class IssuesSeverityCount(BaseModel):
    """Severity distribution entry for issues analytics."""
    severity: str
    count: int
    pct: float


class IssuesRuleCount(BaseModel):
    """Rule distribution entry for issues analytics."""
    rule: str
    count: int
    pct: float


class IssuesColumnCount(BaseModel):
    """Column distribution entry for issues analytics."""
    column: str
    count: int
    pct: float


class IssuesTrendPoint(BaseModel):
    """Time-bucketed issue trend point."""
    bucket: str
    count: int


class IssuesImpactItem(BaseModel):
    """Summary of a high-impact issue rule."""
    rule: str
    severity: str | None = None
    affected_tx: int
    issue_rows: int
    example_message: str | None = None


class IssuesAnalyticsOut(BaseModel):
    """Aggregated issues analytics payload for the quality UI."""
    as_of: str
    kpis: IssuesAnalyticsKpis
    by_severity: list[IssuesSeverityCount]
    by_rule: list[IssuesRuleCount]
    by_column: list[IssuesColumnCount]
    trend: list[IssuesTrendPoint]
    top_issues: list[IssuesImpactItem]
