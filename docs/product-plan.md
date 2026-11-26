# Verity Development Plan

## Product Overview

Verity is an AI-powered SaaS customer support platform that integrates a knowledge base, intelligent chatbot, and ticketing system into a unified solution.

## 1. User Registration, Authentication, and Onboarding

- Allow users to register using their full name, email address, and password.
- Require email verification through a secure, time-limited verification link sent to the registered email address.
- Allow users to resend the verification email if necessary.
- Prevent users from accessing the customer dashboard until their email address has been verified.
- Provide secure login, logout, password reset, and password recovery functionality.
- Automatically create a dedicated workspace upon successful registration and email verification, assigning the registered user the Workspace Owner role.
- Redirect verified users to the customer dashboard, where they can view available subscription plans, compare features and resource limits, and purchase a subscription.
- Allow users without an active subscription to access their account settings, subscription plans, and billing section while restricting subscription-dependent features.
- Automatically activate subscription features and resource allowances upon successful payment confirmation.
- Allow users to manage their profile, change their password, view subscription details, and access billing history.
- Display the current subscription status, renewal date, and available upgrade options within the customer dashboard.
- Implement secure password hashing, session management, and rate limiting to protect against unauthorized access and brute-force attacks.

## 2. Knowledge Base (RAG)

- Support document uploads in multiple formats (PDF, DOCX, TXT, CSV, and XLSX), with a default limit of 100 documents.
- Crawl websites and their subpages, with a default limit of 10 domains and support for periodic synchronization.
- Manage knowledge sources, monitor processing status, and update or reprocess content.
- Integrate with external APIs and enable AI to determine when to call them (Tool Calling), such as checking product availability or order status, with proper authentication and access control.

## 3. Chatbot

- Provide an embeddable chatbot through a JavaScript snippet, with a default limit of one authorized domain per workspace.
- Customize appearance, logo, colors, language, tone, welcome message, and behavioral instructions.
- Generate responses using the knowledge base and configured external APIs.
- View conversation history and escalate unresolved customer requests to human support.
- Provide a dynamic form builder with customizable fields, AI-based activation conditions, and ticket category mapping.
- Automatically prefill form fields using conversation context, subject to customer confirmation.

### Human Handoff and Chatbot Forms

The chatbot has two ways to bring a person into a conversation. They solve different problems and are designed to work together.

| | Live handoff (Phase 2) | Chatbot form → ticket (Phase 3) |
|---|---|---|
| What it is | A team member takes over the live conversation from the AI. | A structured request, filled in inside the chat, that becomes a ticket. |
| Trigger | The customer asks for a person, or the AI can't find an answer or is unavailable (for example, the token allowance is used up). | A form's AI activation condition matches the conversation (for example, a return request), or the customer chooses a form. |
| Best for | Someone on the team is online; quick back-and-forth; urgent or unclear issues. | Nobody is online; work that needs follow-up over hours or days; requests that need specific data (order number, email) or routing to a category. |
| AI behaviour | The AI stops replying until an agent resolves the conversation. | The AI prefills the form from the conversation; the customer confirms before submitting. |
| Result | One conversation thread with agent replies (Open → Escalated → Resolved). | A ticket in the mapped category, with the form data and the conversation history. |

- **How they work together:** when a customer escalates and no one replies, the chatbot offers the matching form so the request isn't lost. From Phase 3, an agent can also turn an escalated conversation into a ticket.
- **Who can reply:** anyone whose role allows replying to conversations (by default Workspace Owner, Workspace Administrator and Support Agent), not only administrators.
- **Prepared in Phase 2 so forms need no rework:** messages have a type (text, form or event), the widget renders messages as typed blocks, escalation runs through a single service that Phase 3 extends to create tickets, and the customer's name and email are stored on the conversation.

## 4. Ticketing System

- Create and manage custom ticket categories.
- Create tickets through incoming emails, chatbot forms, or manual submissions.
- Display submitted form data and relevant conversation history within tickets.
- Support ticket assignment, priority management, internal notes, search, and filtering.
- Manage ticket statuses: New, Open, Pending, Resolved, and Closed.
- Allow tickets to be reopened when customers respond, based on workspace settings.

## 5. Email Integration

- Connect personal or organizational email accounts, with a default limit of 3 accounts per workspace.
- Detect spam and promotional emails using machine learning and move them to a reviewable spam section.
- Automatically classify legitimate incoming emails and create tickets under appropriate categories.
- Allow configurable automatic initial responses using predefined templates or an LLM, including the ticket ID.
- Send confirmation emails to customers who submit chatbot forms.
- Send support agent responses within the original email thread.
- Append incoming customer replies to existing tickets using Message-ID, In-Reply-To, References, and ticket identifiers.
- Prevent duplicate message processing and automatic reply loops.

