from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


RiskLevel = Literal["normal", "warning", "emergency"]
FindingSeverity = Literal["warning", "emergency"]


@dataclass(frozen=True)
class RiskFinding:
    alert_type: str
    severity: FindingSeverity
    message: str


@dataclass(frozen=True)
class RiskAssessment:
    risk_level: RiskLevel
    findings: tuple[RiskFinding, ...]


def analyze_health(data: Mapping[str, object]) -> RiskAssessment:
    findings: list[RiskFinding] = []
    heart_rate = float(data["heart_rate"])

    if heart_rate < 50:
        findings.append(
            RiskFinding("low_heart_rate", "emergency", "Low heart rate detected")
        )
    if heart_rate > 120:
        findings.append(
            RiskFinding("high_heart_rate", "emergency", "High heart rate detected")
        )
    if float(data["oxygen_level"]) < 92:
        findings.append(
            RiskFinding("low_oxygen_level", "emergency", "Low oxygen level detected")
        )
    if float(data["temperature"]) > 38:
        findings.append(
            RiskFinding("high_temperature", "warning", "High temperature detected")
        )
    if data["medicine_status"] == "missed":
        findings.append(
            RiskFinding("medicine_missed", "warning", "Medicine dose missed")
        )
    if data["emergency_pressed"] is True:
        findings.append(
            RiskFinding("emergency_button", "emergency", "Emergency button pressed")
        )

    if any(finding.severity == "emergency" for finding in findings):
        risk_level: RiskLevel = "emergency"
    elif findings:
        risk_level = "warning"
    else:
        risk_level = "normal"

    return RiskAssessment(risk_level=risk_level, findings=tuple(findings))
