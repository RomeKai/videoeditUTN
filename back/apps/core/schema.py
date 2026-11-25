"""OpenAPI schema (drf-spectacular): the views scoped to the active workspace declare its header."""
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter

from .permissions import WorkspaceRolePermission
from .workspace import WORKSPACE_HEADER

WORKSPACE_PARAMETER = OpenApiParameter(
    name=WORKSPACE_HEADER,
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.HEADER,
    required=False,
    description=(
        "Active workspace. Without it: the oldest workspace the user owns, else their oldest "
        "active membership. A workspace the user cannot act in (not a member, or a pending "
        "invitation) answers 404, like one that does not exist."
    ),
)


class WorkspaceAutoSchema(AutoSchema):
    def get_override_parameters(self):
        parameters = super().get_override_parameters()
        if WorkspaceRolePermission in getattr(self.view, 'permission_classes', ()):
            return [*parameters, WORKSPACE_PARAMETER]
        return parameters
