"""
The wallet's movements as the web app shows them (Uso › Movimientos, G1 and G2).

- ``kind``: what a Transaction did to the balance, from its type and status
  (``KINDS``). A row with no kind (a DEPOSIT or REFUND that FAILED, a SPEND
  still PENDING) never touched the balance and is not a movement.
- Months and days are those of ``settings.WORKSPACE_TIME_ZONE``.
- Consumption is what was charged (SPEND + COMPLETED), on the day it was
  charged (``settled_at``; ``created_at`` on rows settled before it existed).

Amounts stay ``Decimal`` and go out as plain decimal text, never a float.
"""
import csv
import io
import operator
import re
from datetime import datetime, timedelta
from functools import reduce
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Case, CharField, Count, Q, Sum, Value, When
from django.db.models.functions import Coalesce, TruncDate

from ..models import Transaction

Type, Status, Category = Transaction.Type, Transaction.Status, Transaction.Category

# (type, status) -> kind. The order is the one of the filter chips.
KINDS = {
    (Type.DEPOSIT, Status.COMPLETED): 'purchase',
    (Type.SPEND, Status.RESERVED): 'reserved',
    (Type.SPEND, Status.COMPLETED): 'charged',
    (Type.REFUND, Status.COMPLETED): 'refund',
    # A reservation that was returned whole (rollback_reservation): "Devuelto".
    (Type.SPEND, Status.FAILED): 'released',
    (Type.DEPOSIT, Status.PENDING): 'pending',
    (Type.REFUND, Status.PENDING): 'pending',
}
KIND_CHOICES = list(dict.fromkeys(KINDS.values()))
# What the CSV says of each kind (front/src/features/uso/movements.tsx, KIND_META).
KIND_LABELS = {
    'purchase': 'Compra', 'reserved': 'Reservado', 'charged': 'Cobrado',
    'refund': 'Devolución', 'released': 'Devuelto', 'pending': 'Pendiente',
}
# What the kind does to the balance: reserved and charged take, the rest gives (or will).
OUTGOING = frozenset({'reserved', 'charged'})

ZERO = Decimal('0E-10')
CHARGED_AT = Coalesce('settled_at', 'created_at')


def time_zone():
    return ZoneInfo(settings.WORKSPACE_TIME_ZONE)


def kind_of(txn):
    return KINDS.get((txn.transaction_type, txn.status))


def kind_filter(kind=None):
    """Q of the rows of ``kind`` (one of KIND_CHOICES); without one, of every row that is a movement."""
    return reduce(operator.or_, (
        Q(transaction_type=transaction_type, status=status)
        for (transaction_type, status), value in KINDS.items() if kind is None or value == kind
    ))


def kind_annotation():
    return Case(
        *(When(transaction_type=t, status=s, then=Value(kind)) for (t, s), kind in KINDS.items()),
        output_field=CharField(),
    )


def money(value):
    """A Decimal as JSON text: exact, never in exponent notation."""
    return format(Decimal(value), 'f')


def csv_amount(value):
    """Two decimals when that is exact, else every decimal it has: ``-555.30``, ``0.1234567``."""
    value = Decimal(value)
    cents = value.quantize(Decimal('0.01'))
    return format(cents if cents == value else value.normalize(), 'f')


def parse_month(raw):
    """``(year, month)`` of a ``YYYY-MM``, or None when it is not one."""
    match = re.fullmatch(r'([0-9]{4})-([0-9]{2})', raw or '')
    if match is None:
        return None
    year, month = int(match[1]), int(match[2])
    # 9999-12 has no next month to end it.
    if not 1 <= month <= 12 or not 1 <= year <= 9998:
        return None
    return year, month


def current_month(now):
    local = now.astimezone(time_zone())
    return local.year, local.month


def month_bounds(year, month):
    """First instant of the month and of the next one, in the workspace's zone."""
    zone = time_zone()
    start = datetime(year, month, 1, tzinfo=zone)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=zone)
    return start, end


