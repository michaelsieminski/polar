from typing import Any, Protocol, TypeVar

from polar.auth.models import AuthSubject
from polar.exceptions import PolarError, PolarRequestValidationError, ValidationError
from polar.models import Benefit, Organization, User
from polar.models.benefit import BenefitProperties
from polar.notifications.notification import (
    BenefitPreconditionErrorNotificationContextualPayload,
)
from polar.postgres import AsyncSession
from polar.redis import Redis


class BenefitServiceError(PolarError): ...


class BenefitPropertiesValidationError(PolarRequestValidationError):
    """
    Benefit properties validation error.
    """

    def __init__(self, errors: list[ValidationError]) -> None:
        errors = [
            {
                "loc": ("body", "properties", *error["loc"]),
                "msg": error["msg"],
                "type": error["type"],
                "input": error["input"],
            }
            for error in errors
        ]
        super().__init__(errors)


class BenefitRetriableError(BenefitServiceError):
    """
    A retriable error occured while granting or revoking the benefit.
    """

    defer_seconds: int
    "Number of seconds to wait before retrying."

    def __init__(self, defer_seconds: int) -> None:
        self.defer_seconds = defer_seconds
        message = f"An error occured. We'll retry in {defer_seconds} seconds."
        super().__init__(message)


class BenefitPreconditionError(BenefitServiceError):
    """
    Some conditions are missing to grant the benefit.

    It accepts a payload schema.
    When set, a notification will be sent to the backer to explain them what happened.
    """

    def __init__(
        self,
        message: str,
        *,
        payload: BenefitPreconditionErrorNotificationContextualPayload | None = None,
    ) -> None:
        """
        Args:
            message: The plain error message.
            payload: The payload to build the notification.
        """
        self.payload = payload
        super().__init__(message)


B = TypeVar("B", bound=Benefit, contravariant=True)
BP = TypeVar("BP", bound=BenefitProperties)
BGP = TypeVar("BGP", bound=BenefitProperties)


class BenefitServiceProtocol(Protocol[B, BP, BGP]):
    """
    Protocol that should be implemented by each benefit type service.

    It allows to implement very customizable and specific logic to fulfill the benefit.
    """

    session: AsyncSession
    redis: Redis

    should_revoke_individually: bool = False

    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis

    async def grant(
        self,
        benefit: B,
        user: User,
        grant_properties: BGP,
        *,
        update: bool = False,
        attempt: int = 1,
    ) -> BGP:
        """
        Executes the logic to grant a benefit to a backer.

        Args:
            benefit: The Benefit to grant.
            user: The backer user.
            grant_properties: Stored properties for this specific benefit and user.
            Might be available at this stage if we're updating
            an already granted benefit.
            update: Whether we are updating an already granted benefit.
            attempt: Number of times we attempted to grant the benefit.
            Useful for the worker to implement retry logic.

        Returns:
            A dictionary with data to store for this specific benefit and user.
            For example, it can be useful to store external identifiers
            that may help when updating the grant or revoking it.
            **Existing properties will be overriden, so be sure to include all the data
            you want to keep.**

        Raises:
            BenefitRetriableError: An temporary error occured,
            we should be able to retry later.
            BenefitPreconditionError: Some conditions are missing to grant the benefit.
        """
        ...

    async def revoke(
        self,
        benefit: B,
        user: User,
        grant_properties: BGP,
        *,
        attempt: int = 1,
    ) -> BGP:
        """
        Executes the logic to revoke a benefit from a backer.

        Args:
            benefit: The Benefit to revoke.
            user: The backer user.
            grant_properties: Stored properties for this specific benefit and user.
            attempt: Number of times we attempted to revoke the benefit.
            Useful for the worker to implement retry logic.

        Returns:
            A dictionary with data to store for this specific benefit and user.
            For example, it can be useful to store external identifiers
            that may help when updating the grant or revoking it.
            **Existing properties will be overriden, so be sure to include all the data
            you want to keep.**

        Raises:
            BenefitRetriableError: An temporary error occured,
            we should be able to retry later.
        """
        ...

    async def requires_update(self, benefit: B, previous_properties: BP) -> bool:
        """
        Determines if a benefit update requires to trigger the granting logic again.

        This method is called whenever a benefit is updated. If it returns `True`, the
        granting logic will be re-executed again for all the backers, i.e. the `grant`
        method will be called with the `update` argument set to `True`.

        Args:
            benefit: The updated Benefit.
            previous_properties: The Benefit properties before the update.
            Use it to check which fields have been updated.
        """
        ...

    async def validate_properties(
        self, auth_subject: AuthSubject[User | Organization], properties: dict[str, Any]
    ) -> BP:
        """
        Validates the benefit properties before creation.

        Useful if we need to call external logic to make sure this input is valid.

        Args:
            user: The User creating the benefit.
            properties: The input properties to validate.

        Returns:
            The validated Benefit properties.
            It can be different from the input if needed.

        Raises:
            BenefitPropertiesValidationError: The benefit
            properties are invalid.
        """
        ...
