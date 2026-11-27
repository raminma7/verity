"""Permission catalogue and the default workspace roles.

Permissions are checked on the backend for every request; the web app only uses them to hide controls.
"""

PERMISSIONS: dict[str, str] = {
    "workspace.read": "View the workspace",
    "workspace.update": "Rename the workspace and change its settings",
    "workspace.delete": "Delete the workspace",
    "members.read": "See members and pending invitations",
    "members.manage": "Invite and remove members, change roles",
    "billing.read": "See the subscription, invoices and billing history",
    "billing.manage": "Buy, change or cancel the subscription",
    "usage.read": "See AI usage and limits",
    "analytics.read": "See support analytics: ticket volume, response times, SLA, workload, chatbot and AI reports",
    "knowledge.read": "Browse knowledge sources",
    "knowledge.manage": "Add, sync and remove knowledge sources",
    "chatbots.read": "See chatbots and conversations",
    "chatbots.manage": "Configure chatbots and forms",
    "conversations.reply": "Reply to escalated chatbot conversations",
    "tickets.read": "See tickets",
    "tickets.manage": "Reply to, assign and update tickets",
    "tickets.configure": "Manage ticket categories, teams and routing",
    "integrations.manage": "Connect email accounts and external APIs",
    "email.review": "Review spam and promotional email, mark it not spam or delete it",
    "audit.read": "See the workspace audit log",
}

ALL = list(PERMISSIONS)

DEFAULT_ROLES: list[dict] = [
    {
        "key": "owner",
        "name": "Workspace Owner",
        "rank": 100,
        "description": "Full workspace management, subscriptions, billing, members and settings.",
        "permissions": ALL,
    },
    {
        "key": "admin",
        "name": "Workspace Administrator",
        "rank": 80,
        "description": "Manages resources, members, knowledge, chatbots, tickets and integrations. No billing or ownership.",
        "permissions": [p for p in ALL if not p.startswith("billing.") and p != "workspace.delete"],
    },
    {
        "key": "agent",
        "name": "Support Agent",
        "rank": 40,
        "description": "Works tickets, replies to customers, adds internal notes and reads conversation history.",
        "permissions": [
            "workspace.read",
            "members.read",
            "knowledge.read",
            "chatbots.read",
            "conversations.reply",
            "tickets.read",
            "tickets.manage",
            "email.review",
        ],
    },
    {
        "key": "viewer",
        "name": "Viewer",
        "rank": 10,
        "description": "Read-only access to the workspace.",
        "permissions": ["workspace.read", "members.read", "knowledge.read", "chatbots.read", "tickets.read", "usage.read"],
    },
]
