"""Inspection, simulation, replay, and reporting support."""

from .orientation_campaign import (
    AlignmentSample,
    OrientationAlignmentResult,
    OrientationCampaignSummary,
    OrientationTransitionRecord,
    TargetPoint,
    compute_expected_points,
    evaluate_alignment_sample,
    format_campaign_markdown_report,
    run_guided_physical_campaign,
    summarize_latencies,
)
from .report import (
    Component,
    ComponentStatus,
    InspectionReport,
    Monitor,
    render_json,
    render_text,
)
from .support_report import (
    DiscoverySupport,
    RuntimeSupport,
    SupportReport,
    build_support_report,
    render_support_json,
    render_support_text,
)

__all__ = [
    "AlignmentSample",
    "Component",
    "ComponentStatus",
    "DiscoverySupport",
    "InspectionReport",
    "Monitor",
    "OrientationAlignmentResult",
    "OrientationCampaignSummary",
    "OrientationTransitionRecord",
    "RuntimeSupport",
    "SupportReport",
    "TargetPoint",
    "compute_expected_points",
    "build_support_report",
    "evaluate_alignment_sample",
    "format_campaign_markdown_report",
    "render_json",
    "render_support_json",
    "render_support_text",
    "render_text",
    "run_guided_physical_campaign",
    "summarize_latencies",
]