def movements(workspace, *, kind=None, month=None, project=None):
    """The workspace's movements with the filters, newest first. ``month``: ``(year, month)``."""
    rows = Transaction.objects.filter(kind_filter(kind), wallet__workspace_id=workspace.pk)
    if month is not None:
        start, end = month_bounds(*month)
        rows = rows.filter(created_at__gte=start, created_at__lt=end)
    if project is not None:
        rows = rows.filter(project_id=project)
    return rows.order_by('-created_at', '-id')


def with_details(rows):
    """What the serializer reads of the creator and the project, in the same query."""
    return rows.select_related('created_by', 'project').only(
        'id', 'wallet_id', 'created_by_id', 'amount', 'transaction_type', 'status', 'description',
        'created_at', 'category', 'project_id', 'project__title',
        'created_by__first_name', 'created_by__last_name', 'created_by__username',
    )


def totals(rows):
    """``{kind: {count, amount}}`` of ``rows``, only the kinds that have movements."""
    grouped = (
        rows.order_by().annotate(kind=kind_annotation()).values('kind')
        .annotate(count=Count('id'), amount=Sum('amount'))
    )
    return {row['kind']: {'count': row['count'], 'amount': money(row['amount'])} for row in grouped if row['kind']}


def charged(workspace):
    """What was charged to the workspace, with ``charged_at`` (when)."""
    return Transaction.objects.filter(
        wallet__workspace_id=workspace.pk, transaction_type=Type.SPEND, status=Status.COMPLETED,
    ).annotate(charged_at=CHARGED_AT)


def spent_in_month(workspace, year, month):
    start, end = month_bounds(year, month)
    total = charged(workspace).filter(charged_at__gte=start, charged_at__lt=end).aggregate(total=Sum('amount'))['total']
    return total if total is not None else ZERO


def usage(workspace, year, month):
    """Every day of the month: what was charged, split in projects, renders and SEO."""
    start, end = month_bounds(year, month)
    per_day = (
        charged(workspace).filter(charged_at__gte=start, charged_at__lt=end)
        .annotate(day=TruncDate('charged_at', tzinfo=time_zone()))
        .order_by().values('day', 'category').annotate(total=Sum('amount'))
    )
    columns = {Category.RENDER: 'renders', Category.SEO: 'seo'}
    days = {}
    for row in per_day:
        entry = days.setdefault(row['day'], {'projects': ZERO, 'renders': ZERO, 'seo': ZERO})
        column = columns.get(row['category'], 'projects')
        entry[column] += row['total']
    result = []
    day = start.date()
    while day < end.date():
        entry = days.get(day, {'projects': ZERO, 'renders': ZERO, 'seo': ZERO})
        result.append({'date': day.isoformat(), **{key: money(value) for key, value in entry.items()}})
        day += timedelta(days=1)
    return result


# A cell a spreadsheet would run as a formula (CSV injection).
_FORMULA_START = ('=', '+', '-', '@', '\t', '\r')


def _text_cell(value):
    return f"'{value}" if value.startswith(_FORMULA_START) else value


CSV_HEADER = ['fecha', 'concepto', 'proyecto', 'tipo', 'monto']


def csv_text(rows):
    """
    The movements as CSV: the date in the workspace's zone, the description,
    the project, the kind as the screen names it and the amount with the sign
    of what it did to the balance (negative what was reserved or charged).
    """
    zone = time_zone()
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(CSV_HEADER)
    for txn in rows.iterator(chunk_size=1000):
        kind = kind_of(txn)
        amount = -txn.amount if kind in OUTGOING else txn.amount
        writer.writerow([
            txn.created_at.astimezone(zone).isoformat(timespec='seconds'),
            _text_cell(txn.description),
            _text_cell(txn.project.title) if txn.project_id else '',
            KIND_LABELS[kind],
            csv_amount(amount),
        ])
    return out.getvalue()
