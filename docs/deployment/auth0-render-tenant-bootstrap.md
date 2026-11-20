# Auth0 + Render non-production tenant bootstrap runbook

**Status:** Operational procedure for a controlled non-production smoke test. This is not a production deployment approval.

## 1. Current integration contract

The FastAPI service reads these OIDC settings from its environment:

| Variable | Non-production value shape |
|---|---|
| `AGENTBOX_OIDC_ISSUER` | Exact Auth0 issuer URL, including its trailing slash |
| `AGENTBOX_OIDC_AUDIENCE` | API identifier configured in Auth0 |
| `AGENTBOX_OIDC_JWKS_URI` | Auth0 tenant JWKS URL |
| `AGENTBOX_OIDC_TENANT_CLAIM` | Exact namespaced access-token claim populated by a trusted Credentials Exchange Action |

The API validates the signed access token and derives the tenant from the signed claim. It does not trust a tenant ID supplied in a request header or body.

For the current non-production AgentBox integration, the Auth0 API is configured with the RFC 9068 profile and RS256. The Auth0 Credentials Exchange Action must be attached to the correct flow and restricted to the intended M2M client and API audience. Never put a client secret or access token in this repository, an issue, a PR, or application logs.

## 2. Verify authentication before provisioning

1. Confirm the Render deployment is running the intended Git commit.
2. Confirm `/health` returns HTTP 200.
3. Open `/docs` and confirm the Swagger Authorize control is present.
4. Obtain a fresh access token from the Auth0 non-production M2M client. Do not paste the token into GitHub or a chat.
5. Confirm the token is an access token for the AgentBox audience, signed with the expected algorithm, and contains the exact issuer and configured tenant claim.
6. Use Swagger Authorize and call the protected endpoint `GET /v1/agents/agentbox-smoke-test/versions`.

Interpret the result carefully:

- **401:** token authentication failed; inspect issuer, audience, JWKS, signature, time claims, and access-token type.
- **403 / `tenant_not_provisioned`:** the signed token was accepted far enough to reach the tenant lifecycle gate, but the tenant does not exist in the service database.
- **403 / tenant inactive:** the tenant exists but is not active.
- **403 / authorization denied:** tenant is active but no exact matching allow policy exists (or a deny policy overrides it).
- **200:** the authentication, tenant and authorization gates succeeded for this endpoint.

Do not interpret an anonymous 401 as proof of successful authentication.

## 3. Provision the tenant using the existing offline CLI

AgentBox already provides the `agentbox-tenant` command. It operates on a local SQLite database file and intentionally exposes no public tenant-provisioning HTTP endpoint.

The command refuses to create a missing database or operate on an unrelated/empty SQLite file. The database must first have been initialized by AgentBox.

From the backend environment, against the exact database file used by the API:

```bash
agentbox-tenant --database "$AGENTBOX_DATABASE_PATH" create \
  --tenant-id agentbox-test \
  --display-name "AgentBox Non-production Test"

agentbox-tenant --database "$AGENTBOX_DATABASE_PATH" active \
  --tenant-id agentbox-test

agentbox-tenant --database "$AGENTBOX_DATABASE_PATH" audit \
  --tenant-id agentbox-test
```

The tenant is created in `pending` state and must be explicitly activated. The CLI attributes the operation to the local OS operator identity and writes tenant lifecycle audit events.

**Important:** the CLI must access the same durable database file as the running API. Running it on a developer laptop with a different SQLite file does not provision the Render service. Do not attempt this procedure against the current free Render deployment if the database is on ephemeral storage or no supported secure shell/volume access is available.

## 4. Configure an exact, least-privilege authorization grant

The API loads policy artifacts at startup from `AGENTBOX_AUTHORIZATION_POLICIES_PATH`. If that variable is absent, the policy set is empty and all capability actions are denied. The policy file must be a JSON array and each item must validate against `schemas/authorization-policy-v1.schema.json`.

For the smoke-test endpoint only, a non-production policy has this shape:

```json
[
  {
    "schema_version": "1.0.0",
    "policy_id": "agentbox-smoke-test-read",
    "version": "1.0.0",
    "active": true,
    "effect": "allow",
    "capability_id": "agent.registry",
    "principal_scope": "user",
    "principal_ids": ["<EXACT_AUTH0_ACCESS_TOKEN_SUBJECT>"],
    "tenant_ids": ["agentbox-test"],
    "actions": ["agent.read"],
    "resource_ids": ["agentbox-smoke-test"],
    "created_at": "2026-11-20T00:00:00Z"
  }
]
```

Replace the subject placeholder with the exact verified `sub` claim from the non-production access token. Do not use a wildcard subject, tenant, action, or resource. Do not use an ID token subject as a substitute. This grant authorizes only reading versions for the one named agent; it does not authorize agent creation, execution, administration, or other resources.

Store the policy file in a controlled deployment artifact or mounted configuration location, set `AGENTBOX_AUTHORIZATION_POLICIES_PATH` to its absolute path, and restart/redeploy the API so it loads the validated policy. Protect the file from unreviewed edits. Changes to the policy file do not automatically hot-reload.

## 5. Retest and capture evidence

After the tenant is active and the policy is loaded:

1. Obtain a fresh Auth0 access token.
2. Authorize Swagger with the access token.
3. Call `GET /v1/agents/agentbox-smoke-test/versions`.
4. Confirm the expected HTTP status and response.
5. Verify an authorization audit event was recorded for the decision.
6. Repeat with a different tenant claim or an ungranted principal and confirm denial.
7. Remove the temporary grant and confirm access is denied again.

Do not record raw bearer tokens in test evidence. Record timestamps, deployment commit, endpoint, status code, sanitized subject reference, tenant ID, policy ID/version, and audit event reference.

## 6. Production gates

The current Render free SQLite deployment is suitable only for a disposable smoke test. SQLite data on ephemeral storage can be lost on restart/redeploy and is not a production multi-instance persistence strategy.

Before production:

- Move tenant, authorization audit, agent registry and related stores to a supported durable database architecture.
- Define backup, restore, point-in-time recovery, retention and restore-drill procedures.
- Ensure the API and administrative tooling share the same durable database and migration lifecycle.
- Establish a controlled policy deployment/change-review process and policy rollback.
- Use a trusted tenant membership/provisioning workflow; do not allow end users to self-assign tenant claims.
- Verify issuer/JWKS rotation, access-token expiry, revocation/deactivation behavior and cross-tenant denial.
- Run full browser/API end-to-end tests against the deployed Auth0 tenant.
- Enable and require the repository CI workflow; pass backend tests, frontend checks and Flutter checks before merging security changes.

A successful `/health` response proves only that the process is responding. It does not prove authentication, tenant provisioning, authorization, persistence durability, or production readiness.
