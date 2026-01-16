"""Rule-level tests for extended ETL data quality checks."""

from app.etl import rules


def test_country_city_mismatch_flags_known_city():
    """Ensure city-to-country mismatches are detected for known cities."""
    result = rules.country_city_mismatch("Italy", "London", rules.CITY_TO_COUNTRY)
    assert result == (rules.RULE_COUNTRY_CITY_MISMATCH, rules.SEVERITY_ERROR)


def test_country_city_mismatch_skips_unknown_city():
    """Ensure unknown or ambiguous cities are not flagged."""
    result = rules.country_city_mismatch("Italy", "Unknownville", rules.CITY_TO_COUNTRY)
    assert result is None


def test_region_country_mismatch_flags_conflict():
    """Ensure region codes conflicting with country mapping are flagged."""
    result = rules.region_country_mismatch("USA", "EU")
    assert result == (rules.RULE_REGION_COUNTRY_MISMATCH, rules.SEVERITY_ERROR)


def test_region_country_mismatch_allows_match():
    """Ensure correct region codes do not raise issues."""
    result = rules.region_country_mismatch("USA", "NA")
    assert result is None


def test_postal_code_invalid_flags_bad_format():
    """Ensure postal codes are validated against known formats."""
    result = rules.postal_code_invalid("USA", "ABCDE")
    assert result == (rules.RULE_POSTAL_CODE_INVALID, rules.SEVERITY_WARN)


def test_postal_code_invalid_allows_valid_code():
    """Ensure valid postal codes are accepted."""
    result = rules.postal_code_invalid("USA", "12345")
    assert result is None


def test_phone_country_mismatch_flags_prefix_conflict():
    """Ensure phone prefixes mismatching country are flagged."""
    result = rules.phone_country_mismatch("USA", "+44 20 1234 5678")
    assert result == (rules.RULE_PHONE_COUNTRY_MISMATCH, rules.SEVERITY_WARN)


def test_phone_country_mismatch_allows_matching_prefix():
    """Ensure matching phone prefixes do not raise issues."""
    result = rules.phone_country_mismatch("UK", "+44 20 1234 5678")
    assert result is None


def test_financial_total_mismatch_flags_incorrect_total():
    """Ensure financial mismatches are detected with tolerance."""
    row = {
        "quantity": "2",
        "unit_price": "10",
        "discount_percent": "0",
        "tax_rate": "0",
        "total_amount": "15",
    }
    result = rules.financial_total_mismatch(row)
    assert result == (rules.RULE_FINANCIAL_TOTAL_MISMATCH, rules.SEVERITY_ERROR)


def test_financial_total_mismatch_allows_correct_total():
    """Ensure correct totals do not raise issues."""
    row = {
        "quantity": "2",
        "unit_price": "$10.00",
        "discount_percent": "0",
        "tax_rate": "0",
        "total_amount": "20.00",
    }
    result = rules.financial_total_mismatch(row)
    assert result is None


def test_status_invalid_flags_typo():
    """Ensure invalid status values are flagged."""
    result = rules.status_invalid("Completted")
    assert result == (rules.RULE_STATUS_INVALID, rules.SEVERITY_WARN)


def test_payment_method_invalid_flags_typo():
    """Ensure invalid payment method values are flagged."""
    result = rules.payment_method_invalid("Credt Card")
    assert result == (rules.RULE_PAYMENT_METHOD_INVALID, rules.SEVERITY_WARN)


def test_status_payment_inconsistent_flags_missing_payment():
    """Ensure completed statuses require a payment method."""
    result = rules.status_payment_inconsistent("Completed", "")
    assert result == (rules.RULE_STATUS_PAYMENT_INCONSISTENT, rules.SEVERITY_WARN)


def test_category_department_invalid_flags_unknowns():
    """Ensure invalid categories and departments are flagged."""
    category_result = rules.category_invalid("Electronnics")
    department_result = rules.department_invalid("Operatons")
    assert category_result == (rules.RULE_CATEGORY_INVALID, rules.SEVERITY_WARN)
    assert department_result == (rules.RULE_DEPARTMENT_INVALID, rules.SEVERITY_WARN)
