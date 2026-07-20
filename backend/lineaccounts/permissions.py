from rest_framework.permissions import BasePermission

from .authentication import OwnerPrincipal


class IsActiveOwner(BasePermission):
    def has_permission(self, request, view) -> bool:
        return (
            isinstance(request.user, OwnerPrincipal)
            and request.user.account_state == "active"
        )


class CanResumeUnlink(BasePermission):
    def has_permission(self, request, view) -> bool:
        return (
            isinstance(request.user, OwnerPrincipal)
            and request.user.account_state
            in ("deauthorization_pending", "local_deletion_pending")
        )


class HasOwnerSession(BasePermission):
    def has_permission(self, request, view) -> bool:
        return isinstance(request.user, OwnerPrincipal)
