"""
Unit tests for Cost Janitor helper functions.
These run without a real AWS account using moto mocking.

Run with:
  pip install moto[ec2] pytest
  pytest janitor/tests/
"""

import pytest
from unittest.mock import MagicMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from janitor import (
    days_since,
    is_protected,
    missing_required_tags,
    build_report,
)
from datetime import datetime, timezone, timedelta


# --- days_since ---

def test_days_since_zero_for_none():
    assert days_since(None) == 0


def test_days_since_returns_correct_days():
    dt = datetime.now(timezone.utc) - timedelta(days=10)
    assert days_since(dt) == 10


def test_days_since_naive_datetime():
    """Naive datetimes (no tzinfo) should be handled without crashing."""
    dt = datetime.utcnow() - timedelta(days=5)
    result = days_since(dt)
    assert result >= 4  # allow 1 day slack for boundary conditions


# --- is_protected ---

def test_is_protected_true():
    tags = [{"Key": "Protected", "Value": "true"}]
    assert is_protected(tags) is True


def test_is_protected_false_when_no_tags():
    assert is_protected([]) is False
    assert is_protected(None) is False


def test_is_protected_case_insensitive():
    tags = [{"Key": "Protected", "Value": "True"}]
    assert is_protected(tags) is True


def test_is_protected_false_when_other_tags():
    tags = [{"Key": "Project", "Value": "nimbuskart"}]
    assert is_protected(tags) is False


# --- missing_required_tags ---

def test_missing_required_tags_all_missing():
    missing = missing_required_tags([])
    assert set(missing) == {"Project", "Environment", "Owner"}


def test_missing_required_tags_none_missing():
    tags = [
        {"Key": "Project", "Value": "nimbuskart"},
        {"Key": "Environment", "Value": "staging"},
        {"Key": "Owner", "Value": "platform-team"},
    ]
    assert missing_required_tags(tags) == []


def test_missing_required_tags_partial():
    tags = [{"Key": "Project", "Value": "nimbuskart"}]
    missing = missing_required_tags(tags)
    assert "Environment" in missing
    assert "Owner" in missing
    assert "Project" not in missing


# --- build_report ---

def test_build_report_structure():
    findings = [
        {
            "resource_id": "vol-abc123",
            "resource_type": "ebs_volume",
            "reason": "unattached",
            "age_days": 21,
            "estimated_monthly_cost_usd": 8.00,
            "tags": {"Project": None, "Environment": None, "Owner": None},
            "suggested_action": "delete",
            "safe_to_auto_delete": False,
        }
    ]
    report = build_report(findings, "000000000000", "us-east-1")

    assert "scan_timestamp" in report
    assert report["account_id"] == "000000000000"
    assert report["region"] == "us-east-1"
    assert report["summary"]["total_orphans"] == 1
    assert report["summary"]["estimated_monthly_waste_usd"] == 8.00
    assert len(report["findings"]) == 1
    assert report["findings"][0]["resource_id"] == "vol-abc123"


def test_build_report_empty():
    report = build_report([], "000000000000", "us-east-1")
    assert report["summary"]["total_orphans"] == 0
    assert report["summary"]["estimated_monthly_waste_usd"] == 0.0
    assert report["findings"] == []
