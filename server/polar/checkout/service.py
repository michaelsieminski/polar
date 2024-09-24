import uuid
from collections.abc import Sequence
from typing import Any, cast

import stripe as stripe_lib
from sqlalchemy import Select, UnaryExpression, asc, desc, select
from sqlalchemy.orm import contains_eager, joinedload

from polar.auth.models import AuthSubject, is_organization, is_user
from polar.checkout.schemas import (
    CheckoutConfirm,
    CheckoutCreate,
    CheckoutUpdate,
    CheckoutUpdatePublic,
)
from polar.enums import PaymentProcessor
from polar.exceptions import PolarError, PolarRequestValidationError, ValidationError
from polar.integrations.stripe.schemas import ProductType
from polar.integrations.stripe.service import stripe as stripe_service
from polar.integrations.stripe.utils import get_expandable_id
from polar.kit.crypto import generate_token
from polar.kit.pagination import PaginationParams, paginate
from polar.kit.services import ResourceServiceReader
from polar.kit.sorting import Sorting
from polar.kit.utils import utc_now
from polar.models import (
    Checkout,
    Organization,
    Product,
    ProductPriceCustom,
    ProductPriceFixed,
    User,
    UserOrganization,
)
from polar.models.checkout import CheckoutStatus
from polar.models.product_price import ProductPriceFree
from polar.postgres import AsyncSession
from polar.product.service.product import product as product_service
from polar.product.service.product_price import product_price as product_price_service

from .sorting import CheckoutSortProperty


class CheckoutError(PolarError): ...


class CheckoutDoesNotExist(CheckoutError):
    def __init__(self, checkout_id: uuid.UUID, setup_intent_id: str) -> None:
        self.checkout_id = checkout_id
        self.setup_intent_id = setup_intent_id
        message = (
            f"Checkout {checkout_id} from "
            f"setup intent {setup_intent_id} does not exist."
        )
        super().__init__(message)


class NotConfirmedCheckout(CheckoutError):
    def __init__(self, checkout: Checkout) -> None:
        self.checkout = checkout
        self.status = checkout.status
        message = f"Checkout {checkout.id} is not confirmed: {checkout.status}"
        super().__init__(message)


class SetupIntentNotSucceeded(CheckoutError):
    def __init__(self, checkout: Checkout, setup_intent_id: str) -> None:
        self.checkout = checkout
        self.setup_intent_id = setup_intent_id
        message = f"Setup intent {setup_intent_id} for {checkout.id} is not successful."
        super().__init__(message)


class NoCustomerOnSetupIntent(CheckoutError):
    def __init__(self, checkout: Checkout, setup_intent_id: str) -> None:
        self.checkout = checkout
        self.setup_intent_id = setup_intent_id
        message = (
            f"Setup intent {setup_intent_id} "
            f"for {checkout.id} has no customer associated."
        )
        super().__init__(message)


class NoPaymentMethodOnSetupIntent(CheckoutError):
    def __init__(self, checkout: Checkout, setup_intent_id: str) -> None:
        self.checkout = checkout
        self.setup_intent_id = setup_intent_id
        message = (
            f"Setup intent {setup_intent_id} "
            f"for {checkout.id} has no payment method associated."
        )
        super().__init__(message)


CHECKOUT_CLIENT_SECRET_PREFIX = "polar_c_"


