"""Translation between protobuf messages and the validated Pydantic contracts.

Incoming messages become the same camelCase documents the REST API accepts, so every
validator in app/projects.py applies unchanged. Outgoing results start from validated models.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from protobuf.wkt.google.protobuf.timestamp_pb import Timestamp

from ..projects import AlignmentSnapshot as AlignmentModel
from ..projects import Project as ProjectModel
from ..projects import ProjectSummary as SummaryModel
from ..projects import SequenceSource as SourceModel
from ..projects import Workspace as WorkspaceModel
from ..uniprot import EntrySummary as EntrySummaryModel
from ..uniprot import UniProtEntry as EntryModel
from .gen.proteinforge.project.v1 import project_service_pb as project_pb
from .gen.proteinforge.project.v1 import workspace_pb
from .gen.proteinforge.reference.v1 import reference_service_pb as reference_pb

MATRICES = {
    workspace_pb.SubstitutionMatrix.BLOSUM45: "BLOSUM45",
    workspace_pb.SubstitutionMatrix.BLOSUM62: "BLOSUM62",
    workspace_pb.SubstitutionMatrix.BLOSUM80: "BLOSUM80",
}
MODES = {workspace_pb.AlignmentMode.GLOBAL: "global", workspace_pb.AlignmentMode.LOCAL: "local"}
TERMINAL_GAPS = {workspace_pb.TerminalGaps.FREE: "free", workspace_pb.TerminalGaps.NOT_APPLICABLE: "not-applicable"}
ORIGINS = {workspace_pb.SourceOrigin.CATALOG: "catalog", workspace_pb.SourceOrigin.UNIPROT: "uniprot"}
STATUSES = {"draft": project_pb.ProjectStatus.DRAFT}
PAGE_TOKEN_PREFIX = "offset:"


def _reverse(mapping: dict) -> dict:
    return {value: key for key, value in mapping.items()}


def _enum(mapping: dict, value: Any, field: str) -> str:
    try:
        return mapping[value]
    except KeyError as error:
        raise ConnectError(Code.INVALID_ARGUMENT, f"validation: {field}") from error


def timestamp(value: str | datetime | None) -> Timestamp | None:
    if value is None:
        return None
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return Timestamp.from_datetime(moment)


def moment(value: Timestamp | None, field: str) -> datetime:
    if value is None:
        raise ConnectError(Code.INVALID_ARGUMENT, f"validation: {field}")
    return value.to_datetime()


# Incoming messages -> REST-shaped documents -------------------------------------------------

def workspace_document(workspace: workspace_pb.Workspace | None) -> dict[str, Any]:
    if workspace is None:
        return {}
    document: dict[str, Any] = {}
    if workspace.schema_version:
        document["schemaVersion"] = workspace.schema_version
    if workspace.profile_window:
        document["profileWindow"] = workspace.profile_window
    if workspace.source is not None:
        source = workspace.source
        document["source"] = {
            "origin": _enum(ORIGINS, source.origin, "workspace.source.origin"),
            "accession": source.accession,
            "sequence": source.sequence,
            "entryVersion": source.entry_version,
            "sequenceVersion": source.sequence_version,
            "retrievedAt": source.retrieve_time.to_datetime().isoformat() if source.retrieve_time is not None else None,
        }
    if workspace.descriptors is not None:
        descriptors = workspace.descriptors
        result = descriptors.result
        document["descriptors"] = {
            "sequence": descriptors.sequence,
            "excluded": descriptors.excluded,
            "methodVersion": descriptors.method_version,
            "result": None if result is None else {
                "massDa": result.mass_da,
                "isoelectricPoint": result.isoelectric_point,
                "instabilityIndex": result.instability_index,
                "aliphaticIndex": result.aliphatic_index,
                "gravy": result.gravy,
                "length": result.length,
            },
        }
    if workspace.alignment is not None:
        document["alignment"] = alignment_document(workspace.alignment)
    return document


def alignment_document(alignment: workspace_pb.AlignmentSnapshot) -> dict[str, Any]:
    document: dict[str, Any] = {"first": alignment.first, "second": alignment.second}
    # Unspecified matrix or mode fall back to the contract defaults, as an absent REST field does.
    if alignment.matrix != workspace_pb.SubstitutionMatrix.UNSPECIFIED:
        document["matrix"] = _enum(MATRICES, alignment.matrix, "workspace.alignment.matrix")
    if alignment.mode != workspace_pb.AlignmentMode.UNSPECIFIED:
        document["mode"] = _enum(MODES, alignment.mode, "workspace.alignment.mode")
    result = alignment.result
    if result is not None:
        parameters = result.parameters
        if parameters is None:
            raise ConnectError(Code.INVALID_ARGUMENT, "validation: workspace.alignment.result.parameters")
        document["result"] = {
            "alignedFirst": result.aligned_first,
            "alignedSecond": result.aligned_second,
            "marks": result.marks,
            "score": result.score,
            "identities": result.identities,
            "similarities": result.similarities,
            "columns": result.columns,
            "parameters": {
                "matrix": _enum(MATRICES, parameters.matrix, "workspace.alignment.result.parameters.matrix"),
                "mode": _enum(MODES, parameters.mode, "workspace.alignment.result.parameters.mode"),
                "gapOpen": parameters.gap_open,
                "gapExtend": parameters.gap_extend,
                "terminalGaps": _enum(TERMINAL_GAPS, parameters.terminal_gaps, "workspace.alignment.result.parameters.terminalGaps"),
            },
        }
    return document


def write_document(name: str, active_sequence: str, workspace: workspace_pb.Workspace | None) -> dict[str, Any]:
    return {"name": name, "activeSequence": active_sequence, "workspace": workspace_document(workspace)}


# Validated models -> outgoing messages -----------------------------------------------------

def source_message(source: SourceModel | None) -> workspace_pb.SequenceSource | None:
    if source is None:
        return None
    return workspace_pb.SequenceSource(
        origin=_reverse(ORIGINS)[source.origin],
        accession=source.accession,
        sequence=source.sequence,
        entry_version=source.entry_version,
        sequence_version=source.sequence_version,
        retrieve_time=timestamp(source.retrieved_at),
    )


def alignment_message(alignment: AlignmentModel) -> workspace_pb.AlignmentSnapshot:
    result = alignment.result
    return workspace_pb.AlignmentSnapshot(
        first=alignment.first,
        second=alignment.second,
        matrix=_reverse(MATRICES)[alignment.matrix],
        mode=_reverse(MODES)[alignment.mode],
        result=None if result is None else workspace_pb.AlignmentResult(
            aligned_first=result.aligned_first,
            aligned_second=result.aligned_second,
            marks=result.marks,
            score=result.score,
            identities=result.identities,
            similarities=result.similarities,
            columns=result.columns,
            parameters=workspace_pb.AlignmentParameters(
                matrix=_reverse(MATRICES)[result.parameters.matrix],
                mode=_reverse(MODES)[result.parameters.mode],
                gap_open=float(result.parameters.gap_open),
                gap_extend=float(result.parameters.gap_extend),
                terminal_gaps=_reverse(TERMINAL_GAPS)[result.parameters.terminal_gaps],
            ),
        ),
    )


def workspace_message(workspace: WorkspaceModel) -> workspace_pb.Workspace:
    descriptors = workspace.descriptors
    return workspace_pb.Workspace(
        schema_version=workspace.schema_version,
        profile_window=workspace.profile_window,
        source=source_message(workspace.source),
        descriptors=None if descriptors is None else workspace_pb.DescriptorSnapshot(
            sequence=descriptors.sequence,
            excluded=descriptors.excluded,
            method_version=descriptors.method_version,
            result=workspace_pb.DescriptorResult(
                mass_da=descriptors.result.mass_da,
                isoelectric_point=descriptors.result.isoelectric_point,
                instability_index=descriptors.result.instability_index,
                aliphatic_index=descriptors.result.aliphatic_index,
                gravy=descriptors.result.gravy,
                length=descriptors.result.length,
            ),
        ),
        alignment=alignment_message(workspace.alignment),
    )


def summary_message(summary: SummaryModel) -> project_pb.ProjectSummary:
    return project_pb.ProjectSummary(
        id=str(summary.id),
        name=summary.name,
        status=STATUSES.get(summary.status, project_pb.ProjectStatus.UNSPECIFIED),
        create_time=timestamp(summary.created_at),
        update_time=timestamp(summary.updated_at),
    )


def project_message(project: ProjectModel) -> project_pb.Project:
    return project_pb.Project(
        summary=summary_message(project),
        active_sequence=project.active_sequence,
        workspace=workspace_message(project.workspace),
    )


def entry_summary_message(summary: EntrySummaryModel) -> reference_pb.EntrySummary:
    return reference_pb.EntrySummary(
        accession=summary.accession, name=summary.name, organism=summary.organism, reviewed=summary.reviewed, length=summary.length,
    )


def entry_message(entry: EntryModel) -> reference_pb.Entry:
    return reference_pb.Entry(
        summary=entry_summary_message(entry),
        sequence=entry.sequence,
        entry_version=entry.entry_version,
        sequence_version=entry.sequence_version,
        retrieve_time=timestamp(entry.retrieved_at),
        function=entry.function,
        structures=[
            reference_pb.PdbReference(id=item.id, method=item.method, resolution=item.resolution, chains=item.chains)
            for item in entry.structures
        ],
    )


# Pagination --------------------------------------------------------------------------------

def page_token(offset: int | None) -> str:
    if offset is None:
        return ""
    return base64.urlsafe_b64encode(f"{PAGE_TOKEN_PREFIX}{offset}".encode()).decode().rstrip("=")


def page_offset(token: str, maximum: int) -> int:
    """Offset hidden in an opaque page token; INVALID_ARGUMENT for anything not issued by page_token."""
    if not token:
        return 0
    try:
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        if not decoded.startswith(PAGE_TOKEN_PREFIX):
            raise ValueError("Unknown token")
        offset = int(decoded[len(PAGE_TOKEN_PREFIX):])
        if not 0 <= offset <= maximum:
            raise ValueError("Offset out of range")
        return offset
    except ValueError as error:
        raise ConnectError(Code.INVALID_ARGUMENT, "validation: page_token") from error
