"""
Validation rule engine.

Defines:
  ValidationResult   — outcome of a single rule check
  ValidationRule     — base class for all rules
  RuleEngine         — runs a list of rules against a record, collects results

Rules are composable and chainable. Each rule returns a ValidationResult
indicating whether to PASS, WARN (auto-correct and continue), or REJECT
(block the record from reaching the fact table).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from app.schemas.base import IssueSeverity, IssueType


class RuleOutcome(str, Enum):
    PASS   = "pass"
    WARN   = "warn"    # issue logged, record continues
    REJECT = "reject"  # record blocked


@dataclass
class ValidationResult:
    outcome:         RuleOutcome
    issue_type:      Optional[IssueType] = None
    issue_detail:    Optional[str]       = None
    raw_value:       Optional[str]       = None
    corrected_value: Optional[str]       = None
    severity:        IssueSeverity       = IssueSeverity.WARNING
    auto_corrected:  bool                = False
    field_name:      Optional[str]       = None
    corrected_record: Optional[dict]     = None  # mutated record if auto-corrected

    @classmethod
    def ok(cls) -> "ValidationResult":
        return cls(outcome=RuleOutcome.PASS)

    @classmethod
    def warn(
        cls,
        issue_type: IssueType,
        detail: str,
        raw_value: Optional[str] = None,
        corrected_value: Optional[str] = None,
        field_name: Optional[str] = None,
        corrected_record: Optional[dict] = None,
    ) -> "ValidationResult":
        return cls(
            outcome=RuleOutcome.WARN,
            issue_type=issue_type,
            issue_detail=detail,
            raw_value=raw_value,
            corrected_value=corrected_value,
            severity=IssueSeverity.WARNING,
            auto_corrected=corrected_value is not None,
            field_name=field_name,
            corrected_record=corrected_record,
        )

    @classmethod
    def reject(
        cls,
        issue_type: IssueType,
        detail: str,
        raw_value: Optional[str] = None,
        field_name: Optional[str] = None,
    ) -> "ValidationResult":
        return cls(
            outcome=RuleOutcome.REJECT,
            issue_type=issue_type,
            issue_detail=detail,
            raw_value=raw_value,
            severity=IssueSeverity.ERROR,
            field_name=field_name,
        )

    @property
    def is_ok(self) -> bool:
        return self.outcome == RuleOutcome.PASS

    @property
    def is_rejected(self) -> bool:
        return self.outcome == RuleOutcome.REJECT

    def to_issue_dict(self, source_name: str, staging_id: Optional[int] = None) -> dict:
        return {
            "staging_id":      staging_id,
            "source_name":     source_name,
            "issue_type":      self.issue_type.value if self.issue_type else "unknown",
            "issue_detail":    self.issue_detail,
            "raw_value":       self.raw_value,
            "corrected_value": self.corrected_value,
            "severity":        self.severity.value,
            "auto_corrected":  self.auto_corrected,
        }


class ValidationRule(ABC):
    """Base class for all validation rules."""

    name: str = "base_rule"

    @abstractmethod
    def validate(self, record: dict, context: Optional[dict] = None) -> ValidationResult:
        """
        Validate a single (already canonical-field) record.
        May return a mutated record in result.corrected_record.
        """
        ...


@dataclass
class RuleEngineResult:
    accepted:  bool
    record:    dict
    results:   list[ValidationResult] = field(default_factory=list)
    issues:    list[dict]             = field(default_factory=list)

    @property
    def has_warnings(self) -> bool:
        return any(r.outcome == RuleOutcome.WARN for r in self.results)

    @property
    def rejection_reasons(self) -> list[str]:
        return [
            r.issue_detail or r.issue_type.value
            for r in self.results
            if r.is_rejected
        ]


class RuleEngine:
    """
    Runs a sequence of rules against a record.
    Rules execute in order; a REJECT short-circuits remaining rules.
    Auto-corrections are applied to the record in place.
    """

    def __init__(self, rules: list[ValidationRule], source_name: str = "unknown"):
        self.rules       = rules
        self.source_name = source_name

    def run(self, record: dict, context: Optional[dict] = None) -> RuleEngineResult:
        current_record = dict(record)
        all_results: list[ValidationResult] = []
        issues: list[dict] = []

        for rule in self.rules:
            result = rule.validate(current_record, context)

            if not result.is_ok:
                all_results.append(result)
                issues.append(result.to_issue_dict(self.source_name))

                # Apply auto-correction if rule provided a fixed record
                if result.corrected_record:
                    current_record = result.corrected_record

                # Hard stop on reject
                if result.is_rejected:
                    return RuleEngineResult(
                        accepted=False,
                        record=current_record,
                        results=all_results,
                        issues=issues,
                    )

        return RuleEngineResult(
            accepted=True,
            record=current_record,
            results=all_results,
            issues=issues,
        )

    def run_batch(
        self,
        records: list[dict],
        context: Optional[dict] = None,
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """
        Run engine against a list of records.
        Returns (accepted_records, rejected_records, all_issues).
        """
        accepted:  list[dict] = []
        rejected:  list[dict] = []
        all_issues: list[dict] = []

        for record in records:
            result = self.run(record, context)
            all_issues.extend(result.issues)
            if result.accepted:
                accepted.append(result.record)
            else:
                rejected.append({**record, "_rejection_reasons": result.rejection_reasons})

        return accepted, rejected, all_issues
