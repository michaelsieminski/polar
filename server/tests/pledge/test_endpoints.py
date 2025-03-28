import uuid

import pytest
from httpx import AsyncClient

from polar.models.external_organization import ExternalOrganization
from polar.models.issue import Issue
from polar.models.organization import Organization
from polar.models.pledge import Pledge, PledgeState
from polar.models.repository import Repository
from polar.models.user import User
from polar.models.user_organization import UserOrganization
from polar.pledge.schemas import Pledge as PledgeSchema
from polar.postgres import AsyncSession
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import (
    create_issue,
    create_organization,
    create_pledge,
    create_user,
    create_user_github_oauth,
)


@pytest.mark.asyncio
@pytest.mark.auth
async def test_get_pledge(
    organization: Organization,
    external_organization_linked: ExternalOrganization,
    repository_linked: Repository,
    pledge_linked: Pledge,
    issue_linked: Issue,
    user_organization: UserOrganization,  # makes User a member of Organization
    session: AsyncSession,
    client: AsyncClient,
) -> None:
    # then
    session.expunge_all()

    response = await client.get(f"/v1/pledges/{pledge_linked.id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(pledge_linked.id)
    assert response.json()["type"] == "pay_upfront"
    assert response.json()["issue"]["id"] == str(issue_linked.id)
    assert response.json()["issue"]["repository"]["id"] == str(repository_linked.id)
    assert response.json()["issue"]["repository"]["organization"]["id"] == str(
        external_organization_linked.id
    )


@pytest.mark.asyncio
@pytest.mark.http_auto_expunge
@pytest.mark.auth
async def test_get_pledge_member_sending_org(
    external_organization_linked: ExternalOrganization,
    repository_linked: Repository,
    issue_linked: Issue,
    save_fixture: SaveFixture,
    client: AsyncClient,
    user: User,
) -> None:
    pledging_organization = await create_organization(save_fixture)

    pledge_created_by_user = await create_user(save_fixture)

    pledge = await create_pledge(
        save_fixture,
        external_organization_linked,
        repository_linked,
        issue_linked,
        pledging_organization=pledging_organization,
    )
    pledge.created_by_user_id = pledge_created_by_user.id
    await save_fixture(pledge)

    # make user member of the pledging organization
    user_organization = UserOrganization(
        user_id=user.id,
        organization_id=pledging_organization.id,
    )
    await save_fixture(user_organization)

    response = await client.get(f"/v1/pledges/{pledge.id}")

    assert response.status_code == 200

    assert response.json()["id"] == str(pledge.id)
    res: PledgeSchema = PledgeSchema.model_validate(response.json())
    assert res.id == pledge.id
    assert res.created_by is not None


@pytest.mark.asyncio
@pytest.mark.http_auto_expunge
@pytest.mark.auth
async def test_get_pledge_member_sending_org_user_has_github(
    external_organization_linked: ExternalOrganization,
    repository_linked: Repository,
    issue_linked: Issue,
    save_fixture: SaveFixture,
    client: AsyncClient,
    user: User,
) -> None:
    pledging_organization = await create_organization(save_fixture)

    pledge_created_by_user = await create_user(save_fixture)
    pledge_created_by_gh = await create_user_github_oauth(
        save_fixture, pledge_created_by_user
    )

    pledge = await create_pledge(
        save_fixture,
        external_organization_linked,
        repository_linked,
        issue_linked,
        pledging_organization=pledging_organization,
    )
    pledge.created_by_user_id = pledge_created_by_user.id
    await save_fixture(pledge)

    # make user member of the pledging organization
    user_organization = UserOrganization(
        user_id=user.id,
        organization_id=pledging_organization.id,
    )
    await save_fixture(user_organization)

    response = await client.get(f"/v1/pledges/{pledge.id}")

    assert response.status_code == 200

    assert response.json()["id"] == str(pledge.id)
    res: PledgeSchema = PledgeSchema.model_validate(response.json())
    assert res.id == pledge.id
    assert res.created_by is not None
    assert res.created_by.name == pledge_created_by_gh.account_username


@pytest.mark.asyncio
@pytest.mark.http_auto_expunge
@pytest.mark.auth
async def test_get_pledge_member_receiving_org(
    organization: Organization,
    external_organization_linked: ExternalOrganization,
    repository_linked: Repository,
    issue_linked: Issue,
    save_fixture: SaveFixture,
    client: AsyncClient,
    user: User,
) -> None:
    pledging_organization = await create_organization(save_fixture)

    pledge_created_by_user = await create_user(save_fixture)

    pledge = await create_pledge(
        save_fixture,
        external_organization_linked,
        repository_linked,
        issue_linked,
        pledging_organization=pledging_organization,
    )
    pledge.created_by_user_id = pledge_created_by_user.id
    await save_fixture(pledge)

    # make user member of the receiving organization
    user_organization = UserOrganization(
        user_id=user.id,
        organization_id=organization.id,
    )
    await save_fixture(user_organization)

    response = await client.get(f"/v1/pledges/{pledge.id}")

    assert response.status_code == 200

    assert response.json()["id"] == str(pledge.id)
    res: PledgeSchema = PledgeSchema.model_validate(response.json())
    assert res.id == pledge.id
    assert res.created_by is None  # created_by should not be available!


@pytest.mark.asyncio
@pytest.mark.http_auto_expunge
@pytest.mark.auth
async def test_get_pledge_not_member(
    organization: Organization,
    repository: Repository,
    pledge: Pledge,
    session: AsyncSession,
    client: AsyncClient,
) -> None:
    response = await client.get(f"/v1/pledges/{pledge.id}")

    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.http_auto_expunge
@pytest.mark.auth
async def test_search_pledge(
    organization: Organization,
    user_organization: UserOrganization,  # makes User a member of Organization
    external_organization_linked: ExternalOrganization,
    repository_linked: Repository,
    pledge_linked: Pledge,
    issue_linked: Issue,
    client: AsyncClient,
) -> None:
    response = await client.get(
        "/v1/pledges/search", params={"organization_id": str(organization.id)}
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == str(pledge_linked.id)
    assert response.json()["items"][0]["issue"]["id"] == str(issue_linked.id)
    assert response.json()["items"][0]["issue"]["repository"]["id"] == str(
        repository_linked.id
    )
    assert response.json()["items"][0]["issue"]["repository"]["organization"][
        "id"
    ] == str(external_organization_linked.id)


@pytest.mark.asyncio
@pytest.mark.http_auto_expunge
@pytest.mark.auth
async def test_search_pledge_no_member(
    organization: Organization,
    external_organization_linked: ExternalOrganization,
    repository_linked: Repository,
    pledge_linked: Pledge,
    issue_linked: Issue,
    client: AsyncClient,
) -> None:
    response = await client.get(
        "/v1/pledges/search", params={"organization_id": str(organization.id)}
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == 0


@pytest.mark.asyncio
@pytest.mark.auth
async def test_search_pledge_by_issue_id(
    organization: Organization,
    external_organization_linked: ExternalOrganization,
    pledging_organization: Organization,
    repository_linked: Repository,
    user_organization: UserOrganization,  # makes User a member of Organization
    pledge_linked: Pledge,
    save_fixture: SaveFixture,
    session: AsyncSession,
    issue_linked: Issue,
    client: AsyncClient,
) -> None:
    # create another issue and another pledge
    other_issue = await create_issue(
        save_fixture,
        external_organization=external_organization_linked,
        repository=repository_linked,
    )

    other_pledge = Pledge(
        id=uuid.uuid4(),
        by_organization_id=pledging_organization.id,
        issue_id=other_issue.id,
        repository_id=repository_linked.id,
        organization_id=external_organization_linked.id,
        amount=50000,
        currency="usd",
        fee=50,
        state=PledgeState.created,
    )
    await save_fixture(other_pledge)

    other_pledge_2 = Pledge(
        id=uuid.uuid4(),
        by_organization_id=pledging_organization.id,
        issue_id=other_issue.id,
        repository_id=repository_linked.id,
        organization_id=external_organization_linked.id,
        amount=50000,
        currency="usd",
        fee=50,
        state=PledgeState.created,
    )
    await save_fixture(other_pledge_2)

    # then
    session.expunge_all()

    response = await client.get(f"/v1/pledges/search?issue_id={pledge_linked.issue_id}")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["id"] == str(pledge_linked.id)
    assert response.json()["items"][0]["issue"]["id"] == str(issue_linked.id)
    assert response.json()["items"][0]["issue"]["repository"]["id"] == str(
        repository_linked.id
    )
    assert response.json()["items"][0]["issue"]["repository"]["organization"][
        "id"
    ] == str(external_organization_linked.id)

    response = await client.get(f"/v1/pledges/search?issue_id={other_issue.id}")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    assert response.json()["items"][0]["id"] == str(other_pledge.id)
    assert response.json()["items"][0]["issue"]["id"] == str(other_issue.id)
    assert response.json()["items"][1]["id"] == str(other_pledge_2.id)
    assert response.json()["items"][1]["issue"]["id"] == str(other_issue.id)


@pytest.mark.asyncio
@pytest.mark.http_auto_expunge
@pytest.mark.auth
async def test_search_no_params(
    organization: Organization,
    repository: Repository,
    pledge: Pledge,
    client: AsyncClient,
) -> None:
    response = await client.get("/v1/pledges/search")

    assert response.status_code == 400
    assert response.json() == {"detail": "No search criteria specified"}


@pytest.mark.asyncio
@pytest.mark.http_auto_expunge
@pytest.mark.auth
async def test_create_pay_on_completion(
    organization: Organization,
    repository: Repository,
    issue: Issue,
    client: AsyncClient,
) -> None:
    create_pledge = await client.post(
        "/v1/pledges/pay_on_completion",
        json={"issue_id": str(issue.id), "amount": 133700},
    )

    assert create_pledge.status_code == 200

    pledge = create_pledge.json()
    assert pledge["state"] == "created"
    assert pledge["type"] == "pay_on_completion"

    # pledge_id = pledge["id"]
    # create_invoice = await client.post(
    #     f"/v1/pledges/{pledge_id}/create_invoice"
    # )
    # assert create_invoice.status_code == 200
    # pledge = create_invoice.json()
    # assert pledge["type"] == "pay_on_completion"
    # assert len(pledge["hosted_invoice_url"]) > 5
    # assert response.json() == {"detail": "No search criteria specified"}
    # assert False


@pytest.mark.asyncio
async def test_summary(
    repository: Repository,
    pledge: Pledge,
    pledging_organization: Organization,
    client: AsyncClient,
    session: AsyncSession,
    save_fixture: SaveFixture,
) -> None:
    repository.is_private = False
    await save_fixture(repository)

    expected_github_username = pledging_organization.slug
    expected_name = pledging_organization.slug

    # then
    session.expunge_all()

    response = await client.get(
        f"/v1/pledges/summary?issue_id={pledge.issue_id}",
    )

    assert response.status_code == 200
    assert response.json() == {
        "funding": {
            "funding_goal": None,
            "pledges_sum": {"amount": pledge.amount, "currency": "USD"},
        },
        "pledges": [
            {
                "pledger": {
                    "avatar_url": "https://avatars.githubusercontent.com/u/105373340?s=200&v=4",
                    "github_username": expected_github_username,
                    "name": expected_name,
                },
                "type": "pay_upfront",
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.http_auto_expunge
async def test_summary_private_repo(
    repository: Repository,
    pledge: Pledge,
    client: AsyncClient,
    save_fixture: SaveFixture,
) -> None:
    repository.is_private = True
    await save_fixture(repository)

    response = await client.get(
        f"/v1/pledges/summary?issue_id={pledge.issue_id}",
    )

    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.auth
async def test_create_pay_on_completion_total_monthly_spending_limit(
    organization: Organization,
    repository: Repository,
    issue: Issue,
    client: AsyncClient,
    session: AsyncSession,
    save_fixture: SaveFixture,
) -> None:
    # configure org spending limit
    organization.billing_email = "foo@polar.sh"
    organization.total_monthly_spending_limit = 10000
    await save_fixture(organization)

    # then
    session.expunge_all()

    # first pledge is OK
    create_pledge = await client.post(
        "/v1/pledges/pay_on_completion",
        json={
            "issue_id": str(issue.id),
            "amount": 6000,
            "by_organization_id": str(organization.id),
        },
    )

    assert create_pledge.status_code == 200

    pledge = create_pledge.json()
    assert pledge["state"] == "created"

    # not OK, reached spending limit
    create_pledge = await client.post(
        "/v1/pledges/pay_on_completion",
        json={
            "issue_id": str(issue.id),
            "amount": 6000,
            "by_organization_id": str(organization.id),
        },
    )

    assert create_pledge.status_code == 400

    assert (
        create_pledge.text
        == '{"error":"BadRequest","detail":"The team spending limit has been reached"}'
    )


@pytest.mark.asyncio
@pytest.mark.auth
async def test_create_pay_on_completion_per_user_monthly_spending_limit(
    organization: Organization,
    repository: Repository,
    issue: Issue,
    client: AsyncClient,
    session: AsyncSession,
    save_fixture: SaveFixture,
) -> None:
    # configure org spending limit
    organization.billing_email = "foo@polar.sh"
    organization.per_user_monthly_spending_limit = 10000
    await save_fixture(organization)

    # then
    session.expunge_all()

    # first pledge is OK
    create_pledge = await client.post(
        "/v1/pledges/pay_on_completion",
        json={
            "issue_id": str(issue.id),
            "amount": 6000,
            "by_organization_id": str(organization.id),
        },
    )

    assert create_pledge.status_code == 200

    pledge = create_pledge.json()
    assert pledge["state"] == "created"

    # not OK, reached spending limit
    create_pledge = await client.post(
        "/v1/pledges/pay_on_completion",
        json={
            "issue_id": str(issue.id),
            "amount": 6000,
            "by_organization_id": str(organization.id),
        },
    )

    assert create_pledge.status_code == 400

    assert (
        create_pledge.text
        == '{"error":"BadRequest","detail":"The user spending limit has been reached"}'
    )


@pytest.mark.asyncio
@pytest.mark.http_auto_expunge
@pytest.mark.auth
async def test_no_billing_email(
    organization: Organization,
    repository: Repository,
    issue: Issue,
    client: AsyncClient,
    save_fixture: SaveFixture,
) -> None:
    # configure org spending limit
    organization.per_user_monthly_spending_limit = 10000
    await save_fixture(organization)

    # not OK, reached spending limit
    create_pledge = await client.post(
        "/v1/pledges/pay_on_completion",
        json={
            "issue_id": str(issue.id),
            "amount": 6000,
            "by_organization_id": str(organization.id),
        },
    )

    assert create_pledge.status_code == 400

    assert (
        create_pledge.text
        == '{"error":"BadRequest","detail":"The team has no configured billing email"}'
    )


@pytest.mark.asyncio
@pytest.mark.auth
async def test_spending(
    organization: Organization,
    repository: Repository,
    issue: Issue,
    client: AsyncClient,
    session: AsyncSession,
    save_fixture: SaveFixture,
) -> None:
    # configure org spending limit
    organization.billing_email = "foo@polar.sh"
    organization.per_user_monthly_spending_limit = 10000
    await save_fixture(organization)

    # then
    session.expunge_all()

    # make a pledge
    create_pledge = await client.post(
        "/v1/pledges/pay_on_completion",
        json={
            "issue_id": str(issue.id),
            "amount": 6000,
            "by_organization_id": str(organization.id),
        },
    )

    assert create_pledge.status_code == 200

    pledge = create_pledge.json()
    assert pledge["state"] == "created"

    # get spending

    spending = await client.get(
        f"/v1/pledges/spending?organization_id={organization.id}"
    )

    assert spending.status_code == 200
    assert spending.json()["amount"] == 6000


@pytest.mark.asyncio
@pytest.mark.http_auto_expunge
@pytest.mark.auth
async def test_spending_zero(organization: Organization, client: AsyncClient) -> None:
    # get spending

    spending = await client.get(
        f"/v1/pledges/spending?organization_id={organization.id}"
    )

    assert spending.status_code == 200
    assert spending.json()["amount"] == 0