class CheckoutService(ResourceServiceReader[Checkout]):
    async def list(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        *,
        organization_id: Sequence[uuid.UUID] | None = None,
        product_id: Sequence[uuid.UUID] | None = None,
        pagination: PaginationParams,
        sorting: list[Sorting[CheckoutSortProperty]] = [
            (CheckoutSortProperty.created_at, True)
        ],
    ) -> tuple[Sequence[Checkout], int]:
        statement = self._get_readable_checkout_statement(auth_subject)

        if organization_id is not None:
            statement = statement.where(Product.organization_id.in_(organization_id))

        if product_id is not None:
            statement = statement.where(Checkout.product_id.in_(product_id))

        order_by_clauses: list[UnaryExpression[Any]] = []
        for criterion, is_desc in sorting:
            clause_function = desc if is_desc else asc
            if criterion == CheckoutSortProperty.created_at:
                order_by_clauses.append(clause_function(Checkout.created_at))
            elif criterion == CheckoutSortProperty.expires_at:
                order_by_clauses.append(clause_function(Checkout.expires_at))
        statement = statement.order_by(*order_by_clauses)

        return await paginate(session, statement, pagination=pagination)

    async def get_by_id(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        id: uuid.UUID,
    ) -> Checkout | None:
        statement = self._get_readable_checkout_statement(auth_subject).where(
            Checkout.id == id
        )
        result = await session.execute(statement)
        return result.scalar_one_or_none()

    async def create(
        self,
        session: AsyncSession,
        checkout_create: CheckoutCreate,
        auth_subject: AuthSubject[User | Organization],
    ) -> Checkout:
        price = await product_price_service.get_writable_by_id(
            session, checkout_create.product_price_id, auth_subject
        )

        if price is None:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "product_price_id"),
                        "msg": "Price does not exist.",
                        "input": checkout_create.product_price_id,
                    }
                ]
            )

        if price.is_archived:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "product_price_id"),
                        "msg": "Price is archived.",
                        "input": checkout_create.product_price_id,
                    }
                ]
            )

        product = price.product
        if product.is_archived:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "product_price_id"),
                        "msg": "Product is archived.",
                        "input": checkout_create.product_price_id,
                    }
                ]
            )

        if (
            not isinstance(price, ProductPriceCustom)
            and checkout_create.amount is not None
        ):
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "amount"),
                        "msg": "Amount can only be set on custom prices.",
                        "input": checkout_create.amount,
                    }
                ]
            )

        product = cast(Product, await product_service.get_loaded(session, product.id))

        amount = checkout_create.amount
        currency = None
        if isinstance(price, ProductPriceFixed):
            amount = price.price_amount
            currency = price.price_currency
        elif isinstance(price, ProductPriceCustom):
            currency = price.price_currency

        checkout = Checkout(
            client_secret=generate_token(prefix=CHECKOUT_CLIENT_SECRET_PREFIX),
            amount=amount,
            currency=currency,
            user_metadata=checkout_create.metadata,
            product=product,
            product_price=price,
            customer_billing_address=checkout_create.customer_billing_address,
            **checkout_create.model_dump(
                exclude={
                    "product_price_id",
                    "amount",
                    "customer_billing_address",
                    "metadata",
                }
            ),
        )
        session.add(checkout)

        return checkout

    async def update(
        self,
        session: AsyncSession,
        checkout: Checkout,
        checkout_update: CheckoutUpdate | CheckoutUpdatePublic,
    ) -> Checkout:
        if checkout_update.product_price_id is not None:
            price = await product_price_service.get_by_id(
                session, checkout_update.product_price_id
            )
            if (
                price is None
                or price.product.organization_id != checkout.product.organization_id
            ):
                raise PolarRequestValidationError(
                    [
                        {
                            "type": "value_error",
                            "loc": ("body", "product_price_id"),
                            "msg": "Price does not exist.",
                            "input": checkout_update.product_price_id,
                        }
                    ]
                )

            if price.is_archived:
                raise PolarRequestValidationError(
                    [
                        {
                            "type": "value_error",
                            "loc": ("body", "product_price_id"),
                            "msg": "Price is archived.",
                            "input": checkout_update.product_price_id,
                        }
                    ]
                )

            if price.product_id != checkout.product_id:
                raise PolarRequestValidationError(
                    [
                        {
                            "type": "value_error",
                            "loc": ("body", "product_price_id"),
                            "msg": "Price does not belong to the product.",
                            "input": checkout_update.product_price_id,
                        }
                    ]
                )

            checkout.product_price = price
            if isinstance(price, ProductPriceFixed):
                checkout.amount = price.price_amount
                checkout.currency = price.price_currency
            elif isinstance(price, ProductPriceCustom):
                checkout.currency = price.price_currency
            elif isinstance(price, ProductPriceFree):
                checkout.amount = None
                checkout.currency = None

        if checkout_update.amount is not None:
            if not isinstance(checkout.product_price, ProductPriceCustom):
                raise PolarRequestValidationError(
                    [
                        {
                            "type": "value_error",
                            "loc": ("body", "amount"),
                            "msg": "Amount can only be set on custom prices.",
                            "input": checkout_update.amount,
                        }
                    ]
                )

            checkout.amount = checkout_update.amount

        if checkout_update.customer_billing_address:
            checkout.customer_billing_address = checkout_update.customer_billing_address

        if (
            isinstance(checkout_update, CheckoutUpdate)
            and checkout_update.metadata is not None
        ):
            checkout.user_metadata = checkout_update.metadata

        for attr, value in checkout_update.model_dump(
            exclude_unset=True,
            exclude={
                "product_price_id",
                "amount",
                "customer_billing_address",
                "metadata",
            },
        ).items():
            setattr(checkout, attr, value)

        session.add(checkout)
        return checkout

    async def confirm(
        self,
        session: AsyncSession,
        checkout: Checkout,
        checkout_confirm: CheckoutConfirm,
    ) -> Checkout:
        checkout = await self.update(session, checkout, checkout_confirm)

        errors: list[ValidationError] = []

        if checkout.amount is None and isinstance(
            checkout.product_price, ProductPriceCustom
        ):
            errors.append(
                {
                    "type": "missing",
                    "loc": ("body", "amount"),
                    "msg": "Amount is required for custom prices.",
                    "input": None,
                }
            )

        for required_field in [
            "customer_name",
            "customer_email",
            "customer_billing_address",
        ]:
            if getattr(checkout, required_field) is None:
                errors.append(
                    {
                        "type": "missing",
                        "loc": ("body", required_field),
                        "msg": "Field is required.",
                        "input": None,
                    }
                )

        if len(errors) > 0:
            raise PolarRequestValidationError(errors)

        assert checkout.customer_name is not None
        assert checkout.customer_email is not None
        assert checkout.customer_billing_address is not None

        if checkout.payment_processor == PaymentProcessor.stripe:
            stripe_customer = stripe_service.create_customer(
                name=checkout.customer_name,
                email=checkout.customer_email,
                address=checkout.customer_billing_address.to_stripe_dict(),
            )
            setup_intent = stripe_service.create_setup_intent(
                confirm=True,
                automatic_payment_methods={"enabled": True},
                confirmation_token=checkout_confirm.confirmation_token_id,
                customer=stripe_customer.id,
                metadata={"checkout_id": str(checkout.id)},
            )
            checkout.payment_processor_metadata = {
                "setup_intent_client_secret": setup_intent.client_secret,
                "setup_intent_status": setup_intent.status,
            }

        checkout.status = CheckoutStatus.confirmed
        session.add(checkout)
        return checkout

    async def handle_stripe_success(
        self,
        session: AsyncSession,
        checkout_id: uuid.UUID,
        setup_intent: stripe_lib.SetupIntent,
    ) -> Checkout:
        checkout = await self.get(session, checkout_id)

        if checkout is None:
            raise CheckoutDoesNotExist(checkout_id, setup_intent.id)

        if checkout.status != CheckoutStatus.confirmed:
            raise NotConfirmedCheckout(checkout)

        if setup_intent.status != "succeeded":
            raise SetupIntentNotSucceeded(checkout, setup_intent.id)

        if setup_intent.customer is None:
            raise NoCustomerOnSetupIntent(checkout, setup_intent.id)

        if setup_intent.payment_method is None:
            raise NoPaymentMethodOnSetupIntent(checkout, setup_intent.id)

        stripe_customer_id = get_expandable_id(setup_intent.customer)
        stripe_payment_method_id = get_expandable_id(setup_intent.payment_method)
        product_price = checkout.product_price
        metadata: dict[str, str] = {
            "type": ProductType.product,
            "product_id": str(checkout.product_id),
            "product_price_id": str(checkout.product_price_id),
        }
        idempotency_key = f"checkout_{checkout.id}"

        if product_price.is_recurring:
            stripe_service.create_subscription(
                customer=stripe_customer_id,
                currency=checkout.currency or "usd",
                default_payment_method=stripe_payment_method_id,
                price=product_price.stripe_price_id,
                metadata=metadata,
                idempotency_key=idempotency_key,
            )
        else:
            stripe_service.create_invoice(
                customer=stripe_customer_id,
                currency=checkout.currency or "usd",
                default_payment_method=stripe_payment_method_id,
                price=product_price.stripe_price_id,
                metadata=metadata,
                idempotency_key=idempotency_key,
            )

        checkout.status = CheckoutStatus.succeeded
        session.add(checkout)
        return checkout

    async def get_by_client_secret(
        self, session: AsyncSession, client_secret: str
    ) -> Checkout | None:
        statement = (
            select(Checkout)
            .where(
                Checkout.deleted_at.is_(None),
                Checkout.expires_at > utc_now(),
                Checkout.client_secret == client_secret,
            )
            .join(Checkout.product)
            .options(
                contains_eager(Checkout.product).options(
                    joinedload(Product.organization), joinedload(Product.product_medias)
                )
            )
        )
        result = await session.execute(statement)
        return result.unique().scalar_one_or_none()

    def _get_readable_checkout_statement(
        self, auth_subject: AuthSubject[User | Organization]
    ) -> Select[tuple[Checkout]]:
        statement = (
            select(Checkout)
            .where(Checkout.deleted_at.is_(None))
            .join(Checkout.product)
            .options(contains_eager(Checkout.product))
        )

        if is_user(auth_subject):
            user = auth_subject.subject
            statement = statement.where(
                Product.organization_id.in_(
                    select(UserOrganization.organization_id).where(
                        UserOrganization.user_id == user.id,
                        UserOrganization.deleted_at.is_(None),
                    )
                )
            )
        elif is_organization(auth_subject):
            statement = statement.where(
                Product.organization_id == auth_subject.subject.id,
            )

        return statement


checkout = CheckoutService(Checkout)
