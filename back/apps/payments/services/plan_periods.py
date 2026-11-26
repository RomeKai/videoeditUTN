"""
The paid period of a workspace's plan (Workspace.plan_renews_at) and the one
way its plan fields change (``switch_plan``).

A period only exists when it was paid through a checkout: the confirmation of
an upgrade payment starts one month from that moment (services/payments.py),
counted in the workspace's time zone (settings.WORKSPACE_TIME_ZONE) and
anchored to the day it started (Workspace.plan_anchor_day), so chained periods
come back to that day (31 Jan → 28 Feb → 31 Mar). A free plan, a plan set in
the admin and a plan reached by a downgrade have none.

Nothing charges a renewal yet (that is the real provider's, PAY-02): when
``plan_renews_at`` passes, the beat task (subscriptions.apply_due_plan_changes)
applies the scheduled downgrade, or else puts the workspace on the default
plan. Paying once never keeps a paid plan for good.
"""
from calendar import monthrange
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.conf import settings

# The fields switch_plan writes, for save(update_fields=...).
PLAN_FIELDS = ['subscription_plan', 'plan_renews_at', 'plan_anchor_day', 'pending_plan', 'pending_plan_effective_at']


def workspace_zone():
    return ZoneInfo(getattr(settings, 'WORKSPACE_TIME_ZONE', 'UTC'))


def add_month(moment, anchor_day=None):
    """
    The same local time one calendar month later, in the workspace's time
    zone. The day is ``anchor_day`` (the day the period started) or the
    moment's own, and the last day of the month when that one is shorter
    (31 Jan → 28/29 Feb). In UTC.
    """
    local = moment.astimezone(workspace_zone())
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    day = min(anchor_day or local.day, monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day).astimezone(dt_timezone.utc)


def is_paid(plan):
    return plan is not None and plan.price_usd_monthly > 0


def price_of(plan):
    """What a plan costs a month; no plan costs nothing."""
    return plan.price_usd_monthly if plan is not None else 0


def paid_period_end(workspace, now):
    """When the current paid period ends, or None when there is none (or it already ended)."""
    end = workspace.plan_renews_at
    if is_paid(workspace.subscription_plan) and end is not None and end > now:
        return end
    return None


def switch_plan(workspace, plan, *, paid_until=None, anchor_day=None):
    """Put the workspace on ``plan`` with a paid period until ``paid_until`` (or none). Drops any scheduled change. Does not save."""
    workspace.subscription_plan = plan
    workspace.plan_renews_at = paid_until
    workspace.plan_anchor_day = anchor_day if paid_until else None
    workspace.pending_plan = None
    workspace.pending_plan_effective_at = None


def start_paid_period(workspace, plan, now):
    """The plan of a confirmed upgrade payment: a paid month from ``now``, anchored to today's local day. Does not save."""
    anchor_day = now.astimezone(workspace_zone()).day
    switch_plan(workspace, plan, paid_until=add_month(now, anchor_day), anchor_day=anchor_day)
