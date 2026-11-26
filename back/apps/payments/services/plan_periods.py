"""
The paid period of a workspace's plan (Workspace.plan_renews_at) and the one
way its plan fields change (``switch_plan``).

A period only exists when it was paid through a checkout: the confirmation of
an upgrade payment starts one month from that moment (services/payments.py).
A free plan, a plan set in the admin and a plan reached by a downgrade have
none. Nothing charges a renewal yet (that is the real provider's, PAY-02): once
``plan_renews_at`` has passed, the workspace keeps its plan with no period, so
the subscription shows no renewal date and a downgrade applies at once.
"""
from calendar import monthrange

# The fields switch_plan writes, for save(update_fields=...).
PLAN_FIELDS = ['subscription_plan', 'plan_renews_at', 'pending_plan', 'pending_plan_effective_at']


def add_month(moment):
    """The same day and time one month later; the last day of the month when that day is missing (31 Jan → 28/29 Feb)."""
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return moment.replace(year=year, month=month, day=min(moment.day, monthrange(year, month)[1]))


def is_paid(plan):
    return plan is not None and plan.price_usd_monthly > 0


def paid_period_end(workspace, now):
    """When the current paid period ends, or None when there is none (or it already ended)."""
    end = workspace.plan_renews_at
    if is_paid(workspace.subscription_plan) and end is not None and end > now:
        return end
    return None


def switch_plan(workspace, plan, *, paid_until):
    """Put the workspace on ``plan`` with a paid period until ``paid_until`` (or none). Drops any scheduled change. Does not save."""
    workspace.subscription_plan = plan
    workspace.plan_renews_at = paid_until
    workspace.pending_plan = None
    workspace.pending_plan_effective_at = None
