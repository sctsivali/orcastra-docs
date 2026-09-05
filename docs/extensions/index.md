# Extensions

Build an add-on against the Orcastra API, install it into an organization, and receive signed events when things change.

---

An add-on is an external application. It runs on your own infrastructure, authenticates with a credential issued when an operator installs it, and receives webhooks for the events it subscribed to. No add-on code runs inside Orcastra, and none runs inside the dashboard's browser origin.

<div class="grid cards" markdown>

-   :material-puzzle:{ .lg .middle } **What An Add-on Is**

    ---

    The four channels an add-on uses, and the boundary it never crosses

    [:octicons-arrow-right-24: Overview](overview.md)

-   :material-rocket-launch:{ .lg .middle } **Build Your First Add-on**

    ---

    From nothing to a working add-on receiving verified webhooks

    [:octicons-arrow-right-24: Tutorial](build-your-first-add-on.md)

-   :material-download:{ .lg .middle } **Installing An Add-on**

    ---

    What the consent screen asks you to approve, and what uninstalling removes

    [:octicons-arrow-right-24: Installing](installing.md)

-   :material-key-chain:{ .lg .middle } **Capabilities**

    ---

    The ten capabilities, which are marked, and which reach inside a guest

    [:octicons-arrow-right-24: Capabilities](capabilities.md)

-   :material-server-network:{ .lg .middle } **Cluster And Project Scope**

    ---

    How reach is intersected, and why it is recomputed rather than stored

    [:octicons-arrow-right-24: Scope](scope.md)

-   :material-cog:{ .lg .middle } **Settings**

    ---

    Declare configuration fields and let the dashboard render the form

    [:octicons-arrow-right-24: Settings](settings.md)

-   :material-webhook:{ .lg .middle } **Webhooks**

    ---

    The event catalogue, the signature scheme, and what a receiver must do

    [:octicons-arrow-right-24: Webhooks](webhooks.md)

-   :material-speedometer:{ .lg .middle } **Rate Limits**

    ---

    What is counted, what a 429 means, and which origin to call

    [:octicons-arrow-right-24: Rate Limits](rate-limits.md)

-   :material-alert-circle:{ .lg .middle } **Error Responses**

    ---

    Every status this API returns, its cause, and the correct client action

    [:octicons-arrow-right-24: Errors](errors.md)

-   :material-tag-multiple:{ .lg .middle } **Versioning And Deprecation**

    ---

    What is covered by the compatibility promise and what is not

    [:octicons-arrow-right-24: Versioning](versioning.md)

-   :material-shield-lock:{ .lg .middle } **Security Model**

    ---

    The trust boundary, stated as a boundary, including its known limits

    [:octicons-arrow-right-24: Security](security.md)

-   :material-api:{ .lg .middle } **API Reference**

    ---

    Every endpoint an add-on may call, with the capability each one costs

    [:octicons-arrow-right-24: API Reference](api-reference.md)

</div>