## 6. Administration and Security

- Allow users to register using their full name, email address, and password.
- Require email verification before granting access to the customer dashboard.
- Support secure login, logout, password recovery, and password reset.
- Automatically create a workspace upon successful registration and email verification, assigning the user the Workspace Owner role.
- Allow verified users to access the customer dashboard, compare subscription plans, and purchase a subscription.
- Restrict subscription-dependent features until a subscription is activated.
- Allow users to manage their profile, password, language, timezone, and notification preferences.
- Support optional two-factor authentication (2FA).
- Allow workspace owners and authorized administrators to invite members and manage their roles.
- Allow users to belong to multiple workspaces and switch between them.
- Implement multi-tenant architecture with strict data isolation between workspaces.
- Implement Role-Based Access Control (RBAC) with the following default roles:
  - **System Administrator:** Full platform administration, subscription plans, global limits, and system monitoring.
  - **Workspace Owner:** Full workspace management, subscriptions, billing, members, and settings.
  - **Workspace Administrator:** Manage workspace resources, members, knowledge sources, chatbots, tickets, and integrations, excluding billing and ownership.
  - **Support Agent:** Manage tickets, respond to customers, add internal notes, and view relevant conversation history.
  - **Viewer:** Read-only access to authorized workspace resources.
- Support granular permissions and workspace-specific role assignments.
- Enforce access control and subscription limits independently at the backend level.
- Allow system administrators to configure all default limits, including AI usage, storage, documents, domains, crawled pages, and workspace members.
- Allow system administrators to suspend or reactivate users and workspaces.
- Implement secure password hashing, session management, rate limiting, and protection against unauthorized access.
- Protect customer information, API credentials, and integration secrets through encryption and secure storage.
- Maintain audit logs and configurable data retention policies to support GDPR compliance where applicable.
- Provide administrative dashboards for resource usage, support performance, user management, and subscription management.

## 7. Plans, Subscriptions, and Token Management

- Allow system administrators to create and modify subscription plans, including:
  - Plan name and description
  - Price and currency
  - Billing cycle
  - AI token allowance
  - Document and domain limits
  - Connected email account limits
  - Workspace member limits
  - Storage and other resource limits

- Apply token allowances at the workspace level, shared across all members and chatbots.
- Track actual input and output token consumption for all LLM-powered operations, including chatbot responses, classification, summarization, and automatic replies.
- Track embedding usage separately.
- Allow administrators to configure consumption multipliers for different AI models and operation types.
- Calculate weighted token consumption to account for differences in model costs, with transparent calculations for customers.
- Provide a customer usage dashboard displaying:
  - Actual token consumption
  - Weighted token consumption
  - Remaining token allowance
  - Usage percentage
  - Subscription renewal date
  - Daily usage breakdown
  - Usage breakdown by feature and model

- Validate available allowance before every AI request and atomically reserve an estimated token budget.
- Reconcile reserved tokens with actual consumption after request completion to prevent uncontrolled quota overruns caused by concurrent requests.
- Send usage notifications when consumption reaches 80% and 90% of the available allowance.
- Disable AI-powered operations when the token allowance is exhausted, while keeping manual ticketing, email ingestion, and independent forms operational.
- Display a configurable fallback message in the chatbot when AI usage is unavailable.
- Allow customers to upgrade their subscription or purchase additional token packages.
- Reset recurring token allowances at the beginning of each billing cycle. Unused recurring tokens do not roll over by default.
- Provide administrators with workspace usage reports, actual AI provider costs, and subscription information.
- Allow administrators to manually adjust token allowances with audit logging.
- Apply subscription price and plan changes to existing subscriptions at their next renewal by default.

## 8. Development Roadmap

| Phase 1 | User registration, email verification, authentication, password recovery, customer dashboard, workspace management, RBAC, subscription plans, and quota management infrastructure |
| Phase 2 | Knowledge base, RAG, document processing, website crawling, chatbot, live human handoff, and token tracking |
| Phase 3 | Dynamic form builder, chatbot forms that create tickets, ticket categories, and ticket management |
| Phase 4 | Email integration, spam detection, automatic responses, and email threading |
| Phase 5 | External API integrations, intelligent routing, advanced analytics, payment processing, subscription purchases, automatic subscription activation, and subscription upgrades |
